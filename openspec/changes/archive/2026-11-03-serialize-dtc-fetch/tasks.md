## 1. 租约与认领（D1、D3、D4）

- [x] 1.1 模型与迁移：`competitor_store.fetch_lease_until`（可空，带注释）；downgrade 删列
- [x] 1.2 `claim_due_store`：专用键 `pg_try_advisory_xact_lock(0x4B44)`，有未过期租约则不认领，认领时写 `fetch_lease_until = now + 15 min`
- [x] 1.3 `update_store_status` 清租约；`refresh_due_store_round` 异常路径尽力清租约
- [x] 1.4 测试（`test/test_dtc_store_service_integration.py`，真库 5 例）：有未过期租约时不认领；租约过期后可认领；落库后租约清除；另一连接持认领锁时本轮不认领；超时结果不写数据。反向变异：去掉租约判定、去掉咨询锁各有对应用例失败

## 2. 拉取截止（D2）

- [x] 2.1 `refresh_due_store` 用 `asyncio.timeout` 包住 `fetch_store`（14 分钟），超时构造失败结果（无商品、无名次、错误「拉取超时」）并落库
- [x] 2.2 测试（`test/test_dtc_store_fetch_deadline_unit.py` 3 例）：截止短于租约；挂起的拉取在截止后落成无数据失败；落库出错时尽力清租约并继续抛出、轮询 job 吞异常

## 3. 规格、规则与交付

- [x] 3.1 更新 `.claude/rules/competitor.md` 的抓取条目；`openspec validate --strict --all`
- [x] 3.2 后端：`uv run ruff check .`、`uv run pytest test/ -q`（门禁执行，真库 5433）（2026-11-04 提交门禁在 13c473d 上通过；CI 三项通过）
- [x] 3.3 PR #165（`--base v2`，Refs #120）；Codex 评审 13c473d 无意见；用户要求评审后发布，合并为 c6dcb2c，2026-11-04 01:00 发布成功
- [ ] 3.4 发布后只读复核：同一时刻租约数 ≤ 1、日志无多 pid 并发拉取；24 小时后完整率并入 #120 复核（发布后 5 分钟采样结果与 24 小时复核见 #120 评论）
