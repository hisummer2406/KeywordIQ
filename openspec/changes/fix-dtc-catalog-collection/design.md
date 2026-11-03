## Context

独立站监控（`dtc-store-monitoring`，变更 `add-competitor-dtc-monitoring` 于 2026-09 上线）由每个 uvicorn worker 的后台轮询认领到期站点（`FOR UPDATE SKIP LOCKED`），在事件循环上用 httpx 拉 `/meta.json`、`/products.json?limit=250&page=N` 与畅销榜 HTML，再在线程池里落库。生产 4 个 worker，轮询中某 job 返回 True 会立即进入下一轮，所以积压时每个 worker 都在背靠背地连续拉站点。

2026-11-03 生产只读核查（89 站点、121,020 商品、882,684 变体、89 万价格行）：

| 现象 | 数据 | 根因 |
|---|---|---|
| 只有 26 站目录完整 | 34 站 FAILED，几乎全是第 2–9 页 429；28 站 NO_CATALOG_API，其中 22 站是第 1 页 429 | `_is_client_error` 把 429 归为「确定的 4xx」：不重试、第 1 页即判「未开放接口」 |
| 上新流被存量商品淹没 | 41,105 商品 `first_seen_at > first_fetch_at`；近 7 天流 8,147 条中 4,328 条上架早于首刷一天以上 | `is_first` 以「库里没有商品」判定，`first_fetch_at` 在首次**尝试**时就 `COALESCE` 落定，不论完整与否 |
| 重新出现无历史 | — | `clear_delisted` 直接把 `delisted_at` 置空 |
| 商品可购买、名次失真 | — | `_latest_price_stmt` 取最近变动的单个变体；`products_stmt` 取该商品最后一次上榜的名次而不论日期 |

限流实测：ECS 出口对 `choosemuc.com` 第 2 页单次请求即得 `429`、`retry-after: 60`；同时刻 `orvyla.com` 为 200、`kariney.com` 为 429——限流按站点（或站点 × 出口 IP）施加，不是全局封禁。等待 65 秒后以 2 秒间隔连拉 3 页全部 200。本机出口无限流。

## Goals / Non-Goals

**Goals:**

- 429 不再造成「未开放商品接口」误判与大面积目录不完整。
- 上新流只包含首次完整目录之后才出现的商品。
- 下架、重新出现可回溯；商品级「可购买」「店内名次」不再失真。
- 一次性修复已污染的生产数据，修复规则由用户决定：现有商品全部算基线。

**Non-Goals:**

- 商品主列表、跨站点筛选、从列表进详情等工作台功能（#120 阶段 2，另起变更）。
- 销量、库存数量的数据源（#120 阶段 0/2）。
- 代理 IP、分布式限速、跨 worker 的全局请求调度。
- 403 的重新归类：6 个站点第 1 页 403，可能是商户关闭也可能是机器人拦截，本次不改判据，只在 Open Questions 记录。
- 变体被商户删除后的识别（见 Risks）。

## Decisions

### D1 429 按限流重试，单次拉取有总等待预算

`_get_text` 对 429 单独处理：读 `Retry-After`（秒数；缺失或无法解析按 60 秒），单次等待上限 120 秒、下限 5 秒（`Retry-After: 0` 时若真的不等，每次等待都不消耗预算，重试会无限连发）；**每次站点拉取共享一份等待预算，合计不超过 300 秒**。预算内等待后重试同一个 URL，不计入原有的 2 次网络抖动重试；预算耗尽则抛出限流异常。

调用方把限流异常一律记为 `FETCH_FAILED`，错误文本标明「被目标站点限流」，**任何页都不判 `NO_CATALOG_API`**。畅销榜同样走这条路：第 1 页限流耗尽 → `rank_ok=false`；后续页限流耗尽 → 视为本次名次不可靠，`rank_ok=false` 而非像现在那样当作「榜单到头」写入截断的名次。

