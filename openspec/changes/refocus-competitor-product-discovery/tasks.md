> 来源：#166 第一至三、五节。市场分析解耦（第四节）由 `retire-analysis-competitor-filter`（PR #167）承担，不在本变更。
> 进度留痕：2026-11-04 提案并开始实现（Claude Code，worktree `~/.claude-worktrees/refocus-competitor-product-discovery`，分支 `change/refocus-competitor-product-discovery`）。后端第 1～4 节、前端第 5～7 节、插件第 8 节、命名第 9 节、规则 10.1 已完成，尚未提交；**前端 lint/tsc/test 未运行**（按约定由用户或提交门禁运行）；集成测试跑在一次性库 `keywordiq_a_mig`（5433）上，没有改动共享的 `keywordiq_test`（理由见 1.1）。
> 已定：单批上限 1000，插件改为从服务端读取；插件顺带上报可选的 `seller_name`；子菜单用真实子路由（2026-11-04 用户在评估时选定）。

## 0. 前置

- [x] 0.1 （2026-11-10 #167 合并为 7bfae57 并归档，已把 origin/v2 合入，无冲突）PR #167 合并后，把 `origin/v2` 合进本分支。#167 改过 `.claude/rules/competitor.md` 第 16 行和 `openspec/guides/competitor.md`，与本变更第 10 节会碰到同一位置；第 10 节等合并后再做，其余各节不受影响，已先开始
- [x] 0.2 （2026-11-10 只读核对：生产 `/var/www/keywordiq/.env` 显式写了 `COMPETITOR_MAX_REPORT_ITEMS=200`，会覆盖新默认值 1000。是否在发布时改为 1000 由用户决定，已写进 PR 风险项；本变更不改生产）只读核对生产 `.env` 是否显式设置了 `COMPETITOR_MAX_REPORT_ITEMS`（需用户授权读取；design D4 Open Question）。如果覆盖为 200，记录并交由用户决定，不在本变更里改生产

## 1. 卖家店名：迁移与写入（competitor-asin-ingest「卖家店名按最新非空值覆盖」，design D5）

- [x] 1.1 迁移 `e8b2c6d4f1a3_竞对卖家店名.py`：`competitor_seller.seller_name varchar(200) NULL`，带注释，downgrade 删列；模型 `seller_model.SELLER_NAME_COMMENT` 与迁移注释一字不差。在一次性库 `keywordiq_a_mig` 上从零升级到 head，降一级、再升回，`information_schema` 确认列类型、长度与注释；`test_v2_integration.py` 的模型与库一致性用例通过。**没有对共享的 `keywordiq_test` 执行升级**：先加列会让其他分支的门禁出现模型与库不一致。本分支提交前需在门禁之前把 `keywordiq_test` 升到 head（见 11.1）
- [x] 1.2 `AsinReportRequest.seller_name`；`asin_crud.update_seller_name_stmt` 带 `IS DISTINCT FROM`，店名不变时不写行；`asin_service.submit_report` 只在非重放路径、店名去空白后非空时调用；`compute_request_hash` 未改动，店名不进摘要
- [x] 1.3 `test_competitor_asin_integration.py` 新增 5 例：首次写入并去首尾空白；不带店名或全空白不清空；另一个用户上报的新值覆盖旧值；重放带不同店名时按重放处理、店名不变；500 字截断到 200。共享行本来就没有账号列，由表结构保证

## 2. 单批上限（competitor-asin-ingest「服务端公布单批上限」+ 输入约束；design D4）

- [x] 2.1 `config.py` 与 `.env.example` 默认值改为 1000，注明单位、整批拒绝，以及插件从接口读取
- [x] 2.2 `asin_api.get_collector_config` → `asin_service.collector_config()` → `CollectorConfigResponse`
- [x] 2.3 `test_competitor_collector_config_unit.py` 3 例：返回值随 monkeypatch 改变；类默认值为 1000；只有分析权限时 403。集成测试新增 2 例：1000 条一次提交落定基线，之后再报 999 条旧的加 1 条新的，`new_asins == 1` 且基线不变；1001 条在请求模型层被拒，提示「最多 1000 条」

## 3. 店铺管理后端（competitor-storefront-watch「Amazon 卖家行展示手动采集与三态数据状态」「点开 Amazon 卖家查看已采集到的商品」；design D2、D3）

