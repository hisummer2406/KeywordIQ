"""词库的数据访问层：只出语句，不执行、不提交、无业务判定。

**盯盘查询的形状与分析查询屏相反**：那边 FROM 是事实表（行集合 = 在榜 ∪ 掉榜），
这边 FROM 是条目表，事实表与掉榜表都是 LEFT JOIN 上去补数的。清单是用户自己定的，
词掉出榜单恰恰是他最需要看见的事，不能因为事实表里没有行就让它消失。

一次查完（而不是「先分页取条目、再批量补事实」那套两步法）的理由是**排序**：盯盘默认
按当期排名排，而排名在事实表里，两步法第一步就没有排序依据。代价是要指望规划器选对
驱动顺序，兜底是 ``LIBRARY_MAX_ITEMS_PER_GROUP``——一个组至多那么多条，最坏也就是那么多
行的嵌套循环，两侧都走现成索引（``idx_fact_{daily,weekly}_keyword_history`` 是
``(site, keyword_id, 日期列)``，``fact_dropped`` 的主键是
``(site, period_type, anchor_date, keyword_id)``）。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.table.analysis import analysis_crud
from app.table.analysis.dimension_model import DimKeyword
from app.table.analysis.fact_model import FactDropped
from app.table.keyword.library_model import KeywordGroup, KeywordGroupItem

# 三段序：在榜 → 掉榜 → 该期无数据。掉榜与无数据没有可比较的排名，混进排序会让
# 「排得最靠后的词」和「没有数据的词」看起来是一回事。
_SEG_LISTED = 0
_SEG_DROPPED = 1
_SEG_MISSING = 2


# ---------- 分组 ----------

def _item_count_subq():
    """分组的条目数子查询（相关子查询，分组数受上限约束、量级极小）。"""
    return (
        select(func.count())
        .select_from(KeywordGroupItem)
        .where(KeywordGroupItem.group_id == KeywordGroup.group_id)
        .correlate(KeywordGroup)
        .scalar_subquery()
    )


def groups_stmt(user_id: int, site: str | None = None):
    """某用户的分组列表（可按站点过滤），带条目数。"""
    stmt = (
        select(
            KeywordGroup.group_id,
            KeywordGroup.site,
            KeywordGroup.name,
            KeywordGroup.created_at,
            KeywordGroup.updated_at,
            _item_count_subq().label("item_count"),
        )
        .where(KeywordGroup.user_id == user_id)
        .order_by(KeywordGroup.site, KeywordGroup.name)
    )
    if site is not None:
        stmt = stmt.where(KeywordGroup.site == site)
    return stmt


def group_stmt(user_id: int, group_id: int):
    """按 id 取分组。**恒带 user_id**——私有性由这个条件保证，不靠界面不展示。"""
    return select(KeywordGroup).where(
        KeywordGroup.group_id == group_id, KeywordGroup.user_id == user_id
    )


def group_count_stmt(user_id: int, site: str):
    return (
        select(func.count())
        .select_from(KeywordGroup)
        .where(KeywordGroup.user_id == user_id, KeywordGroup.site == site)
    )


def insert_group_stmt(user_id: int, site: str, name: str):
    return (
        pg_insert(KeywordGroup)
        .values(user_id=user_id, site=site, name=name)
        .returning(KeywordGroup.group_id, KeywordGroup.created_at, KeywordGroup.updated_at)
    )


def rename_group_stmt(user_id: int, group_id: int, name: str):
    return (
        update(KeywordGroup)
        .where(KeywordGroup.group_id == group_id, KeywordGroup.user_id == user_id)
        .values(name=name)
        .returning(KeywordGroup.group_id)
    )


def delete_group_stmt(user_id: int, group_id: int):
    """删组。条目由外键 ON DELETE CASCADE 连带删除，不在这里逐条删。"""
    return (
        delete(KeywordGroup)
        .where(KeywordGroup.group_id == group_id, KeywordGroup.user_id == user_id)
        .returning(KeywordGroup.group_id)
    )


# ---------- 条目 ----------

def item_count_stmt(group_id: int):
    return (
        select(func.count())
        .select_from(KeywordGroupItem)
        .where(KeywordGroupItem.group_id == group_id)
    )


def keyword_sites_stmt(keyword_ids: list[int]):
    """取这批关键词各自的站点，用于校验与分组站点一致。

    顺带承担「这些 id 真的存在吗」——查回来的条数少于传入的，说明有 id 不存在。
    """
    return select(DimKeyword.keyword_id, DimKeyword.site).where(
        DimKeyword.keyword_id.in_(keyword_ids)
    )


def insert_items_stmt(group_id: int, keyword_ids: list[int]):
    """批量加词。``DO NOTHING`` + ``RETURNING`` 使幂等与「实际新增了几条」一步得出，
    不必先查一遍已存在的再算差集。"""
    return (
        pg_insert(KeywordGroupItem)
        .values([{"group_id": group_id, "keyword_id": kid} for kid in keyword_ids])
        .on_conflict_do_nothing(index_elements=["group_id", "keyword_id"])
        .returning(KeywordGroupItem.keyword_id)
    )


def delete_items_stmt(group_id: int, keyword_ids: list[int]):
    return (
        delete(KeywordGroupItem)
        .where(
            KeywordGroupItem.group_id == group_id,
            KeywordGroupItem.keyword_id.in_(keyword_ids),
        )
        .returning(KeywordGroupItem.keyword_id)
    )


# ---------- 盯盘 ----------

def _segment_expr(fact, dropped_kid):
    """行的三段序表达式：事实行在 → 在榜；掉榜行在 → 掉榜；都不在 → 无数据。"""
    return case(
        (fact.keyword_id.isnot(None), _SEG_LISTED),
        (dropped_kid.isnot(None), _SEG_DROPPED),
        else_=_SEG_MISSING,
    )


def _sort_column(fact, field: str):
    """排序列。``added_at`` 落在条目表上，其余都在事实表上。"""
    if field == "added_at":
        return KeywordGroupItem.added_at
    return getattr(fact, field)


def watch_stmt(
    group_id: int,
    site: str,
    view: str,
    on: date,
    *,
    order_by: str,
    order_dir: str,
    offset: int,
    limit: int,
):
    """盯盘一页：条目驱动，LEFT JOIN 事实表与掉榜表。

    列投影复用 ``analysis_crud.fact_metric_columns``——**这份投影只有一处定义**，
    两屏各写一份的话，分叉的表现是同一个词在两屏显示不同的值，且不会报错。

    身份列（keyword_id / keyword）取自条目表与维度表，**不取自事实表**：事实行可能整行
    为空（掉榜或该期无数据），从那里取会得到一行 NULL 的"幽灵词"。
    """
    fact, date_col = analysis_crud.fact_and_datecol(view)
    dims = analysis_crud.new_fact_row_dims()
    period = "WEEK" if view == "WEEK" else "DAY"

    stmt = (
        select(
            KeywordGroupItem.keyword_id.label("keyword_id"),
            DimKeyword.keyword.label("keyword"),
            KeywordGroupItem.added_at.label("added_at"),
            _segment_expr(fact, FactDropped.keyword_id).label("segment"),
            FactDropped.last_seen_date.label("last_seen_date"),
            FactDropped.last_ranking.label("last_ranking"),
            *analysis_crud.fact_metric_columns(fact, dims),
        )
        .select_from(KeywordGroupItem)
        .join(DimKeyword, DimKeyword.keyword_id == KeywordGroupItem.keyword_id)
        .join(
            fact,
            (fact.keyword_id == KeywordGroupItem.keyword_id)
            & (fact.site == site)
            & (date_col == on),
            isouter=True,
        )
        .join(
            FactDropped,
            (FactDropped.keyword_id == KeywordGroupItem.keyword_id)
            & (FactDropped.site == site)
            & (FactDropped.period_type == period)
            & (FactDropped.anchor_date == on),
            isouter=True,
        )
    )
    stmt = analysis_crud.join_fact_row_dims(stmt, fact, dims).where(
        KeywordGroupItem.group_id == group_id
    )

    col = _sort_column(fact, order_by)
    ordering = col.desc().nulls_last() if order_dir == "desc" else col.asc().nulls_last()
    # 段序恒在最前，**不随升降序翻转**——与分析屏「掉榜行恒排在在榜行之后」同一条约定。
    # keyword_id 兜底保证分页稳定。
    return stmt.order_by(
        _segment_expr(fact, FactDropped.keyword_id).asc(),
        ordering,
        KeywordGroupItem.keyword_id.asc(),
    ).offset(offset).limit(limit)


def shape_watch_rows(rows) -> list[dict]:
    """查询行 → 行字典，并把段序翻译成三态。

    ``is_dropped`` 由 ``status`` 派生且只在这一处派生：它存在只是因为行的形状与分析屏
    共用（前端的掉榜标签复用同一段渲染）。

    **NULL 一律剔除，交给 schema 的默认值**。掉榜与无数据的行在这条 LEFT JOIN 里会把
    事实表那批列取回成 None，而行 schema 里像 ``top1_click_share: float = 0`` 这样的字段
    并不接受 None——不剔除就是一片校验错误。分析屏的掉榜段是靠"根本不 select 那些列"
    达到同一效果的（``shape_dropped_rows``），这边列是统一的，只能在这里剔。
    """
    out: list[dict] = []
    for row in rows:
        data = {k: v for k, v in row._mapping.items() if v is not None}
        segment = data.pop("segment")
        status = {
            _SEG_LISTED: "LISTED",
            _SEG_DROPPED: "DROPPED",
            _SEG_MISSING: "MISSING",
        }[segment]
        data["status"] = status
        data["is_dropped"] = status == "DROPPED"
        out.append(data)
    return out
