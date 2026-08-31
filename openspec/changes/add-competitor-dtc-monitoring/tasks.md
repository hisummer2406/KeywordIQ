## 0. 开工前置

- [x] 0.1 按 `start-change` 认领并建仓库外 worktree `~/.claude-worktrees/add-competitor-dtc-monitoring`
      与分支 `change/add-competitor-dtc-monitoring`（从含本变更文档的提交 `8d49c2e` 开出，
      非从裸 `v2`）；已补 `.env` 与 `frontend/node_modules`。认领扫描确认无人在做：
      无本地 `change/*` 分支、无 worktree、无开着的 PR
- [x] 0.2 `keywordiq_test` 已在（活动连接 0，未打断他人）。后端基线
      **656 passed, 1 skipped**（唯一跳过是需真实 OSS 回路那个，非集成测试静默全跳过）；
      前端 `pnpm lint` + `pnpm tsc` 通过（vitest 全量留到收工前一次性跑）

## 1. 解析器（纯函数，先做，其余都依赖它）

- [x] 1.1 建 `test/fixtures/dtc/` 并存真实样本（取自 leteimall.com，2026-08-31）：
      `products_page1.json`（4 个商品，含 6 变体/2 变体/1 变体各一）、
      `products_empty_page.json`（空页 = 翻页自然终止）、`bestselling_page1.html`
      （前 6 个商品卡片的真实标记）、`bestselling_expected_order.json`（期望名次）、
      `bestselling_no_products.html`（解析失败样本）。**只截断了长数组与描述文本，
      字段名与类型一字未改**；样本入库后测试不再访问外网
- [x] 1.2 写 `app/table/competitor/store_fetcher.py` 的解析部分（纯函数，不连库不连网）：
      `parse_products_json()` → `ParsedCatalogPage`；`parse_bestseller_html()` →
      按出现顺序去重的 handle 元组（靠 `/products/{handle}` 链接顺序，不依赖 class 名）。
      **实测修正两处**：(a) 畅销榜每页 **20** 个商品、**每个 handle 出现 3 次**，
      去重是必须的，取前 100 名要翻 5 页而非原估的 2~4 页；(b) 同名的 JSON 端点
      `/collections/all/products.json` **实测忽略 `sort_by`**（三种排序返回同一字母序），
      名次只能走 HTML
- [x] 1.3 `normalize_store_domain()`：去协议/端口/路径/查询串、去 `www.`、小写、去尾点。
      亚马逊链接**单独拒绝并指明去处**（笼统报「不是合法域名」会让用户以为链接坏了，
      而实际只是加错了地方）；保留子域（`de.molooco.com` 是独立站点）
- [x] 1.4 单元测试 `test/test_dtc_store_fetcher_unit.py`，**29 例全绿**、`ruff` 通过：
      多变体展开与价格类型、三个时间戳都保留且带时区、**缺价格返回 None 而非 0**
      （0 会被当成免费，静默产生假降价记录）、空页与 HTML 响应必须区分、缺主键字段的
      行跳过而非整页失败、tags 两种形态、畅销榜顺序即名次、去重、
      **0 个商品抛 `RankParseError` 而非返回空列表**、域名规范化 8 个正例与 6 个反例

## 2. 数据模型与迁移

- [x] 2.0（补，实现时发现）**价格必须带币种，且这一列不能事后补。** 实测 17 个站点里
      已有两种币种（leteimall/kydschoice 是 USD，**blessrich 是 GBP**），而
      `/products.json` 的价格数字本身不带币种——不存它，`£12.99` 与 `$18.98` 会在界面上
      并排显示成同一个数；更要紧的是价格历史一旦入库，事后补币种补不回来（不知道当时
      那个数是哪国钱）。币种取自 `/meta.json`，该端点一次请求同时回答三件事，故一并采纳：
      **(a) 币种**；**(b) 是不是 Shopify**——形状不符即判定非 Shopify，不另写一套平台
      探测（看 `powered-by` 响应头要多发一次请求且可被 CDN 改写）；
      **(c) `published_products_count`**——权威商品总数，用作翻页完整性的交叉校验，
      比「翻到空页」强得多，实测三家店（1202 / 151 / 1241）与逐页累计**完全吻合**。
      另收 `myshopify_domain`（换自定义域名时不变，可发现「同一家店换了域名」）与
      `ships_to_countries`。已加 `parse_store_meta()` + `MetaParseError` + 7 个用例
