"""站点作用域查询的业务逻辑层。

职责：解析作用域三元组 (site, view, date)——默认站点、站点校验、日期默认/校验，
再调 crud 组装 ProTable 响应并回显作用域。事务边界在此，crud 不提交。

**执行模型**：``get_scope`` 与 ``search`` 是异步的（面向 200 个运营的读路径，
用 ``AsyncSession`` + asyncpg）；``resolve_export_scope`` 与 ``iter_export_csv``
保持同步——导出走服务端游标，且流式生成器需自建会话（响应体在依赖清理之后才被消费）。
两条路径共用 ``analysis_crud`` 的同一批语句构造函数，SQL 只有一处定义。
"""
from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterator
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.common.errors import (
    CODE_ANALYSIS_EXPORT_TOO_LARGE,
    CODE_ANALYSIS_SCOPE_INVALID,
    PayloadTooLargeError,
    UnprocessableError,
)
from app.site_registry import UnknownSiteError, normalize_site_code
from app.table.analysis import analysis_crud as crud
from app.table.search.search_schemas import (
    AnalysisSearchRequest,
    AnalysisSearchResponse,
    BrandOption,
    CategoryOption,
    DayRowItem,
    ScopeInfo,
    WeekRowItem,
)
from config import settings
from database import SessionFactory

logger = logging.getLogger(__name__)


class ScopeError(UnprocessableError):
    """作用域参数非法（站点未登记、日期无数据/越界）→ 422。

    继承 `APIError` 后由全局处理器直接产出统一错误体，API 层不再需要 try/except 转译。
    """

    code = CODE_ANALYSIS_SCOPE_INVALID
    message = "查询作用域不合法"


class ExportTooLargeError(PayloadTooLargeError):
    """导出结果超出行数上限 → 413。"""

    code = CODE_ANALYSIS_EXPORT_TOO_LARGE
    message = "导出结果超出上限"


# 导出列：与列表屏保持一致的中文表头。两视图都有趋势附加信息，表头明确回溯单位。
#
# 三个商品槽位各输出一组同构列，表头标明名次——界面上能看到前三、导出里没有会被当成缺陷。
# 列数翻倍不改变导出的流式特性：行数不变、yield_per 分块不变，驻留内存仍与块大小同阶。


def _product_slot_columns(slot: int) -> list[tuple[str, str]]:
    """某个商品槽位的导出列。三个槽位结构相同，只有序号与表头前缀不同。

    **不含品牌与类目**：报表里的「点击量第 N 的品牌 / 分类」是关键词级的独立排行，不是
    「第 N 个商品的品牌 / 类目」。导入侧把它们按商品存进了 dim_product，而同一 ASIN 在
    报表中携带互相冲突的取值（实测品牌 28.5%、类目 27.3%，作为对照，标题的冲突率是 0%），
    落库时任选其一胜出。导出这样的值等于把已知错误写进用户会拿去分析的文件。
    修复要动数据模型，另开变更。
    """
    prefix = f"top{slot}"
    label = f"Top{slot}"
    return [
        (f"{prefix}_asin", f"{label} ASIN"),
        (f"{prefix}_title", f"{label} 商品标题"),
        (f"{prefix}_click_share", f"{label} 点击份额"),
        (f"{prefix}_conversion_share", f"{label} 转化份额"),
    ]


_EXPORT_COLUMNS_BASE: list[tuple[str, str]] = [
    ("keyword", "关键词"),
    ("current_ranking", "当期排名"),
    ("previous_ranking", "上期排名"),
    ("ranking_change", "排名变化"),
    ("is_new", "是否新词"),
    *_product_slot_columns(1),
    *_product_slot_columns(2),
    *_product_slot_columns(3),
    # 关键词级的前三品牌与前三类目，按名次各三列——与表格展示同构。
    # 改造前只有 top1 的单列，且那个值是伪造的（品牌#1 配商品#1），按名次导出才有意义。
    ("top1_brand", "品牌 Top1"),
    ("top2_brand", "品牌 Top2"),
    ("top3_brand", "品牌 Top3"),
    ("top1_category", "类目 Top1"),
    ("top2_category", "类目 Top2"),
    ("top3_category", "类目 Top3"),
]
# 趋势列的表头按视图分。界面上两个视图同名（「搜索趋势」，作用域栏就在同屏上方），
# 但 CSV 脱离了界面——「近 7 日趋势」这个表头下装着 7 期周报就是直接写错。
_TREND_COLUMN_LABEL: dict[str, str] = {
    "DAY": "近 7 日趋势",
    "WEEK": "近 7 周趋势",
}


