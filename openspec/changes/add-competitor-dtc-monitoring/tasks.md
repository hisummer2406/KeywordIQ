## 0. 开工前置

- [ ] 0.1 按 `start-change` 认领并建仓库外 worktree `~/.claude-worktrees/add-competitor-dtc-monitoring`
      与分支 `change/add-competitor-dtc-monitoring`（从含本变更文档的提交开出，非从裸 `v2`）；
      补 `.env` 与 `frontend/node_modules`
- [ ] 0.2 确认 `keywordiq_test` 可用并记录后端/前端基线绿数（`TEST_DATABASE_URL` 解开集成
      测试后的 passed 数；注意区分「全绿」与「集成测试静默跳过的假绿」）

## 1. 解析器（纯函数，先做，其余都依赖它）

- [ ] 1.1 建 `test/fixtures/dtc/` 并存两份真实样本：一份 `/products.json` 响应
      （取自 leteimall.com，含多变体商品）、一份 `/collections/all?sort_by=best-selling`
      的 HTML。**样本入库后测试不得再访问外网**
- [ ] 1.2 写 `app/table/competitor/store_fetcher.py` 的解析部分（纯函数，不连库不连网）：
      `parse_products_json()` → 商品与变体的结构化列表；`parse_bestseller_html()` →
      按出现顺序去重的 handle 列表（靠 `/products/{handle}` 链接顺序，**不依赖 class 名**）
- [ ] 1.3 域名规范化函数：去协议、去 `www.`、小写、去尾斜杠；拒绝非法输入并附原因
- [ ] 1.4 单元测试 `test/test_dtc_store_fetcher_unit.py`：多变体商品展开正确、
      `published_at`/`created_at` 都被保留、畅销榜顺序即名次、
      **解析出 0 个商品时返回可区分的「解析失败」而非空列表**、域名规范化的正反例。
      不连库不连网

## 2. 数据模型与迁移

- [ ] 2.1 写 `app/table/competitor/store_model.py` 四张表：
      `CompetitorStore`（`domain` 主键、`platform`、`products_json_ok`、`last_fetch_at`、
      `catalog_complete`、`rank_ok`、`last_fetch_status`、`last_error`、`consecutive_failures`）、
      `CompetitorStoreProduct`（`(domain, product_id)` 主键、`published_at`/`created_at`/
      `first_seen_at`/`delisted_at`、title/handle/vendor/product_type/tags）、
      `CompetitorStorePrice`（`(domain, variant_id, observed_at)` 主键、product_id/sku/
      price/compare_at_price/available）、`CompetitorStoreRank`（`(domain, captured_on, rank)`
      主键、product_id）
- [ ] 2.2 私有关注表 `UserStoreWatch`（`UNIQUE(user_id, domain)`、`group_id` 可空外键
      `ON DELETE SET NULL` 指向既有 `user_seller_group`、`note`）
- [ ] 2.3 五张表全部登记进 `migration_scope.py` 的 `V2_TABLES`；
      **MUST NOT** 登记进 `partitioning.PARTITIONED_TABLES`
- [ ] 2.4 Alembic 迁移（普通表，可 autogenerate）。用 `alembic upgrade head --sql` 核对
      DDL 里**没有** `PARTITION BY`；在测试库上跑一遍 downgrade → upgrade 往返
- [ ] 2.5 `test_v2_integration.py::test_models_match_migrated_schema` 通过——记得在测试
      函数内 import 新模型模块（与 `alembic/env.py` 同一条纪律，漏了表现是一整屏
      `remove_table`）
- [ ] 2.6 配置项 `DTC_FETCH_INTERVAL_HOURS`(24) / `DTC_MAX_PAGES`(40) /
      `DTC_RANK_TOP_N`(100) / `DTC_REQUEST_DELAY_MS`(400) 加进 `config.py`（含
      `field_validator`）并同步 `.env.example`（含单位与说明）

## 3. 抓取与差分写入

- [ ] 3.1 `store_fetcher.py` 的网络部分：`httpx.AsyncClient` 翻页拉全量目录
      （`?limit=250&page=N` 直到空页）+ 拉畅销榜前 N。**返回值必须带
      `catalog_complete` 标记**，仅当每页 200 + 空页自然终止 + 无重试耗尽时为真；
      **撞上 `DTC_MAX_PAGES` 记为不完整**
- [ ] 3.2 失败分三类返回，不合并：目录端点 404/非 JSON → `products_json_ok=false`；
      超时/5xx/重试耗尽 → 抓取失败；畅销榜取到页面但 0 个商品 → `rank_ok=false`。
      目录与名次的成功标记相互独立
- [ ] 3.3 `store_crud.py`：商品 `ON CONFLICT (domain, product_id) DO NOTHING`；
      价格**仅在与该变体最近一条不同时才 INSERT**（比较 price/compare_at_price/available）；
      名次按 `(domain, captured_on, rank)` upsert
- [ ] 3.4 `store_service.py` 的差分与写入：新品判定**看 product_id 在不在库里，不看
      `published_at`**；下架判定**仅在 `catalog_complete` 为真时**执行；
      首刷写入全部商品但**不产生上新事件**