- [x] 3.1 `watch_crud._status_expr` 在 SQL 里用数据库 `now()` 判三态，与新发现数共用同一个时钟和窗口（有基线却没有上报时按陈旧处理）；`_last_report_item_count_subq` 按 `reported_at desc, report_id desc` 取最新一条；`WatchOut` 新增 `last_report_item_count`、`seller_name`；删除 `watch_service._status_of`
- [x] 3.2 `watch_api.list_seller_asins` → `watch_service.list_seller_asins`（`owned_watch_stmt` 不是本人的关注即 404）→ `seller_asins_stmt`：首次发现倒序、`asin` 作次级键，`is_baseline` 取 `first_seen_at = baseline_at`
- [x] 3.3 集成测试新增 5 例：6 天前上报为已采集，8 天前为数据陈旧；上报条数取最新一次（1）而不是首次（3）；清单带出店名；已采集商品两页合计 3 条，顺序与基线标记正确；用别人的 watch_id 请求返回 404。原有 3 例的断言改为新状态值，`test_competitor_watch_unit.py` 的枚举断言改为三态

## 4. 商品发现后端（competitor-storefront-watch「商品发现合并两类来源，标注时间语义，筛选在同一次查询内生效」；design D6）

- [x] 4.1 `NewArrivalRequest` 新增 `source`、`site`、`group_id`、`seller_id`、`domain`（店铺筛选拆成两个扁平字段：`seller_id` 可与 `site` 组合，`domain` 只对应 Shopify）。`store_crud.ArrivalFilters` 两侧各自施加，被排除的一侧不进 UNION，两侧都被排除时保留一侧加恒假条件；`store_service._arrival_filters` 规范化站点（非法 422）、校验分组归属（别人的分组 404），域名按加入时的同一套规则规范化
- [x] 4.2 两侧投影新增 `product_handle`、`discovered_at`、`published_at`；Amazon 的 `source_name` 改为取 `competitor_seller.seller_name`；`product_url` 由 `store_service._product_url` 拼接（Amazon 用 `get_site(site).domain`，Shopify 用 `domain/products/handle`）
- [x] 4.3 集成测试新增 13 例：8 种筛选组合逐一比对，总数与翻完所有页的行数、预期值三者相等且没有重复；分组只含组内两类成员；别人的分组 404；非法站点 422；看品字段（GB 链接、店名、Shopify 链接与两个时间、US 行没有店名时为空）；不传新字段时总数不变。原有跨站点用例继续通过。`test_competitor_asin_unit.py` 的投影列一致性断言改为明确写出这一组刻意的列差异。竞对、店铺、DTC、卖家、ASIN 相关测试全部 258 例通过
- [x] 4.4 一次性库数据极少，所以用 `SET enable_seqscan=off` 验证的是「索引可用」，不是生产上的实际选择。结果：Amazon 侧走 `idx_competitor_seller_asin_new`，`first_seen_at` 窗口进入了 Index Cond；Shopify 侧在空表上选了同样以 `domain` 开头的 `idx_competitor_store_product_handle`，`first_seen_at` 成了过滤条件。Shopify 侧的 SQL 除新增的可选筛选外没有变化，执行计划随数据量的取舍与改动前一致。**生产规模下的计划没有验证**，发布后如需确认，用 diagnose-v2 只读 EXPLAIN

## 5. 前端导航（top-nav-structure「竞对监控以任务菜单组织」及 MODIFIED 单页条款；design D1）

- [x] 5.1 `routes.ts`：父节点去掉 component，挂 `access`；子项是重定向、商品发现（`./Competitor/Discovery`）和店铺管理（`./Competitor/Stores`）。`routes.test.ts`：单页顶层名单去掉竞对监控；分组重定向列表加上竞对监控；新增「只有两项、按任务划分」「子屏继承 access」两条
- [x] 5.2 `index.tsx` 改名为 `Stores.tsx`，`NewArrivalsTab.tsx` 改名为 `Discovery.tsx`，测试文件同步改名；`GroupBar`、`ImportModal`、`StoreTab`、两个详情抽屉都原样复用

## 6. 店铺管理前端（design D2、D3）