- 为什么不是「遇 429 立刻放弃、缩短下次拉取间隔」：每次重拉都从第 1 页开始，大店（最多 16 页）在持续限流下可能永远拿不全；遵循 `Retry-After` 是服务端明确给出的恢复条件，实测有效。
- 为什么要总预算：轮询 job 串行执行，等待会占住该 worker 的轮询循环（导入接手也在同一循环里）。300 秒上限让最坏情况可预期；其余 3 个 worker 照常认领导入与其它站点。
- 为什么不新增 `THROTTLED` 显示状态：spec 的三类失败是「商户选择 / 故障 / 解析坏了」，限流属于暂时性故障，归「抓取失败」并在 `last_error` 写明原因即可；新增状态要连带改前端枚举与文案，收益小。

### D2 同站点请求间隔默认 2000 ms

`DTC_REQUEST_DELAY_MS` 默认值 400 → 2000（`config.py` 与 `.env.example` 同步，单位毫秒）。16 页的大店单次拉取增加约 25 秒，网络等待在事件循环上，不占线程与连接。

**生产 `.env` 显式写了 `DTC_REQUEST_DELAY_MS=400`，改默认值不会生效**；发布清单要求人工把生产值改为 2000（属于生产配置改动，按用户授权执行）。

### D3 基线 = 首次完整拿到目录的时刻（`first_fetch_at` → `baseline_at`）

- 列更名为 `baseline_at`，语义「首次完整目录拉取的时刻」；`apply_fetch_result` 在 `catalog_complete=true` 时以 `COALESCE(baseline_at, now)` 落定，不完整或失败时不动它。
- 去掉 `is_first_fetch` 参数（原以「库里有没有商品」判定），`apply_fetch_result` 在同一事务里读 `baseline_at IS NULL` 判断是否基线前；仅用于日志里的 `new_products` 计数。
- 上新判据不变形：`baseline_at IS NOT NULL AND first_seen_at > baseline_at`。基线落定前（含不完整的若干次）见到的商品 `first_seen_at <= baseline_at`，自然算基线；落定那次首次见到的 `first_seen_at = baseline_at`，同样不算上新。
- 更名而非沿用旧名：旧名「首刷时刻」正是这次缺陷的来源，留着它，下一个读代码的人会照名字理解成「第一次尝试」。API `StoreOut.first_fetch_at` 同步更名，前端只有类型声明引用。
- 替代方案「按 `published_at` 过滤假上新」被否决：现行 spec 明确新品判定不看上架时间（批量重新发布会让它整体跳到当日）。

### D4 下架与重新出现写只追加的事件表

新表 `analysis.competitor_store_product_event(domain, product_id, kind, occurred_at)`，`kind ∈ {DELISTED, RELISTED}`，主键 `(domain, product_id, occurred_at, kind)`，索引 `(domain, occurred_at)` 供阶段 2 按时间窗筛选。`mark_delisted` / `clear_delisted` 在同一事务里按**实际被更新的行**（`UPDATE … RETURNING product_id`）写事件，避免重复事件。`delisted_at` 保留为当前状态，现有查询不变。

- 普通表，不登记 `PARTITIONED_TABLES`（与本模块其余表一致，历史越长越有价值）。量级：现网累计下架约 3,600 个，每天新增事件预计数十到数百行。
- 外键 `domain → competitor_store ON DELETE CASCADE`，与同模块其它表一致。

### D5 商品当前状态的派生口径

- **可购买**：每个变体取最新一条价格观测，商品级 `available = bool_or(变体 available)`。价格仍取当前逻辑（最近一次观测的变体价），本次只修可购买；价格展示口径（最低价/区间）留给阶段 2。
- **当前名次**：只取该站 `max(captured_on)` 那一天的快照；该天未上榜的商品显示「未上榜」，不回退到更早日期的名次。
- 查询代价：`_latest_price_stmt` 由「按商品 DISTINCT ON」改为「按变体 DISTINCT ON 再按商品聚合」，走现有主键 `(domain, variant_id, observed_at)`；单站变体最多约 2.4 万，聚合在请求内完成。名次子查询多一次按 `(domain)` 取最大日期，走主键前缀。均在已有异步连接里执行，不新增连接占用。

