"""竞对监控的数据访问层：只出语句，不执行、不提交、无业务判定。

预览/确认两步流的落库（`bulk_upsert_watches_stmt` 等）用**单条多行 INSERT ... ON
CONFLICT DO UPDATE**而不是逐条循环 + 先查后写：一次网络往返处理整批（最多
``COMPETITOR_MAX_IMPORT_BATCH`` 条），且 Postgres 的 ``DO UPDATE SET`` 只更新显式列出
的列——``append`` 模式只把 ``priority`` 放进 SET 子句，``group_id``/``note`` 天然不受
已存在行影响，不需要额外的分支判断。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, delete, func, literal, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.table.competitor.asin_model import CompetitorAsinReport, CompetitorSellerAsin
from app.table.competitor.seller_model import CompetitorSeller
from app.table.competitor.watch_model import CompetitorWatchPreview, UserSellerGroup, UserSellerWatch

# ---------- 分组 ----------


def _watch_count_subq():
    return (
        select(func.count())
        .select_from(UserSellerWatch)
        .where(UserSellerWatch.group_id == UserSellerGroup.group_id)
        .correlate(UserSellerGroup)
        .scalar_subquery()
    )


def groups_stmt(user_id: int):
    return (
        select(
            UserSellerGroup.group_id,
            UserSellerGroup.name,
            UserSellerGroup.created_at,
            UserSellerGroup.updated_at,
            _watch_count_subq().label("watch_count"),
        )
        .where(UserSellerGroup.user_id == user_id)
        .order_by(UserSellerGroup.name)
    )


def group_stmt(user_id: int, group_id: int):
    """按 id 取分组。恒带 user_id——私有性由这个条件保证，不靠界面不展示。"""
    return select(UserSellerGroup).where(
        UserSellerGroup.group_id == group_id, UserSellerGroup.user_id == user_id
    )


def insert_group_stmt(user_id: int, name: str):
    return (
        pg_insert(UserSellerGroup)
        .values(user_id=user_id, name=name)
        .returning(UserSellerGroup.group_id, UserSellerGroup.created_at, UserSellerGroup.updated_at)
    )


def rename_group_stmt(user_id: int, group_id: int, name: str):
    return (
        update(UserSellerGroup)
        .where(UserSellerGroup.group_id == group_id, UserSellerGroup.user_id == user_id)
        .values(name=name)
        .returning(UserSellerGroup.group_id)
    )


def delete_group_stmt(user_id: int, group_id: int):
    """删组。关注关系由外键 ON DELETE SET NULL 变为未分组，不在这里处理。"""
    return (
        delete(UserSellerGroup)
        .where(UserSellerGroup.group_id == group_id, UserSellerGroup.user_id == user_id)
        .returning(UserSellerGroup.group_id)
    )


def existing_group_names_stmt(user_id: int, names: list[str]):
    return select(UserSellerGroup.group_id, UserSellerGroup.name).where(
        UserSellerGroup.user_id == user_id, UserSellerGroup.name.in_(names)
    )


def insert_groups_stmt(user_id: int, names: list[str]):
    """批量建组，已存在同名的跳过（get-or-create 的"create"那一半）。"""
    return (
        pg_insert(UserSellerGroup)
        .values([{"user_id": user_id, "name": name} for name in names])
        .on_conflict_do_nothing(index_elements=["user_id", "name"])
        .returning(UserSellerGroup.group_id, UserSellerGroup.name)
    )


# ---------- 关注关系 ----------


def watch_count_stmt(user_id: int):
    return select(func.count()).select_from(UserSellerWatch).where(UserSellerWatch.user_id == user_id)


#: 「近 N 日新发现」的窗口。与独立站那侧的 ``new_in_7d`` 取同一个天数，但**含义不同**：
#: 那边按数据源给的 ``published_at`` 算「上架」，这边只能按我们首次见到的时刻算「新发现」。
NEW_DISCOVERY_WINDOW_DAYS = 7


def _new_discoveries_subq():
    """近 N 日新发现数。**无基线时必须是 NULL，不能是 0。**

    这是本查询里唯一容易写错、且错了不会报错的地方：相关子查询里的 ``count(*)`` 恒返回
    0 而不是 NULL，所以必须显式用 ``CASE WHEN baseline_at IS NULL THEN NULL`` 罩一层。
    写成 ``COALESCE(..., 0)`` 或直接裸用 count，整个「待建立基线 vs 监控中」的界面故事
    就地塌掉——0 会被读成"这家店没有新发现"，而事实是"这个卖家还没有被采集过"。

    判据与上新流同构：``first_seen_at > baseline_at`` 且落在窗口内。
    """
    counted = (
        select(func.count())
        .select_from(CompetitorSellerAsin)
        .where(
            CompetitorSellerAsin.seller_id == CompetitorSeller.seller_id,
            CompetitorSellerAsin.site == CompetitorSeller.site,
            CompetitorSellerAsin.first_seen_at > CompetitorSeller.baseline_at,
            CompetitorSellerAsin.first_seen_at
            > func.now() - text(f"interval '{NEW_DISCOVERY_WINDOW_DAYS} days'"),
        )
        .correlate(CompetitorSeller)
        .scalar_subquery()
    )
    return case((CompetitorSeller.baseline_at.is_(None), None), else_=counted)


def _last_reported_at_subq():
    """最近一次被采集的时刻。

    界面必须有它：Amazon 侧的覆盖率取决于运营的浏览习惯，**「很久没采过」与「确实没有
    新发现」在界面上长得一模一样**。缺了这一列，一个三个月没被点开的卖家会长期显示
    「监控中 · 0」，与"一切正常且没有动静"完全相同。

    这里不必像上面那样罩 CASE：``max()`` 对空集天然返回 NULL，而有上报就必然有基线
    （基线正是在首次上报时落定的）。
    """
    return (
        select(func.max(CompetitorAsinReport.reported_at))
        .where(
            CompetitorAsinReport.seller_id == CompetitorSeller.seller_id,
            CompetitorAsinReport.site == CompetitorSeller.site,
        )
        .correlate(CompetitorSeller)
        .scalar_subquery()
    )


def _last_report_item_count_subq():
    """最近一次上报的条目数（#166 D2）。**只表示那次见到了多少，不代表全店总量**。

    与 ``_last_reported_at_subq`` 取同一行（同卖家最新 ``reported_at``，平局按 ``report_id``），
    走 ``idx_competitor_asin_report_latest``。无上报时为 NULL。
    """
    return (
        select(CompetitorAsinReport.item_count)
        .where(
            CompetitorAsinReport.seller_id == CompetitorSeller.seller_id,
            CompetitorAsinReport.site == CompetitorSeller.site,
        )
        .order_by(CompetitorAsinReport.reported_at.desc(), CompetitorAsinReport.report_id.desc())
        .limit(1)
        .correlate(CompetitorSeller)
        .scalar_subquery()
    )


def _status_expr(last_reported_at):
    """三态（competitor-storefront-watch「Amazon 卖家行展示手动采集与三态数据状态」）。

    在 SQL 里判、用数据库的 ``now()``：与「近 N 日新发现」同一个时钟、同一个窗口常量，
    不会出现「新发现数按库时钟算、陈旧按应用时钟算」的两套口径。有基线却没有上报记录在
    正常写入路径下不会出现（基线在首次上报时落定），出现就按陈旧处理，不当成已采集。
    """
    window = func.now() - text(f"interval '{NEW_DISCOVERY_WINDOW_DAYS} days'")
    return case(
        (CompetitorSeller.baseline_at.is_(None), literal("NOT_COLLECTED")),
        (or_(last_reported_at.is_(None), last_reported_at <= window), literal("STALE")),
        else_=literal("COLLECTED"),
    )


def watches_stmt(user_id: int, group_id: int | None, offset: int, limit: int):
    stmt = (
        select(
            UserSellerWatch.watch_id,
            UserSellerWatch.seller_id,
            UserSellerWatch.site,
            UserSellerWatch.group_id,
            UserSellerGroup.name.label("group_name"),
            UserSellerWatch.priority,
            UserSellerWatch.note,
            UserSellerWatch.created_at,
            CompetitorSeller.baseline_at,
            CompetitorSeller.seller_name,
            _status_expr(_last_reported_at_subq()).label("status"),
            _new_discoveries_subq().label("new_discoveries_7d"),
            _last_reported_at_subq().label("last_reported_at"),
            _last_report_item_count_subq().label("last_report_item_count"),
        )
        .select_from(UserSellerWatch)
        .join(
            CompetitorSeller,
            (CompetitorSeller.seller_id == UserSellerWatch.seller_id)
            & (CompetitorSeller.site == UserSellerWatch.site),
        )
        .outerjoin(UserSellerGroup, UserSellerGroup.group_id == UserSellerWatch.group_id)
        .where(UserSellerWatch.user_id == user_id)
    )
    if group_id is not None:
        stmt = stmt.where(UserSellerWatch.group_id == group_id)
    return (
        stmt.order_by(UserSellerWatch.priority.desc(), UserSellerWatch.created_at.desc())
        .offset(offset)
        .limit(limit)
    )


def asin_detail_stmt(user_id: int, seller_id: str, site: str, asin: str):
    """一个 Amazon ASIN 的详情：首次发现时刻（真实数据）+ 卖家级最近采集时刻（近似值）。

    **私有性由 join `UserSellerWatch` 承担**，不是查完再判断——不这样写的话，任何人
    传一个别人关注的 `(seller_id, site)` 都能查到数据，共享层的 `competitor_seller_asin`
    本身不区分是谁在关注。`_last_reported_at_subq()` 复用卖家清单同一个查询，两处
    "最近采集于"必须来自同一个定义，不然同一个卖家在两处显示不同的值。
    """
    return (
        select(
            CompetitorSellerAsin.first_seen_at,
            _last_reported_at_subq().label("seller_last_reported_at"),
        )
        .select_from(CompetitorSellerAsin)
        .join(
            CompetitorSeller,
            (CompetitorSeller.seller_id == CompetitorSellerAsin.seller_id)
            & (CompetitorSeller.site == CompetitorSellerAsin.site),
        )
        .join(
            UserSellerWatch,
            (UserSellerWatch.seller_id == CompetitorSellerAsin.seller_id)
            & (UserSellerWatch.site == CompetitorSellerAsin.site)
            & (UserSellerWatch.user_id == user_id),
        )
        .where(
            CompetitorSellerAsin.seller_id == seller_id,
            CompetitorSellerAsin.site == site,
            CompetitorSellerAsin.asin == asin,
        )
    )


def owned_watch_stmt(user_id: int, watch_id: int):
    """本人的一条关注及其卖家基线。不是本人的一律查不到（调用方据此 404，不是 403）。"""
    return (
        select(UserSellerWatch.seller_id, UserSellerWatch.site, CompetitorSeller.baseline_at)
        .join(
            CompetitorSeller,
            (CompetitorSeller.seller_id == UserSellerWatch.seller_id)
            & (CompetitorSeller.site == UserSellerWatch.site),
        )
        .where(UserSellerWatch.user_id == user_id, UserSellerWatch.watch_id == watch_id)
    )


def seller_asins_stmt(seller_id: str, site: str, baseline_at, offset: int, limit: int):
    """该卖家在该站点下已采集到的商品，按首次发现倒序（#166 D3）。

    只读共享观测，不复制、不新建产品库。``is_baseline``：首批与基线同一事务写入，
    ``first_seen_at`` 与 ``baseline_at`` 严格相等（见 ``asin_service`` 模块文档）。
    次级排序键 ``asin`` 保证同一时刻的多条翻页稳定。走 ``idx_competitor_seller_asin_new``。
    """
    is_baseline = (
        CompetitorSellerAsin.first_seen_at == baseline_at if baseline_at is not None else literal(False)
    )
    return (
        select(
            CompetitorSellerAsin.asin,
            CompetitorSellerAsin.title,
            CompetitorSellerAsin.image_url,
            CompetitorSellerAsin.first_seen_at,
            is_baseline.label("is_baseline"),
        )
        .where(CompetitorSellerAsin.seller_id == seller_id, CompetitorSellerAsin.site == site)
        .order_by(CompetitorSellerAsin.first_seen_at.desc(), CompetitorSellerAsin.asin)
        .offset(offset)
        .limit(limit)
    )


def seller_asins_count_stmt(seller_id: str, site: str):
    return (
        select(func.count())
        .select_from(CompetitorSellerAsin)
        .where(CompetitorSellerAsin.seller_id == seller_id, CompetitorSellerAsin.site == site)
    )


def watches_in_group_count_stmt(user_id: int, group_id: int | None):
    stmt = (
        select(func.count())
        .select_from(UserSellerWatch)
        .where(UserSellerWatch.user_id == user_id)
    )
    if group_id is not None:
        stmt = stmt.where(UserSellerWatch.group_id == group_id)
    return stmt


def watched_pairs_stmt(user_id: int, keys: list[tuple[str, str]]):
    """这批 (seller_id, site) 里哪些当前用户已经在关注——预览阶段判定 new/existing 用。"""
    return select(UserSellerWatch.seller_id, UserSellerWatch.site).where(
        UserSellerWatch.user_id == user_id,
        tuple_(UserSellerWatch.seller_id, UserSellerWatch.site).in_(keys),
    )


def delete_watches_stmt(user_id: int, watch_ids: list[int]):
    return (
        delete(UserSellerWatch)
        .where(UserSellerWatch.user_id == user_id, UserSellerWatch.watch_id.in_(watch_ids))
        .returning(UserSellerWatch.watch_id)
    )


def update_watch_stmt(user_id: int, watch_id: int, *, group_id: int | None, note: str | None, set_group: bool, set_note: bool):
    """改分组/改备注。``set_group``/``set_note`` 区分"没传该字段"与"传了 null"——
    PATCH 语义下两者含义不同（前者不改，后者显式清空）。"""
    values: dict[str, Any] = {}
    if set_group:
        values["group_id"] = group_id
    if set_note:
        values["note"] = note
    return (
        update(UserSellerWatch)
        .where(UserSellerWatch.watch_id == watch_id, UserSellerWatch.user_id == user_id)
        .values(**values)
        .returning(UserSellerWatch.watch_id)
    )


def delete_watches_in_group_stmt(user_id: int, group_id: int):
    """replace_group 模式：清空目标分组下的全部关注关系。"""
    return (
        delete(UserSellerWatch)
        .where(UserSellerWatch.user_id == user_id, UserSellerWatch.group_id == group_id)
        .returning(UserSellerWatch.watch_id)
    )


# ---------- 落库（commit） ----------


def insert_sellers_stmt(rows: list[dict[str, str]]):
    """``rows``: ``[{"seller_id":..., "site":...}, ...]``，已按 (seller_id, site) 去重。"""
    return (
        pg_insert(CompetitorSeller)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["seller_id", "site"])
        .returning(CompetitorSeller.seller_id)
    )


def upsert_watches_stmt(rows: list[dict[str, Any]], *, overwrite_group: bool):
    """批量 upsert 关注关系。

    ``rows``: ``[{"user_id":..., "seller_id":..., "site":..., "group_id":...,
    "priority":..., "note":...}, ...]``。

    ``overwrite_group=False``（append 模式）：已存在的行只更新 ``priority``，
    ``group_id``/``note`` 保持不变——不在 SET 子句里就不会被覆盖，无需额外判断。
    ``overwrite_group=True``（replace_group 模式）：连 ``group_id``/``note`` 一并覆盖
    ——这个模式下调用前已经清空了目标分组，出现冲突意味着该卖家原本被关注在别的分组，
    此次导入的意图是让它归入目标分组。
    """
    stmt = pg_insert(UserSellerWatch).values(rows)
    set_ = {"priority": stmt.excluded.priority}
    if overwrite_group:
        set_["group_id"] = stmt.excluded.group_id
        set_["note"] = stmt.excluded.note
    return stmt.on_conflict_do_update(
        index_elements=["user_id", "seller_id", "site"], set_=set_
    ).returning(UserSellerWatch.watch_id)


# ---------- 预览暂存 ----------


def insert_preview_stmt(preview_token: str, user_id: int, parsed: dict, expires_at):
    return (
        pg_insert(CompetitorWatchPreview)
        .values(preview_token=preview_token, user_id=user_id, parsed=parsed, expires_at=expires_at)
        .returning(CompetitorWatchPreview.preview_token)
    )


def preview_stmt(preview_token: str, user_id: int):
    return select(CompetitorWatchPreview).where(
        CompetitorWatchPreview.preview_token == preview_token,
        CompetitorWatchPreview.user_id == user_id,
    )


def delete_preview_stmt(preview_token: str):
    return delete(CompetitorWatchPreview).where(
        CompetitorWatchPreview.preview_token == preview_token
    )


def delete_expired_previews_stmt(now):
    return delete(CompetitorWatchPreview).where(CompetitorWatchPreview.expires_at < now)
