"""导入历史：查询组装与准入判定的单元测试（不连库）。

crud 层只断言**组装出的 SQL 形状**（编译 statement），service 层直测判定分支并把 crud
与 OSS 换成替身。两层都不碰真实数据库——判定逻辑本就与库无关，接真库只会让这批测试
在没有 PostgreSQL 时静默跳过，而这正是「模型改了、查询层没跟上」那类问题溜过去的方式。
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.common.errors import (
    CODE_UPLOAD_BATCH_NOT_FOUND,
    CODE_UPLOAD_RETRY_NOT_ALLOWED,
    CODE_UPLOAD_SOURCE_NOT_RETAINED,
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableError,
)
from app.table.upload import import_batch_crud, import_history_service
from app.table.upload.import_batch_model import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
)


def _sql(stmt) -> str:
    """编译成带字面量的 SQL 文本，便于断言形状。"""
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# ---------------- crud：语句组装 ----------------


def test_list_stmt_orders_by_created_desc_and_pages():
    sql = _sql(import_batch_crud.list_batches_stmt(page=3, per_page=20))
    assert "ORDER BY" in sql and "created_at DESC" in sql
    assert "LIMIT 20" in sql
    assert "OFFSET 40" in sql  # 第 3 页 = 跳过前 40 条


def test_list_stmt_without_filters_has_no_where():
    """三项筛选全缺省时不应凭空生成 WHERE——那会让「不筛」变成「按空值筛」。"""
    assert "WHERE" not in _sql(import_batch_crud.list_batches_stmt())


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"site": "DE"}, ["site = 'DE'"]),
        ({"period": "WEEK"}, ["period_type = 'WEEK'"]),
        ({"status": STATUS_FAILED}, ["status = 'FAILED'"]),
        (
            {"site": "US", "period": "DAY", "status": STATUS_COMPLETED},
            ["site = 'US'", "period_type = 'DAY'", "status = 'COMPLETED'"],
        ),
    ],
)
def test_list_stmt_applies_each_filter(kwargs, expected):
    sql = _sql(import_batch_crud.list_batches_stmt(**kwargs))
    for fragment in expected:
        assert fragment in sql


def test_count_stmt_shares_filters_with_list_stmt():
    """总数与列表必须同条件，否则翻页会翻到不存在的页。"""
    filters = {"site": "GB", "period": "DAY", "status": STATUS_FAILED}
    count_sql = _sql(import_batch_crud.count_batches_stmt(**filters))
    assert "count(" in count_sql
    for fragment in ["site = 'GB'", "period_type = 'DAY'", "status = 'FAILED'"]:
        assert fragment in count_sql
    # 计数不该带分页
    assert "LIMIT" not in count_sql and "OFFSET" not in count_sql


def test_recent_stmt_is_limited_and_unfiltered_by_status():
    """看板只按周期筛（兼容 daily/weekly），不按状态或站点——它要显示的正是刚失败的那条。"""
    sql = _sql(import_batch_crud.recent_batches_stmt(limit=5))
    assert "LIMIT 5" in sql
    assert "created_at DESC" in sql
    assert "WHERE" not in sql

    scoped = _sql(import_batch_crud.recent_batches_stmt(period="DAY", limit=5))
    assert "WHERE" in scoped and "period_type = 'DAY'" in scoped
    assert "status" not in scoped.split("WHERE")[1]


# ---------------- service：查询参数校验 ----------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one(self):
        return len(self._rows)


class _FakeDB:
    """只回放预设结果的假会话。crud 的语句形状由上面那组测试守着，这里不关心。"""

    def __init__(self, rows=()):
        self.rows = list(rows)

    async def execute(self, stmt):
        return _FakeResult(self.rows)


@pytest.mark.parametrize("bad_site", ["UK", "ZZ", "notasite"])
async def test_query_rejects_unknown_site(bad_site):
    """非法站点报错、不回退默认——回退会让用户以为在看 GB 的历史，其实看的是 US 的。"""
    with pytest.raises(BadRequestError):
        await import_history_service.query_batches(_FakeDB(), site=bad_site)


async def test_query_rejects_unknown_period_and_status():
    with pytest.raises(BadRequestError):
        await import_history_service.query_batches(_FakeDB(), period="MONTH")
    with pytest.raises(BadRequestError):
        await import_history_service.query_batches(_FakeDB(), status="DONE")


async def test_query_rejects_per_page_over_cap_instead_of_truncating():
    """超上限必须**拒绝**。截断会返回一份与请求不符的分页，而界面据 total 与自己以为的
    每页条数算页数，算出来全是错的。"""
    with pytest.raises(BadRequestError):
        await import_history_service.query_batches(
            _FakeDB(), per_page=import_batch_crud.MAX_PER_PAGE + 1
        )


@pytest.mark.parametrize("page, per_page", [(0, 20), (-1, 20), (1, 0), (1, -5)])
async def test_query_rejects_nonpositive_paging(page, per_page):
    with pytest.raises(BadRequestError):
        await import_history_service.query_batches(_FakeDB(), page=page, per_page=per_page)


async def test_query_accepts_cap_exactly():
    rows, total = await import_history_service.query_batches(
        _FakeDB(), per_page=import_batch_crud.MAX_PER_PAGE
    )
    assert rows == [] and total == 0


# ---------------- service：重导准入判定 ----------------


def _batch(**overrides):
    base = dict(
        id=7,
        site="DE",
        period_type="WEEK",
        report_date=date(2026, 7, 13),
        file_name="DE_热门搜索词_简单_Week_2026_07_13.csv",
        object_key="imports/DE/WEEK/2026-07-13/ab12_DE.csv",
        status=STATUS_FAILED,
        total_rows=3_000_000,
        written_rows=0,
        processing_seconds=12,
        error_message="COPY 中断",
        created_at=datetime(2026, 7, 13, 3, 0, 0),
        completed_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def patched(monkeypatch):
    """把 crud 与 OSS 换成替身，并记录 OSS 校验是否被调用过。"""
    calls: dict[str, object] = {"verified": None}

    def _install(batch):
        async def fake_get_batch(db, batch_id):
            return batch

        def fake_verify(object_key, site, period, report_date, *, bucket=None):
            calls["verified"] = (object_key, site, period, report_date)
            return 123

        monkeypatch.setattr(import_history_service.import_batch_crud, "get_batch", fake_get_batch)
        monkeypatch.setattr(
            import_history_service.oss_client, "verify_source_object", fake_verify
        )
        return calls

    return _install


async def test_retry_of_failed_batch_returns_plan(patched):
    batch = _batch()
    calls = patched(batch)

    plan = await import_history_service.prepare_retry(_FakeDB(), batch.id)

    assert plan.source_batch_id == batch.id
    assert (plan.site, plan.period, plan.report_date) == ("DE", "WEEK", date(2026, 7, 13))
    assert plan.object_key == batch.object_key
    # OSS 校验必须真的跑过：对象可能已被清理，不查就会产生一个注定失败的后台任务
    assert calls["verified"] == (batch.object_key, "DE", "WEEK", date(2026, 7, 13))


async def test_retry_missing_batch_is_404(patched):
    patched(None)
    with pytest.raises(NotFoundError) as exc:
        await import_history_service.prepare_retry(_FakeDB(), 999)
    assert exc.value.code == CODE_UPLOAD_BATCH_NOT_FOUND


@pytest.mark.parametrize("status", [STATUS_COMPLETED, STATUS_QUEUED, STATUS_PROCESSING])
async def test_retry_of_non_failed_batch_is_409(patched, status):
    """只允许 FAILED。COMPLETED 的数据正在被查询使用，重导会先删再重写（周报要跑好几
    分钟，期间该切片查不到东西）；QUEUED/PROCESSING 本就在执行中。"""
    calls = patched(_batch(status=status))
    with pytest.raises(ConflictError) as exc:
        await import_history_service.prepare_retry(_FakeDB(), 7)
    assert exc.value.code == CODE_UPLOAD_RETRY_NOT_ALLOWED
    assert calls["verified"] is None  # 本地就否掉了，不付网络代价


async def test_retry_without_retained_source_is_422(patched):
    """本地退路导入的批次没有 object_key，无从重建数据源——当场说清楚要重传。"""
    calls = patched(_batch(object_key=""))
    with pytest.raises(UnprocessableError) as exc:
        await import_history_service.prepare_retry(_FakeDB(), 7)
    assert exc.value.code == CODE_UPLOAD_SOURCE_NOT_RETAINED
    assert "重新上传" in exc.value.message
    assert calls["verified"] is None


async def test_retry_error_messages_never_leak_object_key(patched):
    """object key 含站点/期次/日期/原始文件名，属内部细节，只进日志。"""
    key = "imports/DE/WEEK/2026-07-13/ab12_DE.csv"
    patched(_batch(status=STATUS_COMPLETED, object_key=key))
    with pytest.raises(ConflictError) as exc:
        await import_history_service.prepare_retry(_FakeDB(), 7)
    assert key not in exc.value.message