### D6 数据修复放在同一个 Alembic 迁移里

迁移只动 `analysis` schema 内本模块的表，步骤：

1. `ALTER TABLE analysis.competitor_store RENAME COLUMN first_fetch_at TO baseline_at`，更新列注释。
2. 用户决定「现有商品全部算基线」：`catalog_complete = true` 的站点 `baseline_at = now()`；其余置 `NULL`，等各自首次完整拉取时落定（届时库里已有商品同样全部算基线）。效果：上新流迁移后清空，之后重新累积。
3. 被 429 误判的站点恢复：`products_json_ok = false AND last_error ILIKE '%429%'` → `products_json_ok = true`、`last_fetch_status = 'FAILED'`、`last_fetch_at = NULL`（下一轮优先重拉）。
4. 建事件表；为现有 `delisted_at IS NOT NULL` 的商品各补一条 `DELISTED` 事件（`occurred_at = delisted_at`）。

站点表 89 行、补事件约 3,600 行，迁移内执行无锁与时长问题。**downgrade**：删事件表、列改回 `first_fetch_at`；第 2、3 步的数据改写不可逆（原值本身是错的），downgrade 不尝试还原，在迁移注释中写明。生产库无自动备份，发布前提醒用户。

## Risks / Trade-offs

- [迁移后上新流为空，过去几周真实上新也一并消失] → 用户已确认取舍；按上架时间的「近 7 天上架」统计不受影响，界面上仍可查到。
- [限流等待占住单个 worker 的轮询最长约 5 分钟，期间该 worker 不接导入] → 总预算封顶；导入由其余 worker 认领，且导入接手本身不依赖某个特定 worker。
- [请求间隔放慢后，单站拉取变长，89 站一轮耗时增加] → 4 个 worker 并行，每站多约 25 秒，一轮仍远小于 24 小时间隔。
- [Shopify 限流策略变化或更严，2 秒间隔仍持续 429] → 状态如实记为「抓取失败（被限流）」，连续失败次数可见；不会再误判成未开放接口或伪造假上新。持续不行再评估代理等方案（Non-Goal）。
- [商户删除的变体仍保留最后一次观测，可能让「可购买」偏乐观] → 本次不跟踪变体存在性；阶段 2 设计商品列表时再评估是否需要变体级「最近一次完整拉取仍可见」标记。
- [基线落定前的商品全部算基线：若某站长期拿不全目录，它的上新流会一直为空] → 站点状态显示「数据不完整/抓取失败」，用户能看出原因；比展示假上新正确。

## Migration Plan

1. 合并后发布前：确认无进行中导入；提醒用户库无自动备份。
2. 发布（工作流内执行 `alembic upgrade head`）。
3. 人工把生产 `.env` 的 `DTC_REQUEST_DELAY_MS` 改为 2000 并重启应用（需用户授权；不改则仍按 400 ms 请求，限流重试仍生效但更频繁触发）。
4. 发布后 24–48 小时只读复核：完整目录站点数、`NO_CATALOG_API` 中 429 是否归零、上新流条数与上架时间分布、日志中限流等待次数。

回滚：代码回滚到上一版本后，旧代码读不到 `first_fetch_at` 会失败，必须同时 `alembic downgrade` 一步；数据修复部分不还原（见 D6）。

## Open Questions

- 6 个第 1 页 403 的站点是否其实是机器人拦截而非商户关闭——需要本机对照请求确认，本次不改判据。
- 发布后若完整率仍低，是否需要跨 worker 的站点级并发控制（例如同一时刻最多 N 个站点在拉取）——以发布后复核数据决定。
