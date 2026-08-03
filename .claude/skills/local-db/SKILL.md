---
name: local-db
description: 起本地 PostgreSQL 测试库并跑含集成测试的全量回归。当需要运行集成测试、验证迁移、或 pytest 报大量 skip 时使用。
---

# 本地测试库与集成测试

`test/` 下约 45 个集成测试由 `TEST_DATABASE_URL` 环境变量开关控制。不设它们会
**静默跳过**——`225 passed` 与 `181 passed, 45 skipped` 长得很像，跳过的那 45 个
恰好是唯一会碰真实分区表、咨询锁、`COPY` 装载的部分。所以「测试全绿」这句话，
在没设这个变量时是没有意义的。

## 跑法

```bash
# 1. 确认库在
docker ps --filter name=pgbase --format '{{.Names}}\t{{.Ports}}'
# 不在就起：docker start pgbase

# 2. 确认测试库存在（一次性库，可随时重建）
docker exec pgbase psql -U postgres -lqt | cut -d'|' -f1 | grep keywordiq_test \
  || docker exec pgbase createdb -U postgres keywordiq_test

# 3. 建 schema 与表
DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \
DATABASE_URL_ASYNC=postgresql+asyncpg://postgres:pwd123@localhost/keywordiq_test \
  uv run alembic upgrade head

# 4. 全量（含集成）
TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \
  uv run pytest test/ -q
```

全量约 4–5 秒。跑完看到的应当是 `2xx passed, 1 skipped`——**只有 1 个 skip**。
若 skip 数是 45，说明 `TEST_DATABASE_URL` 没生效，这次运行不能作为「通过」的证据。

## 重建测试库

它是一次性的，schema 脏了直接推倒重来，不要手工改：

```bash
docker exec pgbase dropdb -U postgres --if-exists keywordiq_test
docker exec pgbase createdb -U postgres keywordiq_test
# 再跑上面第 3 步
```

## 两个已知的坑

**咨询锁的抢锁时序。** 导入用 `pg_try_advisory_lock` 串行化。测试里若用默认连接池，
上一个测试归还的连接可能仍持有锁，下一个测试拿不到锁而失败，且表现为随机偶发。
测试夹具里给测试引擎显式用 `NullPool`（每次新连接、用完即关）可规避。看到
「导入被拒 / QUEUED」类的偶发失败，先查这一条，不要当成业务 bug 排查。

**迁移的可回滚性。** CI 会跑 `alembic downgrade -1 && alembic upgrade head`。
分区表与物化视图不在 SQLAlchemy 的 DDL 生成范围内，autogenerate 会把事实表产成
普通表并静默丢掉分区。写完迁移务必 `uv run alembic upgrade head --sql` 核对生成的
DDL 里确有 `PARTITION BY`，再本地跑一遍降级升级。