def _export_columns(view: str) -> list[tuple[str, str]]:
    return [*_EXPORT_COLUMNS_BASE, ("trend", _TREND_COLUMN_LABEL[view])]


def _resolve_site(site: str | None) -> str:
    """未传取默认站点；一律校验合法，非法即报错，绝不静默回退到默认。"""
    if site is None:
        return settings.default_site
    try:
        return normalize_site_code(site)
    except UnknownSiteError as exc:
        raise ScopeError(str(exc)) from exc


def _resolve_date(
    given: date | None, available: list[date]
) -> date | None:
    """日期解析：未传取最新可用；传了必须在可选集内，否则报错不回退。"""
    if given is None:
        return available[0] if available else None
    if given not in available:
        raise ScopeError(f"日期 {given} 无数据或已移出保留窗口")
    return given


async def _available_dates(db: AsyncSession, site: str, view: str) -> list[date]:
    return list((await db.execute(crud.available_dates_stmt(site, view))).scalars().all())


async def _fetch_trends(
    db: AsyncSession, site: str, keyword_ids: list[int], on: date, view: str
) -> tuple[dict[int, list[int]], dict[int, list[date]]]:
    if not keyword_ids:
        return {}, {}
    rows = (await db.execute(crud.trends_stmt(site, keyword_ids, on, view))).all()
    return crud.shape_trends(rows), crud.shape_trend_dates(rows)


async def get_scope(db: AsyncSession, site: str | None, view: str) -> ScopeInfo:
    """作用域元信息：可选日期集 + 最新日期（日期选择器用）。"""
    resolved_site = _resolve_site(site)
    dates = await _available_dates(db, resolved_site, view)
    return ScopeInfo(
        site=resolved_site,
        view=view,  # type: ignore[arg-type]
        latest_date=dates[0] if dates else None,
        available_dates=dates,
    )


async def search(db: AsyncSession, req: AnalysisSearchRequest) -> AnalysisSearchResponse:
    """作用域内分页查询，返回 ProTable 契约 + 生效作用域回显。"""
    site = _resolve_site(req.site)
    view = req.view
    dates = await _available_dates(db, site, view)
    on = _resolve_date(req.date, dates)

    # 该站点该视图无任何数据：正常返回空结果（非报错）
    if on is None:
        return AnalysisSearchResponse(success=True, data=[], total=0, site=site, view=view, date=None)

    rows = crud.shape_rows((await db.execute(crud.search_stmt(site, view, on, req))).all())

    if crud.request_has_filters(req):
        total = (await db.execute(crud.count_filtered_stmt(site, view, on, req))).scalar_one()
    else:
        total = (await db.execute(crud.scope_row_count_stmt(site, view, on))).scalar_one_or_none() or 0

    # 两个视图都附带只读的搜索趋势：先分页拿本页 keyword_id，再批量补附加，避免在整切片上
    # 做相关子查询。回溯窗口按视图取（日 7 天 / 周 7 期），差异全在 crud 的 _TREND_SPEC 里。
    # 附加信息不改变结果集构成。
    row_type = DayRowItem if view == "DAY" else WeekRowItem
    ids = [r["keyword_id"] for r in rows]
    trends, trend_dates = await _fetch_trends(db, site, ids, on, view)
    data = []
    for r in rows:
        item = row_type(**r)
        item.trend = trends.get(r["keyword_id"], [])
        item.trend_dates = trend_dates.get(r["keyword_id"], [])
        data.append(item.model_dump())

    return AnalysisSearchResponse(
        success=True, data=data, total=total, site=site, view=view, date=on
    )


