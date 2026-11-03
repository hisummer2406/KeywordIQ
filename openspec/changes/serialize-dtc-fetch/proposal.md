## Why

PR #164（`fix-dtc-catalog-collection`）上线后，429 已按 `Retry-After` 等待重试，完整目录站点从 28 升到 37 / 89，但仍有大量站点耗尽 300 s 等待预算：发布后 16 个被认领的站点里 4 个在第 1–9 页被持续限流。日志显示 4 个 worker 在同一时刻**各自**拉不同的站点、同时被限流——Shopify 对 ECS 出口的限流看起来按出口 IP 整体施加，而不只按单个站点。4 路并发等于把请求速率乘以 4，2 秒间隔被抵消。

用户决定（2026-11-04）：同一时刻只拉一个站点。

## What Changes

- 全局同一时刻最多一个站点在拉取：站点表新增拉取租约时刻 `fetch_lease_until`；认领在短事务内用专用的事务级咨询锁串行判定「当前有无未过期租约」，有则本轮不认领。
- 单次拉取设硬性截止（短于租约），保证租约过期前拉取一定结束；超时记为抓取失败且不写入任何数据。
- 拉取结束落库时清除租约；进程被杀或崩溃时租约自然过期，其后的拉取恢复。
- 修改 `dtc-store-monitoring` 的调度要求：原「MUST NOT 使用全局咨询锁串行化」改为「全局单站点拉取，但不在拉取全程持有数据库连接或锁」；同步更新 `.claude/rules/competitor.md` 的对应条目。

不影响 ABA 日报、周报的导入与查询；导入仍优先于独立站拉取（轮询顺序不变），且使用不同的锁。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `dtc-store-monitoring`：「拉取调度复用既有轮询任务，按状态列认领到期站点」——允许并要求全局单站点拉取，规定实现方式为租约列 + 认领时的事务级锁，并新增拉取截止与租约过期场景。

## Impact

- 后端：`app/table/competitor/store_crud.py`（认领条件、租约写入与清除）、`store_service.py`（拉取截止、落库清租约）、`store_model.py`。
- 数据库：Alembic 迁移为 `analysis.competitor_store` 新增可空列 `fetch_lease_until`（89 行，常量缺省 NULL，不重写表）。
- 规则：`.claude/rules/competitor.md` 一条。
- 运行：一轮 89 站由 4 路并行变为串行，单站约 1–3 分钟（含限流等待），一轮约 1.5–4.5 小时，仍在 24 小时拉取间隔内。发布重启时若有站点在拉取中被中断，独立站拉取最多暂停一个租约时长（15 分钟）。