- [x] 6.1 `Stores.tsx` 用 `Segmented` 切换类型，`useSearchParams` 读写 `?type=shopify`（缺省为 Amazon）；Amazon 类型显示配额卡片和「添加卖家」，Shopify 类型显示 `StoreTab` 自带的「添加 Shopify 店铺」
- [x] 6.2 `columns.tsx`：三态 Tag 加悬浮说明，未知值回落「未采集」；新增「采集方式：手动采集」列；卖家列显示店名并附 seller_id，没有店名时显示 seller_id，点击打开已采集抽屉；最近采集列显示时刻（陈旧时用 warning 色）和「上报 N 条（不代表全店总量）」；操作列有看新商品、打开商品列表、采集说明（`CollectGuide.tsx`：四步操作、分批提交的风险、风控说明）、取消关注。前端不再自行推算是否陈旧
- [x] 6.3 `SellerAsinsDrawer.tsx`：Alert 写明「已采集的商品，不是全部在售商品」并附采集说明；分页表格显示主图（缺失时「无图」）、标题（缺失时显示 ASIN）、ASIN 链接、首次发现于和「基线」标记
- [ ] 6.4 前端测试已写（更正：此前误记为门禁已通过；CI 上 Discovery/AsinDetailDrawer 各红 1 例，已修，等重跑）：`Stores.test.tsx` 新增或改写三态、手动采集、上报条数文案、店名回退、看新商品跳转、`?type=` 刷新保留、切换后写入 URL；`StoreTab.test.tsx` 中的文案断言改为「Shopify 店铺 / 自动检查正常」。抽屉的空列表和错误态还没有单独的用例。`pnpm lint && pnpm tsc && pnpm test` 由用户运行，或由提交门禁执行

## 7. 商品发现前端（design D6）

- [x] 7.1 `Discovery.tsx`：时间范围、来源、Amazon 站点（来源为 Shopify 或已指定域名时禁用）、分组；店铺筛选不做成预加载全部关注的下拉框（最多 3000 个关注，预加载不全或很慢），改为从店铺管理的「看新商品」带 `seller`+`site` 或 `domain` 过来，显示为可关闭的 Tag。所有筛选写入 URL；`filtersFromParams` 把非法值落回缺省；request 通过 ref 读取最新筛选，首次挂载不重复请求
- [x] 7.2 主图（缺失时「无图」）、商品（标题缺失时显示商品标识；ASIN 或商品 ID；「打开原商品」在新窗口打开，没有链接时不显示）、来源店铺（Amazon/Shopify Tag + 站点 + 店名，没有店名时显示 key）、时间（Shopify 显示上架于 + 首次发现于，Amazon 只显示首次发现于，悬浮说明补采老品的情况）；`AsinDetailDrawer` 补上主图、ASIN 和原商品链接，并在「首次发现于」后注明不是上架时间
- [ ] 7.3 前端测试已写（更正：此前误记为门禁已通过；CI 上 Discovery/AsinDetailDrawer 各红 1 例，已修，等重跑）：`Discovery.test.tsx` 改写，覆盖时间措辞、来源标签不出现「独立站」、看品链接、缺图/缺店名/缺链接时的降级、有店名时的显示、URL 筛选进入请求体、非法参数落回缺省、店铺 Tag、切到 Shopify 时清掉站点、跨站点两行、首刷说明、空状态、两类详情抽屉

## 8. 插件（competitor-browser-collector 两条 ADDED；design D4、D5）

- [x] 8.1 `collector-config.ts`（只接受正整数，401 单独返回，其余一律 unavailable）；`background.handleGetConfig`（令牌自愈逻辑与上报相同，至多重试一次）；`messages.ts` 新增 `ConfigMessage` / `ConfigResult`；`content-script` 打开面板时读取，读不到就禁止「采集本页」（已累积的条目仍可提交），清空和提交结束后都不会解除这个禁止；删除 `MAX_REPORT_ITEMS`、`getMaxReportItems` / `setMaxReportItems`、options 页的字段及其校验
- [x] 8.2 `describeCapExceeded` 已改写
- [x] 8.3 `parseSellerName`：候选选择器**未经真实页面验证**，只接受 200 字以内的文本，取不到返回 undefined；提交时解析并经消息传给 background；`build-report` 去首尾空白，空值不发送
- [x] 8.4 新增 `collector-config.test.ts` 4 例；`background.test.ts` 新增 handleGetConfig 3 例；`parse-storefront` 店名 3 例；`build-report` 店名 2 例；`capture-accumulator` 1000 上限 1 例。删除旧的默认 200 与手填上限用例。2026-11-04 `pnpm run typecheck` 通过，`pnpm run test` 12 个文件 104 例全部通过。content-script 中「读不到就禁止」的 DOM 行为没有自动化测试（与原有面板逻辑一样只能人工验证），列入 11.4
- [x] 8.5 manifest 与 package 都升到 0.2.0（manifest.test 校验两者一致）；`docs/extension-install.md` 首次设置改为两项，补充上报条数、店名、风控等注意事项，FAQ 改为 1000 上限和「无法获取服务端上限」的处理；`extension/README.md` 同步修改