- [x] 2.1 写 `app/table/competitor/store_model.py` 四张表：
      `CompetitorStore`（`domain` 主键、`platform`、**`currency`/`country`/`shop_id`/
      `myshopify_domain`/`ships_to_countries`**、`products_json_ok`、`last_fetch_at`、
      `catalog_complete`、`rank_ok`、`last_fetch_status`、`last_error`、`consecutive_failures`）、
      `CompetitorStoreProduct`（`(domain, product_id)` 主键、`published_at`/
      **`source_created_at`/`source_updated_at`**（改名以免与我方时间混淆）/
      `first_seen_at`/`last_seen_at`/`delisted_at`、title/handle/vendor/product_type/tags/
      image_url）、`CompetitorStorePrice`（`(domain, variant_id, observed_at)` 主键、
      product_id/sku/price/compare_at_price/available，**没有「当前价」列**——当前价就是
      `observed_at` 最大的那行，多存一份等于给同一事实开两个来源）、
      `CompetitorStoreRank`（`(domain, captured_on, rank)` 主键、product_id）
- [x] 2.2 私有关注表 `UserStoreWatch`（`UNIQUE(user_id, domain)`、`group_id` 可空外键
      `ON DELETE SET NULL` 指向**既有** `user_seller_group`、`note`）
- [x] 2.3 五张表全部登记进 `migration_scope.py` 的 `V2_TABLES`（含不要登记进
      `PARTITIONED_TABLES` 的理由注释）；`alembic/env.py` 与
      `test_v2_integration.py` 的 import 同步补上 `store_model`
- [x] 2.4 Alembic 迁移 `a29e42df072d`（autogenerate，5 表 + 6 索引，无多余差异——
      说明既有 schema 也没漂移）。在**一次性刮擦库** `keywordiq_dtc_scratch` 上做，
      不碰共享的 `keywordiq_v2_dev` 与 `keywordiq_test`；`upgrade --sql` 核对
      **0 处 `PARTITION BY`**；应用后再 autogenerate 产出空 upgrade/downgrade；
      downgrade → upgrade 往返验过（表消失又回来）；刮擦库已删
- [x] 2.5 `test_v2_integration.py` **7 passed**，含
      `test_models_match_migrated_schema`（迁移到 head 的库上 autogenerate 为空）
- [x] 2.6 配置项 `DTC_FETCH_INTERVAL_HOURS`(24) / `DTC_MAX_PAGES`(40) /
      `DTC_RANK_TOP_N`(100) / `DTC_REQUEST_DELAY_MS`(400) 加进 `config.py`（含
      `field_validator`）并同步 `.env.example`（含单位与说明）。
      当时 `test_conventions.py::test_no_new_dead_settings` 是红的（这四项还没有代码读
      它们，正是 `MAX_FILE_SIZE` 那笔死配置的同一形态），第 3~4 组落地后转绿——**故本组
      与第 3~4 组必须同批提交**
- [x] 2.7（补，写差分层时发现）**删掉了原计划的 `last_seen_at` 列。** 它需要每次拉取
      更新每一行（100 家店约 8 万行/天的无条件 UPDATE），与本表「只增不改」直接冲突，
      而它完全可推导：`delisted_at` 为空即「上次完整拉取时它还在」，那个时刻就是
      `competitor_store.last_fetch_at`。迁移已按无此列重新生成

## 3. 抓取与差分写入

