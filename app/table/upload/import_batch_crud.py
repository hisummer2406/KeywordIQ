"""导入批次的查询组装层。

`import_batch` 有两个读取入口，它们共用这里的一份查询代码：

- 上传屏的「最近导入状态」看板——最近 N 条，5 秒轮询（:func:`recent_batches_stmt`）
- 导入历史屏——站点/周期/状态三项筛选 + 分页（:func:`list_batches_stmt`）

看板那份查询此前直接内联在 `upload_api` 的路由函数体里，绕过了 crud 层。同一张表两处
各写一份查询，改字段时漏改一处的表现是「两个屏显示的东西对不上」，而这类不一致在
review 里几乎看不出来。

按仓库分层约定：本层只组装与执行查询，**不 commit**、不含业务判定（状态能否重导、
站点码是否合法都在 service 层）。语句组装（``*_stmt``）与执行分开，前者不连库即可断言。
"""

from __future__ import annotations

from sqlalchemy import Select, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.table.upload.import_batch_model import ImportBatch

# 历史列表每页条数上限。超限由 service 层**拒绝**而非截断——静默截断产出的分页与请求的
# 分页不一致，而界面据此计算页数。
MAX_PER_PAGE = 100


def _apply_filters(
    stmt: Select,
    *,
    site: str | None,
    period: str | None,
    status: str | None,
) -> Select:
    """按三项可选条件收窄。任一项为 None 表示不按该项筛。

    取值的合法性由 service 层保证（站点码经站点清单规整、周期与状态按枚举校验），
    本层只负责把已确认合法的值拼进 WHERE。
    """
    if site:
        stmt = stmt.where(ImportBatch.site == site)
    if period:
        stmt = stmt.where(ImportBatch.period_type == period)
    if status:
        stmt = stmt.where(ImportBatch.status == status)
    return stmt


def list_batches_stmt(
    *,
    site: str | None = None,
    period: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> Select:
    """历史列表的一页。按导入时间倒序（走 ``idx_import_batch_created``）。"""
    stmt = _apply_filters(select(ImportBatch), site=site, period=period, status=status)
    return stmt.order_by(desc(ImportBatch.created_at)).offset((page - 1) * per_page).limit(per_page)


def count_batches_stmt(
    *,
    site: str | None = None,
    period: str | None = None,
    status: str | None = None,
) -> Select:
    """与 :func:`list_batches_stmt` 同条件的总数。

    精确实算、不封顶：`import_batch` 是千级以内的小表，计数代价可忽略；而一个封顶的
    total 无法与精确值区分，界面据它算出的页数会把超出上限的记录变得够不着。
    """
    stmt = _apply_filters(select(func.count(ImportBatch.id)), site=site, period=period, status=status)
    return stmt


def recent_batches_stmt(*, period: str | None = None, limit: int = 5) -> Select:
    """看板用：最近 N 条。period 为 None 时不分日报周报。"""
    stmt = _apply_filters(select(ImportBatch), site=None, period=period, status=None)
    return stmt.order_by(desc(ImportBatch.created_at)).limit(limit)


def get_batch_stmt(batch_id: int) -> Select:
    """单条批次。重导前取它来做准入判定。"""
    return select(ImportBatch).where(ImportBatch.id == batch_id)


# ---------------- 执行（薄封装，不 commit） ----------------


async def list_batches(
    db: AsyncSession,
    *,
    site: str | None = None,
    period: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[ImportBatch], int]:
    """返回 (这一页的批次, 同条件下的总数)。"""
    rows = list(
        (
            await db.execute(
                list_batches_stmt(
                    site=site, period=period, status=status, page=page, per_page=per_page
                )
            )
        )
        .scalars()
        .all()
    )
    total = (
        await db.execute(count_batches_stmt(site=site, period=period, status=status))
    ).scalar_one()
    return rows, int(total)


async def recent_batches(
    db: AsyncSession, *, period: str | None = None, limit: int = 5
) -> list[ImportBatch]:
    return list((await db.execute(recent_batches_stmt(period=period, limit=limit))).scalars().all())


async def get_batch(db: AsyncSession, batch_id: int) -> ImportBatch | None:
    return (await db.execute(get_batch_stmt(batch_id))).scalars().first()