## 9. Shopify 命名（competitor-storefront-watch「当前接入的建站平台命名为「Shopify 店铺」」、dtc-store-monitoring MODIFIED；design D7）

- [x] 9.1 `StoreTab` 的添加按钮、空状态、弹窗标题和平台限制说明；`storeStatus` 中 OK 状态改为「自动检查正常」；`storeColumns` 新增「采集方式：自动检查」列和「看新商品」；商品发现的来源标签为 Amazon/Shopify。剩下的「独立站」只出现在代码注释里
- [x] 9.2 diff 中 `store_fetcher.py` 和 `app/jobs/` 都没有改动；竞对、店铺、DTC 相关后端测试 258 例通过（见 4.3）；`ProductDetailDrawer` 未改

## 10. 规则与文档

- [x] 10.1 `.claude/rules/competitor.md` 末尾新增三条（三态、店名、UNION 前筛选），`.claude/rules/extension.md` 新增一条（上限只来自服务端）。只追加在末尾，不改 #167 已修改的第 16 行
- [x] 10.2 （2026-11-10 合入 v2 后复核：crosslink 链接已由 #167 删除，competitor 规则新增三条与 #167 改动的行不冲突，AGENTS 不用改）`extension/README.md` 已改（8.5）。`openspec/guides/competitor.md` 只有链接，不需要改（本变更的 design 链接在归档时加；crosslink 链接由 #167 删除）。AGENTS「竞对监控」审核投影已检查：没有涉及状态、上限或店名的条目，不用改。**等 #167 合并、把 v2 合进来后再复核一次**

## 11. 验收

- [ ] 11.1 （更正：84ec7d6 的门禁在 `git add -A && git commit` 组合命令里先于暂存运行，实际没有检查任何东西。手工结果：ruff、pytest 全量（`keywordiq_a_mig`）、插件 typecheck/test、`openspec validate --strict --all` 40/40 通过；前端在 PR #174 的 CI 上红了 2 例，已修，等这次修复提交的门禁和 CI 结果）提交前：`uv run ruff check .`、`uv run pytest test/ -q`（门禁），前端三项（门禁），插件 `typecheck && test`（门禁），`openspec validate --strict --all`
- [x] 11.2 真库回归（2026-11-10）：共享 `keywordiq_test` 停在核价分支的迁移 `140193107685`，与本变更的 `e8b2c6d4f1a3` 是同一父迁移下的两个分叉，所以改在本机一次性库 `keywordiq_a_mig`（已迁到 head）上跑全量 `pytest test/`：1461 通过、1 跳过（`test_oss_ingestion_integration`，需要 TEST_OSS_*，属预期排除），竞对集成用例全部实际执行。worktree `.env` 已写 `TEST_DATABASE_URL` 指向该库，供门禁使用（#173）
- [ ] 11.3 浏览器验收（用户）：左侧菜单与折叠；`/competitor` 的跳转；店铺管理的类型切换、三态、上报条数文案、采集说明、已采集商品抽屉；商品发现的筛选、看图、打开原商品、Shopify 双时间；Shopify 列表、详情和失败状态与改动前一致
- [ ] 11.4 **真实 Amazon 页面人工验收**（用户，至少一个站点）。逐项记录站点、卖家规模和现象：
  - 添加店铺后显示「未采集」；
  - 多页累积后一次提交，结果显示「已采集」、上报条数正确，首批没有出现在商品发现里；
  - 断网时提交失败，已采集内容保留，恢复网络后重试，服务端只产生一条上报记录；
  - 同一批再次采集不产生新发现；
  - 店铺新出现商品后再采集，该商品出现在商品发现中，主图、标题、链接正确；
  - 店名能否取到，以及取到的值；
  - 实际可采集的最大条数和翻页上限；
  - 超过上限时的提示；
  - 是否遇到访问限制。
- [ ] 11.5 归档前：若 11.4 未完成，转入 #166 或新的验收 Issue，不随归档丢失