- [x] 3.1 `store_fetcher.fetch_store()`：`httpx.AsyncClient`（**由调用方注入**，测试用
      `httpx.MockTransport` 喂 fixture，不出网）翻页拉全量目录 + 拉畅销榜前 N。
      返回 `StoreFetchOutcome`，带 `catalog_complete`。
      **完整性有三道判据**，任一不满足即不完整：每页 200、翻页因空页自然终止（撞上
      `DTC_MAX_PAGES` 记为不完整）、**取到的商品数不少于 `/meta.json` 声称的
      `published_products_count`**（第三道是新加的，比前两道硬得多）
- [x] 3.2 失败分三类，互不合并。**实测修正一处**：`/products.json` 被关闭的真实形态是
      **HTTP 404**，而不是「200 但返回 HTML」——最初只认后者，404 走了网络失败分支报成
      `FETCH_FAILED`，用户会看到「连续 N 次失败」以为是暂时故障在等，而实际上端点永久
      没有了。两种形态现在都映射到 `NO_CATALOG_API`。顺带 **4xx 不重试**（确定的答复不是
      抖动，重试只会把一次答复拖成三倍等待）。目录与名次的成功标记相互独立；
      meta 失败不致命（站点加入时已验过是 Shopify，此处多半是抖动，保留库中既有币种）
- [x] 3.3 `store_crud.py`：商品 `ON CONFLICT DO NOTHING`；价格用 `DISTINCT ON` 取每个
      变体最近一条再比对，**只在 price/compare_at_price/available 任一变化时才 INSERT**；
      名次先删当日再写（`captured_on` 是日期，同日重跑即覆盖）；`mark_delisted` /
      `clear_delisted` 都是条件更新，不碰无关行
- [x] 3.4 `store_service.apply_fetch_result()`：新品判定**看 `product_id` 在不在库里**；
      下架判定**仅在 `catalog_complete` 为真时**执行；首刷写入全部商品但 `new_products`
      报 0（报实际数量会让日志与上新流对不上）；重新上架清掉下架标记
- [x] 3.5 测试：`test_dtc_store_fetch_unit.py` **13 例**（拉取与完整性，
      `MockTransport`，0.08 秒——重试退避做成可注入参数，否则空转 9 秒）+
      `test_dtc_store_service_integration.py` **16 例**（写入语义，`TEST_DATABASE_URL`
      gate，同步会话 + SAVEPOINT 回滚）。覆盖：价格未变不写 / 变了才写且保留历史 /
      是否有货变化也算变化 / 首刷不产生上新事件但历史立即可查 / `published_at` 整体跳变
      不产生假上新 / 不完整时绝不判下架 / 完整时缺席即下架 / 重新上架 / 同日名次幂等 /
      meta 失败不清空既有币种 / 认领只取到期站点并当场推进拉取时刻

## 4. 调度

- [x] 4.1 `store_crud.claim_due_store()`：`FOR UPDATE SKIP LOCKED LIMIT 1` + **认领时当场
      把 `last_fetch_at` 推到现在**。不用全局咨询锁。`last_fetch_at` 记的是「上次尝试」
      不是「上次成功」，这是有意的——否则一个持续失败的站点会在每轮轮询里被反复重抓，
      把整个循环占满，其余站点永远轮不上
- [x] 4.2 挂进 `import_queue.py` 的轮询循环，与 `_reap_stale_uploads` /
      `_purge_expired_competitor_previews` 并列。**排在接手排队批次之后**——导入是用户在
      等的事，拉取不是，让路给前者；拉到了就 `continue` 立刻再来一轮，好让积压的站点连着
      排空。异常就地 `logger.exception` 吞掉，不连累排队接手
- [x] 4.3 网络那段直接 `await` 在事件循环上，**只有数据库写入走 `run_in_threadpool`**
      （`_claim_due_store` / `_persist` 两处）。理由写在 `refresh_due_store` 的 docstring 里
- [x] 4.4 站点表为空时 `claim_due_store` 返回 None，该分支直接跳过——对现有部署零行为改变
      （既有的 `test_import_queue_*` 全部照常通过）

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