def resolve_export_scope(db: Session, req: AnalysisSearchRequest) -> tuple[str, str, date | None]:
    """解析导出作用域并在开始流式输出前把上限检查做掉。

    上限必须在响应头发出之前检查：一旦开始流，就只能给出 200 + 一个被截断的 CSV，
    而截断的 CSV 会被当成完整数据使用。宁可 413 让用户加筛选。
    """
    site = _resolve_site(req.site)
    dates = crud.available_dates(db, site, req.view)
    on = _resolve_date(req.date, dates)
    if on is None:
        return site, req.view, None

    cap = settings.EXPORT_ROW_CAP
    if crud.request_has_filters(req):
        count = crud.count_capped(db, site, req.view, on, req, cap + 1)
    else:
        count = crud.scope_row_count(db, site, req.view, on)
    if count > cap:
        raise ExportTooLargeError(
            f"导出结果超过 {cap} 行上限，请补充筛选条件后再导出"
        )
    return site, req.view, on


def iter_export_csv(
    site: str, view: str, on: date | None, req: AnalysisSearchRequest
) -> Iterator[str]:
    """产出 CSV 文本块（首块带 UTF-8 BOM）。

    自建会话而非复用请求会话：流式响应体是在依赖注入清理之后才被消费的，
    沿用请求会话会踩到"会话已关闭"。会话在生成器结束时（含客户端断开导致的
    GeneratorExit）于 finally 中关闭。

    无数据的作用域产出只有表头的 CSV——空表格比一个报错更符合"导出当前视图"的语义。
    """
    columns = _export_columns(view)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")

    def _flush() -> str:
        chunk = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        return chunk

    writer.writerow([label for _, label in columns])
    yield "﻿" + _flush()

    if on is None:
        return

    db = SessionFactory()
    try:
        for rows in crud.iter_export_rows(db, site, view, on, req, settings.EXPORT_ROW_CAP):
            ids = [r["keyword_id"] for r in rows]
            trends = crud.fetch_trends(db, site, ids, on, view)
            for r in rows:
                row = dict(r)
                kid = r["keyword_id"]
                row["trend"] = " ".join(str(v) for v in trends.get(kid, []))
                writer.writerow([_render_cell(row.get(key)) for key, _ in columns])
            yield _flush()
    except GeneratorExit:
        # 客户端主动断开（关页面、取消下载），不是错误
        raise
    except Exception:
        # 开流之后无法再改状态码——响应头已发出，此时抛异常只会得到一份内容不完整的
        # 200 CSV。只能记日志并断流；request_id 已由日志过滤器带上，供事后追查。
        logger.exception(
            "导出流中断（响应头已发出，客户端将收到不完整文件）site=%s view=%s date=%s",
            site, view, on,
        )
        raise
    finally:
        db.close()


def _render_cell(value) -> str:
    """单元格取值：布尔转中文，None 留空（不补零——空与 0 在排名变化上含义不同）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def export_filename(site: str, view: str, on: date | None) -> str:
    """导出文件名带作用域三元组，便于归档时一眼认出是哪个切片。"""
    return f"{site}_{view}_{on or 'empty'}.csv"


async def get_categories(db: AsyncSession, site: str | None, view: str) -> list[CategoryOption]:
    """类目下拉选项（按作用域）。"""
    resolved_site = _resolve_site(site)
    rows = (await db.execute(crud.category_options_stmt(resolved_site, view))).all()
    return [CategoryOption(**opt) for opt in crud.shape_category_options(rows)]


async def get_brands(db: AsyncSession, site: str | None, view: str) -> list[BrandOption]:
    """品牌下拉选项（按作用域）。与类目同构。"""
    resolved_site = _resolve_site(site)
    rows = (await db.execute(crud.brand_options_stmt(resolved_site, view))).all()
    return [BrandOption(**opt) for opt in crud.shape_brand_options(rows)]
