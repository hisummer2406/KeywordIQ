"""站点作用域查询的业务逻辑层。

职责：解析作用域三元组 (site, view, date)——默认站点、站点校验、日期默认/校验，
再调 crud 组装 ProTable 响应并回显作用域。事务边界在此，crud 不提交。

**执行模型**：``get_scope`` 与 ``search`` 是异步的（面向 200 个运营的读路径，
用 ``AsyncSession`` + asyncpg）；``resolve_export_scope`` 与 ``iter_export_csv``
保持同步——导出走服务端游标，且流式生成器需自建会话（响应体在依赖清理之后才被消费）。
两条路径共用 ``analysis_crud`` 的同一批语句构造函数，SQL 只有一处定义。
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
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
from app.table.analysis.row_segments import (
    INCLUDE_ALL,
    INCLUDE_DROPPED,
    INCLUDE_LISTED,
    plan_segments,
    remaining_for_dropped,
    wants_dropped,
    wants_listed,
)
from app.table.search.search_schemas import (
    AnalysisSearchRequest,
    AnalysisSearchResponse,
    BrandOption,
    CategoryOption,
    DayRowItem,
    FirstListedSummary,
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
    「第 N 个商品的品牌 / 类目」，所以它们不属于任何一个商品槽位。它们作为独立的六列
    出现在 ``_EXPORT_COLUMNS_BASE`` 里，名次由列位置承载——与表格展示同构。
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
    ("first_listed", "首次入榜"),
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


async def _timed_execute(db: AsyncSession, stmt, db_ms: list[int] | None):
    """包裹 ``await db.execute(...)``，可选地把这次等待耗时（含连接池 checkout）
    追加进 ``db_ms``——慢请求诊断只信这个累加值是"数据库耗时"，不再信"阶段耗时求和"
    （后者会把阶段内纯 Python 的结果整形也算作数据库时间，见 ``search()`` 的说明与
    Codex 复审 P2）。``db_ms`` 为 ``None`` 时（未触发诊断的调用方，如 ``get_scope``）
    不计时，避免无意义的 `time.monotonic()` 调用。

    **``finally`` 而不是执行完再记**：语句超时/锁等待超时/连接池 checkout 超时会让
    ``await db.execute(...)`` 直接抛出，之前的实现在这种情况下完全不记录这次等待
    耗时——恰恰是最该被诊断出来的一类失败（Codex 复审 PR #98 P2）。不吞异常，只是
    在传播前把已经等待的时间计进去。
    """
    if db_ms is None:
        return await db.execute(stmt)
    t0 = time.monotonic()
    try:
        return await db.execute(stmt)
    finally:
        db_ms.append(int((time.monotonic() - t0) * 1000))


async def _available_dates(
    db: AsyncSession, site: str, view: str, db_ms: list[int] | None = None
) -> list[date]:
    result = await _timed_execute(db, crud.available_dates_stmt(site, view), db_ms)
    return list(result.scalars().all())


async def _fetch_trends(
    db: AsyncSession, site: str, keyword_ids: list[int], on: date, view: str,
    db_ms: list[int] | None = None,
) -> tuple[dict[int, list[int]], dict[int, list[date]], dict[int, list[int | None]]]:
    """一次回溯查询同时喂趋势与历史排名。

    回溯范围取「趋势窗口」与「最远参照点」的大者（见 ``crud.fetch_lookback_days``），
    因此历史排名不需要第二条查询——那是设计上刻意否掉的方案：多一次往返、多一段要维护
    的 SQL，换来的只是趋势与历史排名解耦，而这两者本来就该对齐。
    """
    if not keyword_ids:
        return {}, {}, {}
    rows = (await _timed_execute(db, crud.trends_stmt(site, keyword_ids, on, view), db_ms)).all()
    return (
        crud.shape_trends(rows, on=on, view=view),
        crud.shape_trend_dates(rows, on=on, view=view),
        crud.shape_history(rows, on, view),
    )


async def _listed_total(
    db: AsyncSession, site: str, view: str, on: date,
    req: AnalysisSearchRequest, has_filters: bool, user_id: int,
    db_ms: list[int] | None = None,
) -> int:
    """在榜段的行数——**同时也是分段分页的分界线**，所以任何时候都要算。

    这一点与 ``include_total`` 无关：那个开关管的是"要不要把总数报给前端"，而分界线
    是内部分页正确性的前提。**无筛选时也必须实算**：``site_data_stats.row_count`` 是整
    切片的预存值，不扣黑名单类目（``HIDDEN_CATEGORY_NAMES``），拿它当分界线会比实际可
    翻的行数多，末页出现空页。``has_filters`` 仍管掉榜段是否显示，与这里无关。
    """
    result = await _timed_execute(db, crud.count_filtered_stmt(site, view, on, req, user_id), db_ms)
    return result.scalar_one()


async def _dropped_total(
    db: AsyncSession, site: str, view: str, on: date, db_ms: list[int] | None = None
) -> int:
    result = await _timed_execute(db, crud.scope_dropped_count_stmt(site, view, on), db_ms)
    return result.scalar_one_or_none() or 0


def _combined_total(
    listed_total: int, dropped_total: int, req: AnalysisSearchRequest, has_filters: bool,
) -> int | None:
    """报给前端的总数。

    ``include_total=False`` 只在带筛选时才省得下东西（精确计数是这条路径上最贵的一步）；
    无筛选时两个数都是预存值，省不下什么，故照常返回——少一处"这次到底算没算"的歧义。
    """
    if not has_filters:
        combined = listed_total + dropped_total
        return combined if req.include in (INCLUDE_ALL,) else (
            listed_total if req.include == INCLUDE_LISTED else dropped_total
        )
    if not req.include_total:
        return None
    # 带筛选时掉榜段恒为空，故总数就是筛选后的在榜行数；仅掉榜时结果必然为空。
    return 0 if req.include == INCLUDE_DROPPED else listed_total


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


def _search_filter_summary(req: AnalysisSearchRequest) -> dict:
    """慢请求诊断用的筛选白名单摘要——只出布尔/计数字段，MUST NOT 含检索词或 ASIN
    原文，与「校验错误不回显请求内容」是同一条纪律在诊断日志上的延伸。"""
    return {
        "has_keyword_search": bool(req.keyword or req.product_title),
        "has_asin_filter": bool(req.asin),
        "categories_count": len(req.categories) if req.categories else 0,
        "brands_count": len(req.brands) if req.brands else 0,
        "first_listed": req.first_listed,
        "competitor_new": req.competitor_new,
        "exclude_brand_in_keyword": req.exclude_brand_in_keyword,
        "include": req.include,
    }


async def search(db: AsyncSession, req: AnalysisSearchRequest, user_id: int) -> AnalysisSearchResponse:
    """作用域内分页查询，返回 ProTable 契约 + 生效作用域回显。

    ``user_id`` 取自当前登录用户（API 层的鉴权依赖），只喂给「竞对新发现」筛选判定关注
    关系，MUST NOT 从请求体读——与本仓库既有鉴权模式一致（词库/竞对监控同一条纪律）。

    **慢请求诊断**（design.md 决策 D 第二层）：围绕既有阶段用 ``time.monotonic()`` 打点，
    总耗时超过 ``settings.ANALYSIS_SLOW_QUERY_THRESHOLD_MS`` 才在结尾输出一条 WARNING，
    不新增查询次数、不在阈值以下时输出。计时点是既有 ``await db.execute(...)`` 调用的
    包裹，不是新增的探测。

    ``db_ms`` 单独累加每一次 ``await db.execute(...)`` 的等待时间（经 ``_timed_execute``），
    与 ``stage_ms`` 分开：后者按既有阶段划分、每个阶段内部混杂着数据库往返与纯 Python
    的结果整形（``shape_rows``/构造响应模型等），直接把 ``stage_ms`` 求和当成"数据库
    耗时"会让整形慢的请求也显得像是数据库慢（Codex 复审 PR #98 P2：`sum(stage_ms.values())`
    恒等于 `total_ms`，`app_ms` 恒等于 0，诊断从不指向应用侧）。``stage_ms`` 仍然保留在
    日志里，用来进一步定位是哪个阶段慢，只是不再充当 db/app 二分的依据。

    **失败路径同样诊断**：语句超时/锁等待超时/连接池 checkout 超时会让某次
    ``await db.execute(...)`` 直接抛出——``_timed_execute`` 已经在 ``finally`` 里
    把这次等待耗时计进 ``db_ms``，这里再用一层 ``try/except`` 包住主体：超阈值时
    照常输出同一条结构化诊断（标 `request_failed=True`），再原样重新抛出，不吞异常、
    不改变最终返回给调用方的行为。此前失败请求既不会走到这里的诊断，通用 SQL 级
    监听（``database.py``）也只在 ``after_cursor_execute`` 记录、对失败的执行同样
    失明——恰是最该被诊断出来的一类（连接卡死、锁等待，见 issue #4）
    （Codex 复审 PR #98 P2）。

    **取消路径单独处理**：`asyncio.CancelledError` 继承自 `BaseException`，
    `except Exception` 捕不到它——Uvicorn 优雅关闭超时/应用层超时都是通过取消
    这个请求的 Task 实现的。不单独捕获的话，`request_logging.py` 只会记一条通用的
    `outcome=cancelled` 摘要，没有这里的 site/view/filters/stage_ms/db_total_ms
    明细——而取消往往发生在请求已经很慢的时候，明细恰恰最有价值（Codex 复审
    PR #98 第三轮 P2）。同样标记后原样重新抛出，不吞。
    """
    request_started = time.monotonic()
    stage_ms: dict[str, int] = {}
    db_ms: list[int] = []
    checkpoint = request_started
    site = _resolve_site(req.site)
    view = req.view
    on: date | None = None

    def _stage(name: str) -> None:
        nonlocal checkpoint
        now = time.monotonic()
        stage_ms[name] = int((now - checkpoint) * 1000)
        checkpoint = now

    def _log_slow_diagnostic(total_ms: int, *, failed: bool = False, cancelled: bool = False) -> None:
        db_total_ms = sum(db_ms)
        logger.warning(
            "分析查询慢请求诊断 request_failed=%s request_cancelled=%s site=%s view=%s on=%s "
            "page=%d perPage=%d orderBy=%s orderDir=%s filters=%s stage_ms=%s "
            "db_total_ms(仅 await db.execute 等待时间,含连接获取)=%d "
            "app_ms(其余应用处理:结果整形/序列化等)=%d total_ms=%d",
            failed, cancelled, site, view, on, req.page, req.perPage, req.orderBy, req.orderDir,
            _search_filter_summary(req), stage_ms,
            db_total_ms, max(total_ms - db_total_ms, 0), total_ms,
        )

    try:
        dates = await _available_dates(db, site, view, db_ms=db_ms)
        on = _resolve_date(req.date, dates)
        _stage("scope_ms")

        # 该站点该视图无任何数据：正常返回空结果（非报错）。耗时通常可忽略，但
        # `_available_dates` 本身也可能因连接池排队/锁等待而慢——不能因为结果集是空的
        # 就假定这次调用一定快，否则恰恰是"作用域解析变慢"这种情形永远不出现在诊断里
        # （Codex 复审 PR #98 P2）。
        if on is None:
            total_ms = int((time.monotonic() - request_started) * 1000)
            if total_ms > settings.ANALYSIS_SLOW_QUERY_THRESHOLD_MS:
                _log_slow_diagnostic(total_ms)
            return AnalysisSearchResponse(success=True, data=[], total=0, site=site, view=view, date=None)

        # 筛选一律以锚定期那一行为准，掉榜行在锚定期没有行可供匹配——所以**任何筛选一
        # 生效，掉榜段就为空**。这不是优化，是「只看选定期」这条口径的必然结果，界面侧
        # 须明示。
        has_filters = crud.request_has_filters(req)

        listed_total = await _listed_total(db, site, view, on, req, has_filters, user_id, db_ms=db_ms)
        dropped_total = 0 if has_filters else await _dropped_total(db, site, view, on, db_ms=db_ms)
        _stage("count_ms")

        # 两段各自有序且永不交错（掉榜恒在末尾），故按边界切分、各发一条查询，而不是
        # UNION ALL 之后整体排序——后者会强制物化两段全部行，而在榜段就有四十多万行。
        plan = plan_segments(
            offset=(req.page - 1) * req.perPage, limit=req.perPage,
            listed_total=listed_total, include=req.include,
        )

        rows: list[dict] = []
        if plan.listed is not None:
            offset, limit = plan.listed
            rows += crud.shape_rows(
                (
                    await _timed_execute(
                        db,
                        crud.search_stmt(site, view, on, req, offset=offset, limit=limit, user_id=user_id),
                        db_ms,
                    )
                ).all()
            )
        if plan.dropped is not None and not has_filters:
            offset, limit = plan.dropped
            # 只补「整页还差的那几条」。在榜段实际返回多少由查询说了算，不由预存计数
            # 决定——计数偏小时多出来的在榜行照常返回，这里少补几条即可，不会有行凭空
            # 消失。
            take = remaining_for_dropped(limit, len(rows)) if plan.listed is not None else limit
            if take > 0:
                rows += crud.shape_dropped_rows(
                    (await _timed_execute(db, crud.dropped_stmt(site, view, on, offset, take), db_ms)).all()
                )
        _stage("rows_ms")

        total = _combined_total(listed_total, dropped_total, req, has_filters)

        # 两个视图都附带只读的搜索趋势与历史排名：先分页拿本页 keyword_id，再批量补
        # 附加，避免在整切片上做相关子查询。两者取自**同一次**回溯查询。附加信息不
        # 改变结果集构成。掉榜行同样补——它那一行除了历史排名与趋势之外几乎是空的。
        row_type = DayRowItem if view == "DAY" else WeekRowItem
        ids = [r["keyword_id"] for r in rows]
        trends, trend_dates, history = await _fetch_trends(db, site, ids, on, view, db_ms=db_ms)
        _stage("trend_ms")
        data = []
        for r in rows:
            item = row_type(**r)
            item.trend = trends.get(r["keyword_id"], [])
            item.trend_dates = trend_dates.get(r["keyword_id"], [])
            item.history = history.get(r["keyword_id"], [None, None, None])
            data.append(item.model_dump())

        # 该切片有没有重建过品牌/类目。一次 LIMIT 1 探测，相对本页的多次查询可忽略。
        # 未重建时前端要明确提示——静默返回空是最难排查的形态。
        has_brand = (
            await _timed_execute(db, crud.slice_has_brand_data_stmt(site, view, on), db_ms)
        ).first() is not None
        _stage("brand_probe_ms")

        total_ms = int((time.monotonic() - request_started) * 1000)
        if total_ms > settings.ANALYSIS_SLOW_QUERY_THRESHOLD_MS:
            _log_slow_diagnostic(total_ms)

        return AnalysisSearchResponse(
            success=True, data=data, total=total, site=site, view=view, date=on,
            pending_rebuild=not has_brand,
        )
    except asyncio.CancelledError:
        # asyncio.CancelledError 继承自 BaseException 而不是 Exception（3.8+），
        # 下面的 except Exception 捕不到它——Uvicorn 优雅关闭超时/应用层超时都是
        # 通过取消这个请求的 Task 实现的。`_timed_execute` 的 finally 已经把取消前
        # 已等待的耗时计进 db_ms，但如果这里不单独捕获，那些数据永远不会被写进日志：
        # request_logging.py 那一层只有一条通用的 outcome=cancelled 摘要，没有这里的
        # site/view/filters/stage_ms/db_total_ms 明细（Codex 复审 PR #98 第三轮 P2）。
        total_ms = int((time.monotonic() - request_started) * 1000)
        if total_ms > settings.ANALYSIS_SLOW_QUERY_THRESHOLD_MS:
            _log_slow_diagnostic(total_ms, failed=True, cancelled=True)
        raise
    except Exception:
        total_ms = int((time.monotonic() - request_started) * 1000)
        if total_ms > settings.ANALYSIS_SLOW_QUERY_THRESHOLD_MS:
            _log_slow_diagnostic(total_ms, failed=True)
        raise


def resolve_export_scope(
    db: Session, req: AnalysisSearchRequest, user_id: int
) -> tuple[str, str, date | None]:
    """解析导出作用域并在开始流式输出前把上限检查做掉。

    上限必须在响应头发出之前检查：一旦开始流，就只能给出 200 + 一个被截断的 CSV，
    而截断的 CSV 会被当成完整数据使用。宁可 413 让用户加筛选。``user_id`` 同 ``search``。
    """
    site = _resolve_site(req.site)
    dates = crud.available_dates(db, site, req.view)
    on = _resolve_date(req.date, dates)
    if on is None:
        return site, req.view, None

    cap = settings.EXPORT_ROW_CAP
    has_filters = crud.request_has_filters(req)

    # 上限按**两段之和**判定。只数在榜段的话，合计超限的导出会照常开流，而流一旦开始
    # 就只能给出 200 加一份被截断的 CSV——截断的 CSV 会被当成完整数据使用。
    listed = 0
    if wants_listed(req.include):
        # 无筛选也实算：预存的 row_count 不扣黑名单类目，会高估（见 ``_listed_total``）。
        listed = crud.count_capped(db, site, req.view, on, req, cap + 1, user_id)
    # 带筛选时掉榜段恒为空：筛选一律以锚定期那一行为准，而掉榜行在锚定期没有行可匹配。
    dropped = (
        crud.scope_dropped_count(db, site, req.view, on)
        if wants_dropped(req.include) and not has_filters else 0
    )

    if listed + dropped > cap:
        raise ExportTooLargeError(
            f"导出结果超过 {cap} 行上限，请补充筛选条件后再导出"
        )
    return site, req.view, on


def iter_export_csv(
    site: str, view: str, on: date | None, req: AnalysisSearchRequest, user_id: int
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
        def _emit(rows):
            """一块行 → CSV 文本。两段共用，保证列与走势的处理方式完全一致。"""
            ids = [r["keyword_id"] for r in rows]
            trends = crud.fetch_trends(db, site, ids, on, view)
            for r in rows:
                row = dict(r)
                kid = r["keyword_id"]
                row["trend"] = " ".join(str(v) for v in trends.get(kid, []))
                writer.writerow([_render_cell(row.get(key)) for key, _ in columns])
            return _flush()

        if wants_listed(req.include):
            for rows in crud.iter_export_rows(
                db, site, view, on, req, settings.EXPORT_ROW_CAP, user_id
            ):
                yield _emit(rows)

        # 掉榜段接在在榜段之后，与列表里的顺序一致（掉榜恒在末尾）。带筛选时它恒为空，
        # 连查都不用查。导出的必须就是屏幕上那份行集合——否则用户拿到的 CSV 会比界面上
        # 少掉一批词，而那批恰恰是掉榜功能最想让人拿去分析的。
        if wants_dropped(req.include) and not crud.request_has_filters(req):
            for rows in crud.iter_export_dropped_rows(
                db, site, view, on, settings.EXPORT_ROW_CAP
            ):
                yield _emit(rows)
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


async def _resolve_options_scope(
    db: AsyncSession, site: str | None, view: str, on: date | None
) -> tuple[str, date | None]:
    """下拉选项的作用域解析：与列表查询走同一套规则。

    统计按 (站点, 周期, 日期) 分片后，下拉**必须落在某一个日期上**——否则要么把整个窗口
    的分片加起来（就是改造前那个对不上的数），要么无从取数。日期缺省沿用列表的规则：
    取该站点该视图的最新可用日期；显式传了就必须在可选集内，非法即报错、不静默回退。

    该 (站点, 视图) 完全没有数据时返回 ``None``，由调用方给出空选项列表——空作用域取不到
    选项是正常的，不是错误。
    """
    resolved_site = _resolve_site(site)
    dates = await _available_dates(db, resolved_site, view)
    return resolved_site, _resolve_date(on, dates)


async def get_categories(
    db: AsyncSession, site: str | None, view: str, on: date | None = None
) -> list[CategoryOption]:
    """类目下拉选项（按作用域三元组）。"""
    resolved_site, resolved_date = await _resolve_options_scope(db, site, view, on)
    if resolved_date is None:
        return []
    rows = (await db.execute(crud.category_options_stmt(resolved_site, view, resolved_date))).all()
    return [CategoryOption(**opt) for opt in crud.shape_category_options(rows)]


async def get_first_listed(
    db: AsyncSession, site: str | None, view: str, on: date | None = None
) -> FirstListedSummary | None:
    """所选期次「首次入榜」数量 + 统计范围。

    映射窗口起点（日 ``d``；周 ``w-6``）不晚于站点监测起点 ``B``（导入创建的词的最早首次日期）
    时 ``judgeable=False``、``count=None``：那之前没有对照期，**无法判断而不是 0**。
    站点无数据时返回 ``None``（调用方给空），与下拉选项同一约定。
    """
    resolved_site, resolved_date = await _resolve_options_scope(db, site, view, on)
    if resolved_date is None:
        return None
    earliest = (await db.execute(crud.imported_since_stmt(resolved_site))).scalar()
    if earliest is None or crud.first_listed_window_start(view, resolved_date) <= earliest:
        return FirstListedSummary(
            site=resolved_site, view=view, on=resolved_date,  # type: ignore[arg-type]
            judgeable=False, earliest_known_date=earliest,
        )
    count = (await db.execute(crud.first_listed_count_stmt(resolved_site, view, resolved_date))).scalar_one()
    return FirstListedSummary(
        site=resolved_site, view=view, on=resolved_date,  # type: ignore[arg-type]
        judgeable=True, count=count, earliest_known_date=earliest,
    )


async def get_brands(
    db: AsyncSession, site: str | None, view: str, on: date | None = None
) -> list[BrandOption]:
    """品牌下拉选项（按作用域三元组）。与类目同构。"""
    resolved_site, resolved_date = await _resolve_options_scope(db, site, view, on)
    if resolved_date is None:
        return []
    rows = (await db.execute(crud.brand_options_stmt(resolved_site, view, resolved_date))).all()
    return [BrandOption(**opt) for opt in crud.shape_brand_options(rows)]