- [ ] 3.5 单元测试 `test/test_dtc_store_service_unit.py`：价格未变不写、变了才写、
      首刷不产生上新事件、`published_at` 整体跳变不产生假上新、
      `catalog_complete=false` 时不判下架、撞页数上限即不完整

## 4. 调度

- [ ] 4.1 到期站点认领：`SELECT ... WHERE last_fetch_at IS NULL OR last_fetch_at <
      now() - interval FOR UPDATE SKIP LOCKED LIMIT 1`。**不用全局咨询锁**
- [ ] 4.2 挂进 `app/table/upload/import_queue.py` 的轮询循环，与
      `_reap_stale_uploads` / `_purge_expired_competitor_previews` 并列；抓完立刻再来
      一轮（照搬导入接手的 `continue`）；异常就地 `logger.exception` 吞掉，不连累排队接手
- [ ] 4.3 **网络拉取直接 `await` 在事件循环上，只有数据库写入走 `run_in_threadpool`**。
      不要照抄旁边 `run_in_threadpool(drain_import_queue, ...)` 把整个抓取塞进线程
- [ ] 4.4 站点表为空时该分支直接跳过，对现有部署零行为改变（回归测试覆盖这一点）

## 5. API

- [ ] 5.1 `store_schemas.py`：站点增删查、关注记录（ProTable 契约 `{data,total,success}`）、
      商品列表、上新流、价格历史、名次走势
- [ ] 5.2 `store_api.py` 端点，全部 `Depends(simple_auth.require_permission(COMPETITOR_VIEW))`，
      **不新增权限点**；挂 `/api/competitor`，跨用户一律 404 而非 403
- [ ] 5.3 上新流端点合并两类来源，每条带来源标记与时间语义字段（独立站=上架于，
      Amazon=发现于）；分页必填、有默认时间窗、不返回全表
- [ ] 5.4 业务错误一律抛 `app/common/errors.py` 的 `APIError` 子类，API 层不做
      try/except 转译；非 Shopify 站点显式拒绝并说明原因
- [ ] 5.5 集成测试 `test/test_dtc_store_integration.py`（`TEST_DATABASE_URL` gate）：
      首刷历史立即可查但上新流为空、第二次拉取的新品进流、价格变更序列正确、
      不完整拉取不判下架、跨用户不可见、删组不取消关注（两类成员都验）

## 6. 前端

- [ ] 6.1 `services/competitor.ts` 补独立站相关请求函数
- [ ] 6.2 竞对监控页新增独立站分段（**不动顶栏**——`/competitor` 是单页顶层，加二级菜单
      会长出约 208px 侧栏）；添加站点入口（粘贴域名）
- [ ] 6.3 状态列按成员类型分流：独立站行显示真实的近 7 日上新数（**含 0**），
      Amazon 卖家行显示占位符；两者 MUST NOT 呈现为同一种视觉状态
- [ ] 6.4 三类失败状态分别呈现：「该站点未开放商品接口」/「最近一次抓取失败（连续 N 次）」/
      「畅销榜解析异常」。**解析异常不得显示为「该站销量榜为空」**
- [ ] 6.5 上新流：混合两类来源，主列表**只用交集字段**（店铺/卖家、商品、时间、来源），
      独立站的变体价格与 Amazon 的 BSR 放详情
- [ ] 6.6 销量展示为「店内畅销榜名次」+ 历史走势；文案 MUST NOT 出现销量/销售额/
      任何绝对数量的表述
- [ ] 6.7 事件回调补 `catch`——Button `onClick`、Select `onChange`、Popconfirm `onConfirm`
      都不消费返回的 promise，失败请求会成为 unhandled rejection（P0 踩过一次，
      见其 tasks 5.7）
- [ ] 6.8 前端单测：状态分流、三类失败文案、上新流时间语义标注

## 7. 文档与收尾

- [ ] 7.1 CLAUDE.md 竞对监控一节补写：独立站与 Amazon 卖家的分合边界（数据/采集分，
      分组/权限/上新流合）、三表三写入模式及其理由、完整性标记是下架判定的前提、
      新品判定不看 `published_at`、**不做取关 GC 且这是有意的**
- [ ] 7.2 `uv run pytest test/ -q`（带 `TEST_DATABASE_URL`）+ `pnpm lint && pnpm tsc &&
      pnpm test` 全绿，且确认不是集成测试静默跳过的假绿
- [ ] 7.3 人工验收：粘贴 3~5 个真实域名 → 首刷（确认历史可查、上新流为空）→ 手工改一条
      价格记录后重跑（确认变更日志追加）→ 断网模拟不完整拉取（确认不判下架）→
      关闭某站的名次解析（确认状态分流正确）→ 混装分组并删组（确认两类成员都存活）
- [ ] 7.4 按路径提交、开 PR（`--base v2`，带变更 id、`Refs #56`、spec delta 文件清单），
      说明这是 issue #56 数据获取层的独立站分支，Amazon 插件采集另立变更
- [ ] 7.5 顺便向运营确认 design.md 里那条 Open Question：独立站只有 17 个，是只关心
      Amazon 卖家，还是没有好办法收藏所以没攒
