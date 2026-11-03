## 1. 限流与请求节奏（D1、D2）

- [x] 1.1 `store_fetcher`：429 单独处理——读 `Retry-After`（缺失/非法按 60 s，单次上限 120 s、下限 5 s），每次站点拉取共享 300 s 等待预算，耗尽抛限流异常；429 不再走 `_is_client_error` 的「确定 4xx」分支
- [x] 1.2 目录翻页：限流异常任何页都记 `FETCH_FAILED` 并注明被限流，不判 `NO_CATALOG_API`；畅销榜第 1 页及后续页限流耗尽均 `rank_ok=false`，不写截断名次
- [x] 1.3 `DTC_REQUEST_DELAY_MS` 默认 400 → 2000，同步 `config.py` 注释与 `.env.example`（单位毫秒）
- [x] 1.4 测试（`test/test_dtc_store_throttle_unit.py` 10 例，httpx MockTransport，等待用注入的假 sleep）：一次 429 后恢复可完整；第 1 页持续 429 → FAILED 且 `products_json_ok=true`；`Retry-After` 缺失/超上限；预算耗尽；畅销榜中途限流 → `rank_ok=false`；404 仍判 `NO_CATALOG_API`；加入站点探测遇 429 给限流原因（单条与批量）；`Retry-After: 0` 按下限等待、预算照样封顶。反向变异：去掉 429 分支后 9 例中 7 例失败

## 2. 上新基线（D3）

- [x] 2.1 模型与 schema：`first_fetch_at` → `baseline_at`（列注释改为「首次完整目录拉取时刻」）；`StoreOut` 字段同名变更
- [x] 2.2 `apply_fetch_result` 仅在 `catalog_complete` 时 `COALESCE` 落定 `baseline_at`；去掉 `is_first_fetch` 参数，改在 `apply_fetch_result` 内读 `baseline_at` 判断是否基线前（只影响日志里的上新计数）；`new_arrivals` 判据改用 `baseline_at`
- [x] 2.3 测试（`test/test_dtc_store_service_integration.py`，真库）：首刷只拿 1 页、次日完整 → 存量不进上新流；首刷第 1 页失败 → 同上；基线后不完整拉取中出现的新商品进上新流；基线落定后再次完整拉取不改变基线

## 3. 下架与重新出现事件（D4）

- [x] 3.1 模型：`CompetitorStoreProductEvent`（`domain, product_id, kind, occurred_at`，主键四列，`(domain, occurred_at)` 索引，FK 级联），不登记 `PARTITIONED_TABLES`
- [x] 3.2 `mark_delisted` / `clear_delisted` 改为 `UPDATE … RETURNING product_id`，按实际更新行在同一事务写事件
- [x] 3.3 测试（`test/test_dtc_store_service_integration.py`，真库）：完整拉取后消失 → 一条 DELISTED；再出现 → 一条 RELISTED 且原事件仍在、不进上新流；同一天重复拉取不产生重复事件；不完整拉取不写下架事件

## 4. 商品当前状态口径（D5）

- [x] 4.1 `_latest_price_stmt`：按变体取最新观测后按商品 `bool_or(available)`；价格口径保持不变
- [x] 4.2 `products_stmt` 当前名次只取该站 `max(captured_on)` 快照
- [x] 4.3 前端：`services/competitor.ts` 字段改 `baseline_at`；`ProductDetailDrawer.tsx` 文案「在售/下架」→「可购买/不可购买」，同步相关测试断言
- [x] 4.4 测试（`test/test_dtc_store_api_integration.py`，真库）：三变体一个售罄 → 商品可购买；两周前上榜、最新快照未上榜 → `latest_rank` 为空

## 5. 迁移与数据修复（D6）

- [x] 5.1 Alembic 迁移：列更名与注释、建事件表；`catalog_complete=true` 站点 `baseline_at=now()`，其余置 NULL；`products_json_ok=false AND last_error ILIKE '%429%'` 的站点恢复可拉取（`products_json_ok=true`、`last_fetch_status='FAILED'`、`last_fetch_at=NULL`）；为已下架商品补 DELISTED 事件；downgrade 删表与改回列名，注明数据改写不可逆
- [x] 5.2 测试：迁移在 `keywordiq_test` 上 upgrade/downgrade/upgrade 往返（手工执行）；`test_models_match_migrated_schema` 通过；修复语句抽成 `REPAIR_SQL` 常量，`test/test_dtc_store_migration_repair_integration.py` 在回滚事务里执行并核对四类站点与补事件、重复执行不重复（不在共享库上来回 downgrade，避免打断并行会话）

## 6. 规格、检查与交付

- [x] 6.1 `openspec validate --strict --all`
- [x] 6.2 后端：`uv run ruff check .`、`uv run pytest test/ -q`（`TEST_DATABASE_URL` 指向 5433 的 `keywordiq_test`）（2026-11-03：ruff 通过；1402 passed / 1 skipped；openspec 40 项全部通过）
- [x] 6.3 前端：`pnpm lint && pnpm tsc && pnpm test`（2026-11-03 由提交门禁在 1cafbb4 上执行并通过：eslint、tsc、vitest；同一次门禁另跑了 ruff 与真库 pytest）
- [x] 6.4 PR #164（`--base v2`，Refs #120；Codex 评审 8646fdb 无意见，合并为 66fa062，2026-11-04 发布成功）写明：生产 `.env` 的 `DTC_REQUEST_DELAY_MS=400` 需改为 2000；迁移含不可逆数据修复、库无自动备份；发布后上新流会清空重新累积
- [ ] 6.5 发布后（用户授权）24–48 小时只读复核：完整目录站点数、429 误判是否归零、上新流条数与上架时间分布、限流等待日志（2026-11-04 发布 66fa062 后未到复核时点，已转入 #120 评论 https://github.com/hisummer2406/KeywordIQ/issues/120#issuecomment-6608955412 跟踪）
