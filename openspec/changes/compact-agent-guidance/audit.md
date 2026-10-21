# CLAUDE.md 精简的逐段去向审计

- 基准：v2 `ef69c60` 加执行前已存在的 1692 行 CLAUDE.md 工作树内容。
- 基准本地差异保存在同目录 `baseline.patch`，为零上下文补丁（应用时用 `git apply --unidiff-zero`），可与 `git show ef69c60:CLAUDE.md` 重建原文并核对下述哈希。
- 原文件 SHA256：`a812afdc77c02566d62969f8001274a3d399c8ea93514598834101387fc89bc0`。
- 原有本地修改包括部署代触发授权、维护纪律与 V1 UPSERT 描述纠错，均保留语义，不覆盖原主工作区。
- 授权依据：用户批准 800 行以内的分层压缩方案并要求执行；下面处置均在该范围内。
- 核对层级：逐段文档迁移与高风险红线核对；未重新证明每项代码、生产状态或真机行为。
- 行号均指上述冻结基准，代码块内以空行分开的片段也逐项登记；每个非空原文行都有去向。
- 主文件为全部任务首读，各域全文是命中任务的必读；原文中的历史反例继续留在指南，未批量删掉。

## 逐段清单

| 原行号 | 原条目起始文本 | 处置 | 当前去向 | 确认/执行 |
|---|---|---|---|---|
| 1–1 | # CLAUDE.md | 压缩 | CLAUDE.md 开工入口 | 已批准范围内／已执行 |
| 3–6 | 本文件是这个仓库的开发约定与设计记录，**面向所有在这里干活的智能体**，不只是 Claude Code | 压缩 | CLAUDE.md 开工入口 | 已批准范围内／已执行 |
| 8–8 | ## Project Overview | 压缩 | CLAUDE.md 开工入口 | 已批准范围内／已执行 |
| 10–10 | KeywordIQ - A professional Amazon search term data analysis platform support | 压缩 | CLAUDE.md 开工入口 | 已批准范围内／已执行 |
| 12–15 | **Import data volumes (critical for performance decisions):** | 压缩 | CLAUDE.md 开工入口 | 已批准范围内／已执行 |
| 17–17 | ## Development Commands | 去重/引用 | CLAUDE.md 项目与命令 | 已批准范围内／已执行 |
| 19–22 | bash | 去重/引用 | CLAUDE.md 项目与命令 | 已批准范围内／已执行 |
| 24–25 | # Run tests | 去重/引用 | CLAUDE.md 项目与命令 | 已批准范围内／已执行 |
| 27–29 | # Add a dependency | 去重/引用 | CLAUDE.md 项目与命令 | 已批准范围内／已执行 |
| 31–42 | # Production deployment (Docker Compose: app + self-hosted PostgreSQL + Netd | 去重/引用 | CLAUDE.md 项目与命令 | 已批准范围内／已执行 |
| 44–46 | # Database operations | 去重/引用 | CLAUDE.md 项目与命令 | 已批准范围内／已执行 |
| 48–58 | # Database migrations (Alembic)——本地开发直连数据库时用这组； | 去重/引用 | CLAUDE.md 项目与命令 | 已批准范围内／已执行 |
| 60–60 | ## V2 数据模型 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 62–65 | V1 已完成使命并退役，V2 是完整重建。数据模型、导入、查询、界面均已落地，V2 已在阿里云 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 67–67 | **V2 的表**（星型模型，全部在 analysis schema 下）： | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 69–78 | / 表 / 说明 / | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 80–81 | 分区表的注册表是 partitioning.PARTITIONED_TABLES（表名 → PartitionScheme(date_column, s | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 83–83 | **三条不可违反的约束**： | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 85–96 | 1. **事实表只追加，不原地更新。** 排名、上期排名、排名变化都在写入时算定。 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 98–100 | **迁移注意**：分区表与物化视图不在 SQLAlchemy 的 DDL 生成范围内，autogenerate 会把 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 102–105 | **新增模型必须登记进 migration_scope.py 的 V2_TABLES**（alembic/env.py 的 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 107–112 | **列注释参与 autogenerate 比对**，因此模型里的注释文本要与库里一字不差；改注释就要配 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 114–116 | **V2 查询层（site-scoped-query + window-scoped-rows，已落地）**：分析查询查 V2 星型模型， | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 118–122 | / / 天视图 / 周视图 / | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 124–126 | **行集合 = 在榜 ∪ 掉榜**：在榜 = 锚定期有数据；掉榜 = 行窗口内出现过但锚定期没有（读 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 128–131 | **但带筛选的路径仍然锁死在单一事实切片里。** 筛选一律以锚定期那一行为准,必然后果是 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 133–133 | 要点： | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 135–163 | - **日期选择器走 site_data_stats**（不在 8 亿行上实时聚合；在榜总数因类目黑名单已改为实算，见分析查询屏一节）；类目下拉走 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 165–167 | **关键词工具 · 持续增长市场（/api/keyword/growth/*，权限点 keyword:view）**：近 7 天名次 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 169–180 | - **只有日报**。周报没有「连续 7 天」，period='WEEK' 的导入不算定、不写表。 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 182–185 | 代价是空表格从两种成因变成**三种**——还没查 / 窗口缺期 / 口径下确实没命中——含义 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 187–189 | **关键词工具 · 我的词库（/api/keyword/library/*，权限点同为 keyword:view）**：用户自建的 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 191–211 | - **行集合由条目决定，事实表只补数**——与分析查询屏相反（那边行集合 = 在榜 ∪ 掉榜， | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 213–214 | 事实行的列投影由 analysis_crud.fact_metric_columns 提供，**两屏共用一份**；复制第二份 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 216–226 | **关键词工具 · 排名趋势详情（GET /api/keyword/trend，issue #86）**：单关键词的日/周排名 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 228–253 | - **五态分类，且不信任 site_data_stats 在保留下界之前的行。** retention_service | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 255–255 | ## 顶栏菜单：按数据域命名，不设按排除法命名的容器 | 迁出 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 257–262 | 顶栏八项，名称统一四字，顺序固定：**机会中心 → 市场分析 → 词库工具 → 竞对监控 → | 迁出 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 264–269 | **新增一级菜单的判据是「它读哪个数据域」，没有一项匹配就新开一个。** 这里曾有一个叫 | 迁出 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 271–275 | **单页顶层（/opportunity、/analysis、/competitor、/products、/pricing）不得加二级菜单。** 布局是 | 迁出 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 277–289 | **机会中心（/opportunity，issue #76）是承接"确定性 Signal → 候选机会 → 验证推进 → | 迁出 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 291–295 | **风险核查（/ip，审核待办 / 外观专利 / 商标查询 / 风险案例，原名「侵权查询」）只是占位屏**：仓库里没有 | 迁出 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 297–299 | **四字导航（2026-09-05）**：导航简称不强制缩短业务页面标题。「市场分析」页面仍可显示 | 迁出 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 301–302 | 产品档案（/products）与定价核算（/pricing）本轮只提供明确标注尚未接入的入口， | 迁出 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 304–310 | **占位屏仅超管可见（2026-09-20）**：产品档案、风险核查、定价核算三个一级菜单挂前端 access | 迁出 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 312–313 | 把它放进顶栏是自觉取舍：产品最空的一块因此成了最显眼的一块，换来的是「这块在做」这个 | 迁出 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 315–316 | 旧地址 /toolbox、/toolbox/growth、/toolbox/library 保留重定向；两个占位屏的旧地址 | 迁出 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 318–318 | ## 竞对监控（/api/competitor/*，权限点 competitor:view） | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 320–326 | 盯别人的店：把竞对卖家攒成关注清单，供后续的上新监控使用。**P0（关注清单）、P3a | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 328–329 | **插件的两处限制与一处已澄清的误解，改动前先读 extension/README.md 与对应变更的 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 331–342 | - **令牌是从已登录网页里同步来的 24 小时 JWT，全权限**（session-sync.ts 读网页 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 344–346 | **采集是「多页本地合并、一次上报」，不是逐页上报。** 逐页上报会让第一页单独落定该卖家的基线， | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 348–350 | **分发方式是 zip + 加载已解压，不上 Chrome Web Store。** pnpm run pack（extension/ | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 352–360 | - **版本号有两处，必须一致**：manifest.json 与 package.json。打包脚本与 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 362–362 | **两层数据模型，分层的判据是「这条数据属于谁」**： | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 364–371 | / 表 / 层 / 说明 / | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 373–373 | 六张都是普通表，不分区、不参与保留清理、不被导入路径写入。 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 375–376 | 分共享层的理由是抓取代价：后续上新监控要按卖家抓商品，**一个卖家被十个用户关注也只该 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 378–378 | **与词库（keyword_group）刻意不同的两点**，抄错了不会报错： | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 380–385 | - **分组不绑站点。** 这条后来还多兑现了一次：独立站监控落地时，同一个分组直接就能装 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 387–392 | **解析器 seller_ref_parser.py 是纯函数，书签导入与手工粘贴共用一份。** 用户从浏览器 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 394–397 | **站点识别的优先级是 marketplaceID > 域名 TLD > 调用方给的 fallback_site。** | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 399–399 | **导入是预览 → 确认两步，配额在预览阶段就判定。** 两条约束： | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 401–405 | - **超配额整批拒绝、不截断**，且判定发生在预览而非确认——让用户看完 2657 条的预览再告诉 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 407–410 | 预览暂存进表而不是 Redis，是为了不给这一个功能引入一个新的基础设施依赖；过期行清理 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 412–414 | **权限点 competitor:view 是独立的，不复用 keyword:view。** 与「外观专利」那种占位屏 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 416–416 | ### 竞对卖家 ASIN 上报（/api/competitor/asin-reports，issue #56 P3a 服务端侧） | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 418–421 | **推模型，不是拉模型。** 采集端（浏览器插件，不在本变更范围）主动 POST 它在某卖家页面 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 423–423 | **由此推出三条边界，缺一条都会在某个时候制造出一批安静的假数据：** | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 425–435 | 1. **不做下架判定。** "这次没见到"与"已下架"在数据上无法区分。与独立站「下架判定只在 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 437–445 | **基线 = 第一次上报，一旦落定不再改动。** 某卖家的首次上报把 baseline_at 落成本次 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 447–451 | **时间的权威只有服务端。** baseline_at 与 first_seen_at 一律取服务端事务时间；上报体 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 453–453 | **幂等靠两把锁，缺一把只挡住一半：** | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 455–466 | - request_id（客户端生成，**必填**，按 (user_id, request_id) 唯一）——命中即回放原 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 468–471 | **每行在售关系记录首次引入它的上报**（first_report_id，ON DELETE SET NULL）。共享层 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 473–476 | **上报只接受上报者本人已关注的卖家，整个请求拒绝（不是逐条）。** 一次上报只对一个 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 478–482 | **单条 ASIN 格式非法逐条拒绝、其余照写；条数超限整批拒绝、不截断。** 判据是"这样做会不会 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 484–488 | **零条有效 ASIN（请求为空，或全部条目因格式非法被逐条拒绝）整批拒绝（422），MUST NOT | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 490–492 | **非法站点码返回 422，不是 500。** normalize_site_code 抛 UnknownSiteError， | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 494–507 | **上新流在 SQL 里合并两类来源，不在应用层归并两页**（store_crud.new_arrivals_stmt 对 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 509–512 | **「点开一行看详情」不在本变更范围内**（两类来源都没有这个交互，不是 Amazon 分支特有的 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 514–516 | **competitor_seller_asin/competitor_asin_report 不参与按月保留清理**（不登记进 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 518–522 | **配送地区仍不用于过滤或存活判定，只留档。** 实测 Amazon 店铺搜索结果按操作者账号收货 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 524–524 | ### 独立站监控（Shopify，/api/competitor/stores*） | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 526–527 | 与 Amazon 卖家**共用分组、权限点与上新流，不共用表与字段**。分的是数据与采集，合的是 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 529–531 | **为什么先做这条**：它没有未决前提。/products.json 是公开端点，服务端一个 HTTP 请求即可， | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 533–539 | / 表 / 粒度 / 写入模式 / | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 541–543 | 五张普通表，**MUST NOT 登记进 PARTITIONED_TABLES**——登记即被保留清理按 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 545–545 | **七条不可违反的约束**，每条都对应一个「错了不会报错」的失败形态： | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 547–568 | 1. **下架判定只在 catalog_complete 为真时做。** 完整 = 每页 200 + 翻页因空页自然终止 + | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 570–576 | **加入入口有两个，共用一份域名规范化**：单条表单（长期主路径）与批量粘贴（一次性初始 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 578–581 | **拉取挂在 import_queue.py 已有的轮询循环上**，按 last_fetch_at 状态列认领 | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 583–584 | **本变更不做取关后的 GC。** issue #56 定过「保留 7 天」，但那是按每天全量轮询的量写的； | 迁出 | openspec/guides/competitor.md | 已批准范围内／已执行 |
| 586–591 | **数据保留：只保留最近三个月。** 由 HOT_WINDOW_MONTHS（默认 3）定义热窗口，报告日期 | 迁出/纠错 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 593–599 | **分区移出必须同时清理 site_data_stats/category_stats/brand_stats 的对应残留 | 迁出/纠错 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 601–604 | **扩容触发条件**（数据从上线起约 6600 万行/月、16 GB/月增长）：磁盘使用率超过 70%， | 历史化/纠错 | 本审计历史容量段；CLAUDE.md 部署与运维 | 已批准范围内／已执行 |
| 606–606 | ## Architecture | 压缩 | CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 608–608 | ### Module Structure | 压缩 | CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 610–622 |  | 压缩 | CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 624–629 | ops_bot/               # 飞书运维机器人：独立于 app/ 的 host 级 systemd 服务（见 Production E | 压缩 | CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 631–631 | ### Layered Architecture Pattern | 压缩 | CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 633–633 | **All table modules follow strict layering:** | 压缩 | CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 635–639 | 1. **Model** (*_model.py): SQLAlchemy 2.0 ORM models using DeclarativeBase,  | 压缩 | CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 641–641 | **Example**: app/table/analysis/ - analysis_api.py → analysis_service.py → a | 压缩 | CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 643–643 | ### Data Import Flow | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 645–645 | **V2 OSS-first 导入（现行，日报周报同一套流程）**：表格文件**先直传 OSS，再从 OSS 导入**。 | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 647–658 | 1. **签发直传凭证** POST /oss/upload-credentials（upload_api.py → oss_client.issue_ | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 660–671 | **维度补齐只在属性真变时才写。** dim_product 的 UPSERT 带 | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 673–675 | **object key 约定**：<OSS_KEY_PREFIX>/<SITE>/<PERIOD>/<report_date>/<uuid>_<原文件 | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 677–681 | **配置**：OSS_*（见 .env.example）；OSS_ENABLED=false 时走本地文件退路 | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 683–686 | **bucket 的 CORS 不在本仓库配置范围内，但直传依赖它**：来源需含前端域名、方法需含 | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 688–692 | **上线前必改**（开发环境放宽处，清单与理由见 .env.example 的 OSS 段）：CORS 来源从 * | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 694–695 | **导入串行**：pg_try_advisory_lock 保证同一时刻只有一个导入在跑（2C4G 上两个 GB 级导入并发会争抢 | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 697–700 | **QUEUED 必须真的会被接手**，否则它就是个死标签。接手者是 app/table/upload/import_queue.py | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 702–702 | 因此两条约束： | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 704–708 | - **接手用轮询，不用「导入结束时接着跑下一个」**。链式接力在进程被杀、导入崩在半路、或前一个 | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 710–714 | **UPLOADING 适用同一条纪律**（import_service.reap_stale_uploads，挂在同一个轮询任务上）。 | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 716–719 | - **在** → 字节传完了、只是没触发导入（关标签页的典型形态）：置 QUEUED 交给上面那个接手者， | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 721–726 | **导入批次有两个读取出口，职责不同、不要合并**：上传屏的「最近导入状态」看板 | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 728–729 | **重导**（POST /batches/{id}/retry，权限点同为 upload:import）凭批次留存的 object key | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 731–736 | - **只允许 FAILED。** 重导会先删该 (站点, 周期, 期次) 的现有切片再重写；对 COMPLETED | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 738–742 | 准入判定（不存在 404 / 非 FAILED 409 / object_key 空 422 / 前缀越权 400 / 对象已删或超限） | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 744–745 | > V1 的 csv_processor.py（pandas 分块 + ON CONFLICT (keyword) UPSERT + ranking_t | 迁出 | openspec/guides/ingestion.md | 已批准范围内／已执行 |
| 747–747 | ### Authentication & Authorization | 迁出 | openspec/guides/auth.md | 已批准范围内／已执行 |
| 749–764 | - **自建 JWT + RBAC**（fastapi-user-auth/fastapi-amis-admin 已随 amis 于阶段七移除）。 | 迁出 | openspec/guides/auth.md | 已批准范围内／已执行 |
| 766–772 | 两条历史教训写在这里，别再走回去：**（1）** 曾有 user:manage 权限点，收紧后它成了 | 迁出 | openspec/guides/auth.md | 已批准范围内／已执行 |
| 774–797 | 代价是**能管用户的唯一判据就是 is_super**：若唯一超管的该位被误改为 false，界面上 | 迁出 | openspec/guides/auth.md | 已批准范围内／已执行 |
| 799–801 | **钉钉单点登录（/api/auth/sso/*，全部免鉴权）**：员工扫码即可登录，不必在本系统单设 | 迁出 | openspec/guides/auth.md | 已批准范围内／已执行 |
| 803–835 | - **这套不是 OIDC，尽管授权参数里的 scope 叫 openid。** 钉钉换回来的是 accessToken， | 迁出 | openspec/guides/auth.md | 已批准范围内／已执行 |
| 837–842 | 前端入口在登录页（pages/Login）与过场屏 pages/SsoCallback（路由 /sso/callback， | 迁出 | openspec/guides/auth.md | 已批准范围内／已执行 |
| 844–848 | **回调域名配置与真机扫码无法靠 code review 确认**，用 uv run python scripts/check_dingtalk_sso | 迁出 | openspec/guides/auth.md | 已批准范围内／已执行 |
| 850–851 | **后台 UI 是独立的 AntD Pro SPA**（frontend/，Umi Max + Ant Design + ProComponents）， | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 853–854 | **分析查询屏（frontend/src/pages/Analysis/）分三段**：顶部作用域栏（站点/视图/日期/检索框/ | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 856–863 | **状态编排拆成三个 hook，依赖方向单向**（issue #69 阶段二）：useAnalysisSession（会话快照、 | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 865–868 | - **首屏按默认作用域自动查一次**。进屏先拉站点、作用域与类目（选择器的前提），拿到结果 | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 870–873 | **默认值只有一处定义**：filters.ts 的 defaultFilters()，初始草稿、首屏自动查询、 | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 875–876 | **条件初始化有三级优先级**（savedMode.ts），高者胜出：**会话快照 > 用户保存的条件 > | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 878–882 | / / 存活期 / 存在哪 / 谁清它 / | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 884–886 | 保存的条件（「保存当前模式」，对标卖家精灵）存**站点 + 视图 + 筛选 + 排序**， | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 888–898 | - **它必须活过 doLogout，这不是漏清。** 会话快照是**无主的单份**，换账号会串，只能清； | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 900–902 | **首屏按保存的作用域初始化时，reloadScopeMeta 必须带 keepCategories**。它平时会清空 | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 904–905 | 首屏这次查询**带筛选**，两个后果都是既有语义、不是本屏特例：总数走 count_filtered | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 907–910 | 自动查询**只发生在无会话缓存的首次进入**（if (restored) return; 那道闸之后），切走 | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 912–1027 | 站点一期数据都没有时不发请求，此时空表格**必须说明是没有数据**（scopeHasNoDates）， | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 1029–1039 | **competitorNew 必须计入前端 FILTER_KEYS**（useAnalysisQuery.ts）——它决定「本次结果不含 | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 1041–1045 | - **结果表格可勾选行并批量「加入词库」**（唯一的入库入口，见上文词库一节）。可选分组只列 | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 1047–1048 | 条件转换逻辑在 filters.ts（纯函数，vitest 直接测）；展示组件 ScopeBar / CategoryPanel / | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 1050–1053 | **切走再切回不刷新，这是约定不是缺陷。** umi 4.6 没有 keepalive，路由一变这个组件真的被 | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 1055–1063 | - **短路只作用于复原后的第一次请求**（一次性）。写成「参数相同就用缓存」会把同参数的 | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 1065–1068 | 滚动位置一并恢复，且**必须等结果行落到 DOM 之后**——行没渲染时页面高度不足，scrollTo | 迁出/纠错 | openspec/guides/frontend.md | 已批准范围内／已执行 |
| 1070–1070 | ### 错误契约（统一,不可绕过） | 压缩 | CLAUDE.md 认证与错误契约 | 已批准范围内／已执行 |
| 1072–1072 | **所有失败响应只有一种形状**,由 app/common/error_handlers.py 的五类处理器统一产出: | 压缩 | CLAUDE.md 认证与错误契约 | 已批准范围内／已执行 |
| 1074–1076 | json | 压缩 | CLAUDE.md 认证与错误契约 | 已批准范围内／已执行 |
| 1078–1091 | - **业务错误一律「抛」不「返」**。服务层抛 app/common/errors.py 的 APIError 子类, | 压缩 | CLAUDE.md 认证与错误契约 | 已批准范围内／已执行 |
| 1093–1093 | 两条容易踩的边界: | 压缩 | CLAUDE.md 认证与错误契约 | 已批准范围内／已执行 |
| 1095–1098 | 1. **流式端点(如导出)的失败必须在开流前判定。** 一旦返回 StreamingResponse,状态码与响应头 | 压缩 | CLAUDE.md 认证与错误契约 | 已批准范围内／已执行 |
| 1100–1103 | 前端侧:src/app.tsx 的 errorConfig.errorHandler 是**错误提示的唯一出口**,页面层不写 | 压缩 | CLAUDE.md 认证与错误契约 | 已批准范围内／已执行 |
| 1105–1105 | ### Database Design | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 1107–1107 | **Schema**: analysis | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 1109–1111 | 表结构见上文「V2 数据模型」一节（星型模型：dim_keyword / dim_product / | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 1113–1116 | **category_stats / brand_stats**：普通表，按 (站点, 周期, 日期) 分片提供类目/品牌 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 1118–1118 | ### Performance Optimizations | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 1120–1124 | 1. **Connection Pooling**: database.py 在模块级建了**两个**引擎，各自持有独立的池， | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 1126–1129 |  | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 1131–1137 | start.sh/Dockerfile 固定 4 workers。当前取值 (2+3)×4 + (8+8)×4 = 84，为 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 1139–1162 | **THREAD_POOL_LIMIT（默认 32）与连接池无关，调池时不要跟着一起动。** anyio 的默认 | 迁出 | openspec/guides/analysis.md | 已批准范围内／已执行 |
| 1164–1164 | ## Key Configuration (.env) | 去重/引用 | .env.example/config.py；CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 1166–1166 | 完整项与说明见 .env.example（新增配置务必同步过去，含单位与安全的本地默认值）。 | 去重/引用 | .env.example/config.py；CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 1168–1172 | bash | 去重/引用 | .env.example/config.py；CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 1174–1177 | # Sites / retention | 去重/引用 | .env.example/config.py；CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 1179–1184 | # 钉钉单点登录。SSO_ENABLED=true 时前三项缺一即启动失败；关闭时端点一律 404 | 去重/引用 | .env.example/config.py；CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 1186–1189 | # 持续增长市场的判定口径（仅日报）。改了要跑 scripts/backfill_growth.py 重算历史期次 | 去重/引用 | .env.example/config.py；CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 1191–1193 | # 词库上限（超限 422 拒绝、整批不写）。每组条目数同时是盯盘查询的规模保证，上调前先重测 | 去重/引用 | .env.example/config.py；CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 1195–1198 | # Upload / OSS（OSS_ENABLED=false 时走本地文件退路，其余 OSS_* 可留空） | 去重/引用 | .env.example/config.py；CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 1200–1205 | # Connection pool（两个引擎各自独立配置，上限是两组相加再 × 4 workers； | 去重/引用 | .env.example/config.py；CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 1207–1208 | # 线程池上限（导出/探活/bcrypt/同步后台任务共用，与连接池无关） | 去重/引用 | .env.example/config.py；CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 1210–1218 | # 自建 PostgreSQL 容器（docker-compose.yml 的 db 服务读取，不经 config.py） | 去重/引用 | .env.example/config.py；CLAUDE.md 分层与通用编码约束 | 已批准范围内／已执行 |
| 1220–1220 | ## Production Environment | 迁出 | openspec/guides/operations.md | 已批准范围内／已执行 |
| 1222–1224 | **服务出问题排查看 etc/observability-runbook.md**（怎么用 request_id 关联 nginx/ | 迁出 | openspec/guides/operations.md | 已批准范围内／已执行 |
| 1226–1229 | / Category / Spec / | 迁出 | openspec/guides/operations.md | 已批准范围内／已执行 |
| 1231–1233 | 应用与数据库不再是两台独立机器——dockerize-selfhosted-infra 变更之前是阿里云 RDS | 迁出 | openspec/guides/operations.md | 已批准范围内／已执行 |
| 1235–1236 | **V2 上线拓扑（2026-09-19）**——细节与实时状态以 docs/ops/v2-ecs-handoff.md 为准，这里只 | 迁出 | openspec/guides/operations.md | 已批准范围内／已执行 |
| 1238–1252 | - **V1 与 V2 同机、完全隔离。** V1 仍是裸部署（域名 amazon.liangtong.online、后端 8000）， | 迁出 | openspec/guides/operations.md | 已批准范围内／已执行 |
| 1254–1257 | **飞书运维机器人（ops_bot/）是与业务 app 完全独立的 host 级 systemd 服务** | 迁出 | openspec/guides/operations.md | 已批准范围内／已执行 |
| 1259–1276 | - **它崩溃、重启不影响业务 app，反之亦然**；app/ 不 import 它，它也不依赖 app.*。 | 迁出 | openspec/guides/operations.md | 已批准范围内／已执行 |
| 1278–1291 | **Capacity implications for code changes:** | 迁出 | openspec/guides/operations.md | 已批准范围内／已执行 |
| 1293–1293 | ## Important Constraints | 压缩 | CLAUDE.md 分层与通用编码约束、V2 数据模型 | 已批准范围内／已执行 |
| 1295–1300 | 1. **SQLAlchemy 2.0 Only**: Use DeclarativeBase, Mapped, mapped_column, expl | 压缩 | CLAUDE.md 分层与通用编码约束、V2 数据模型 | 已批准范围内／已执行 |
| 1302–1302 | ## Development Guardrails | 压缩 | CLAUDE.md 分层与通用编码约束、V2 数据模型 | 已批准范围内／已执行 |
| 1304–1309 | - **Database migrations**: Schema changes must go through Alembic migrations | 压缩 | CLAUDE.md 分层与通用编码约束、V2 数据模型 | 已批准范围内／已执行 |
| 1311–1311 | ## 流程重量：先问，别默认最重 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1313–1313 | **开工先问一句这件事要多重，默认取最轻的那档。** 三档： | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1315–1319 | / 档位 / 做什么 / 适用 / | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1321–1322 | 判据是**这个决策以后还要不要被翻出来看**，不是改动大小。「账号按 unionId 还是 userid 匹配」 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1324–1324 | ### 为什么要有这条 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1326–1328 | 2026-08-19 的钉钉 SSO 变更（issue #36）事后统计：生产代码 949 行、测试 1106 行、 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1330–1333 | 原因不是哪一步做错了，而是**默认把所有重流程叠满**：brainstorming 判成 architectural → | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1335–1335 | ### 四条具体约束 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1337–1339 | 1. **澄清问题一次问完。** 一个 AskUserQuestion 最多带 4 个问题，**不要串行问 4 轮**—— | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1341–1344 | 2. **篇幅按「反事实是否显然」定，不要一律拉满。** 本文件这种「每条都解释不这么做会怎样」 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1346–1349 | 3. **先验前提，再设计。** 有没有那个租户 / 权限 / 数据源 / 收不收费——先问或先查。 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1351–1354 | 4. **测试按影响面跑，全量只在提交前一次。** 前端全量约 97 秒且随用例数增长； | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1356–1356 | ## 跨 Agent 交付流程：Issue 是交接契约，PR 是审核包 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1358–1360 | 默认由用户在 **Codex 桌面端**讨论需求；讨论收敛后，先创建 GitHub Issue，再由 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1362–1362 | 完整流程如下；小改动仍按上一节的「直接做」或「先对齐再做」分流，不为流程而流程： | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1364–1368 | text | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1370–1370 | ### 1. Issue：跨工具的唯一交接物 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1372–1375 | Issue 不得只有标题或聊天摘要；至少写明**背景与目标、可验证的验收条件、非目标、已决定的 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1377–1377 | ### 2. 实现与 PR：把契约和证据一并交付 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1379–1381 | Claude Code 先按 Issue 判断流程重量；需要长期留档的变更走 OpenSpec，并遵守下文的认领与 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1383–1383 | #### Claude Code 代发 @codex review | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1385–1388 | Codex 评审由 PR 下的 @codex review 评论触发（GitHub 上的 Codex 应用响应，评论作者是用户自己的 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1390–1391 | 代发前先核对：PR 存在且 base 为 v2；本地提交已推送，PR 的 headRefOid 等于要评审的提交； | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1393–1395 | bash | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1397–1398 | 可在评论里补一两行"请重点看"的范围，不贴聊天或日志。发完只报告评论链接，不轮询等待评审结果 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1400–1400 | ### 3. 审核与修复：独立视角，但不越权改分支 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1402–1405 | Codex 审核 Issue / OpenSpec / PR diff 是否一致，并检查数据正确性、权限边界、迁移与测试缺口。 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1407–1407 | ### 4. 合并与收尾：v2 的 Issue 要手动关闭 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1409–1411 | 审核通过后才合并进 v2。GitHub 只会在合并到默认分支时处理 Closes #...，而默认分支是 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1413–1413 | ## 工作分支是 v2，不是 main | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1415–1416 | V1 已完成使命并退役，V2 是完整重建（见上文「V2 数据模型」）。**所有工作基于 v2**， | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1418–1418 | 两条直接后果： | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1420–1422 | - **开分支从 v2 拉**（git worktree add -b "change/$ID" ... v2）。 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1424–1425 | 同理，Closes #<n> 在合并进 v2 时**不会触发关闭**（GitHub 只在合并进默认分支时关 issue）， | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1427–1429 | **V2 不逐个发布**：各变更合进 v2 后不单独上线，整体做完再一次性发布。列剩余工作时不要 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1431–1434 | **用户在对话里明确要求发布时（如「发布」「部署到服务器」），Claude Code 直接代为触发** | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1436–1441 | - 目标 SHA 是 origin/v2 最新提交，本地无未推送提交； | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1443–1445 | 触发用 gh workflow run "Deploy V2 to ECS" --ref v2，随后 gh run watch 到结束， | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1447–1447 | ## 并行会话：一个变更一个 worktree | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1449–1450 | **这个仓库经常同时有多个会话在干活。** 默认假设不是「只有我一个人」。 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1452–1452 | ### 一个变更一个 worktree，路径在仓库外 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1454–1459 | 每个 openspec 变更配一个分支 change/<变更id> 和一个 worktree，路径 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1461–1461 | worktree 解决的是**共享索引与共享工作树**这两类碰撞： | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1463–1464 | - git commit 提交的是整个索引，别的会话 staged 的文件会被一并带走（真实发生过） | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1466–1466 | ### worktree 解决不了的三类，要另外守 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1468–1473 | 1. **重复劳动。** 两个 worktree 不阻止两人做同一件事，只会把撞车从「改到一半」 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1475–1481 | **反过来也成立：跑一次全量 pytest 会清空这个库里的业务数据。** | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1483–1487 | 同理，集成测试里的计数**必须带作用域**（WHERE site=... AND report_date=...）。 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1489–1489 | ### 提交前看索引列 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1491–1493 | git status --short 的**第一列是索引**。看到不属于本次改动的条目，用 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1495–1500 | **门禁会拦一半。** 索引与工作树对同一文件不一致时（暂存后又改了、或暂存后从磁盘 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1502–1503 | 门禁**拦不住**的那一半：索引里那些与你无关、内容又和磁盘一致的文件（别的会话暂存 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1505–1515 | > **「暂存区是空的」这件事本身漏过两次，成因不同。** 一次是查错了仓库：会话在 | 迁出/压缩 | openspec/guides/workflow.md；CLAUDE.md 流程重量 | 已批准范围内／已执行 |
| 1517–1517 | ## CLAUDE.md 的维护纪律（issue #142） | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1519–1521 | 本文件保留跨变更仍有效的约束与必要理由；一次排障的过程、日志和已完成任务留在共享记录。 | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1523–1523 | ### 经验晋升：一般经验复发后晋升，高风险约束首次即可进入 | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1525–1537 | - 一般经验首次出现时，先记在可共享检索的 GitHub Issue，或随版本管理的 | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1539–1539 | ### 条目审计：按证据处置，不能把工具存在当作覆盖完整 | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1541–1542 | 用户手动发起、大版本收尾，或发现规则与当前实现冲突时开展审计；不默认创建定时任务。 | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1544–1549 | 1. 对应能力、代码与约束现在是否仍存在？是否与本文件其他条目、主 specs 或当前实现冲突？ | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1551–1551 | 审计按以下判据提出四种处置： | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1553–1558 | / 处置 / 判据 / | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1560–1563 | 产出共享删改清单，每项列原章节/条目、证据、建议处置、目标位置与确认状态；执行后补实际 | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1565–1565 | ### 完整流程的覆盖核对：需求、任务、实际改动与验证证据相互对应 | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1567–1569 | 仅约束本节生效后的新完整流程变更；「直接做」「先对齐再做」两档及已归档变更不强制补矩阵。 | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1571–1574 | text | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1576–1577 | 迁移、重构、测试、文档等支撑任务注明其服务的需求或已批准设计决策，不为凑对应关系虚构 | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1579–1579 | **PR 审核、合并前完成双向核对**，归档前再核对最终结果： | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1581–1589 | 1. 从已批准的需求与场景查任务、实际改动及验证证据，区分已实现、已验证和待人工验收； | 压缩 | CLAUDE.md 维护纪律 | 已批准范围内／已执行 |
| 1591–1591 | ## 开发 harness（提交门禁） | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1593–1594 | **开工先 openspec list。** 手上的活儿很可能正是某个在飞变更里没勾完的任务， | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1596–1596 | ### 提交门禁 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1598–1601 | git commit 之前会自动按改动范围跑验证，不通过就拒绝提交。挂在 PreToolUse 上 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1603–1608 | / 改动范围 / 跑什么 / 耗时 / | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1610–1613 | **这些检查并发跑**（run_checks），墙钟是最慢那条而不是各条之和：前后端都改时实测 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1615–1617 | 本地 keywordiq_test 连得上时会自动带上 TEST_DATABASE_URL 解开集成测试，连不上就 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1619–1621 | 每条检查有 300 秒超时，超时按失败处理。卡住的 pytest（抢咨询锁、半开连接）若没有 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1623–1623 | ### 铁律检查在哪 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1625–1625 | 分两处，**不重叠**： | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1627–1631 | - **ruff**（pyproject.toml 的 \[tool.ruff.lint\]）——能靠「看一个文件」判定的： | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1633–1634 | 新增铁律前先问「ruff 能不能查」，能就别手写。手写的规则**必须自带正反 fixture** | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1636–1636 | ### 基线棘轮 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1638–1640 | 规则建立之前就存在的违规登记在 test/conventions_baseline.toml，被放行，好让门禁 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1642–1644 | **基线当前是空的**——建立规则时登记的三笔债已全部清偿。空基线是常态，不是终点： | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1646–1646 | 已清偿的三笔（留作参照，说明这类问题长什么样）： | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1648–1653 | - **user_crud.py 的返值式错误**——写方法返回 (success, message, user) 三元组， | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1655–1662 | - **MAX_FILE_SIZE 曾是死配置**——.env.example 与本文件都公示 3GB 上传上限， | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1664–1664 | ### 项目 skill | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1666–1673 | - **start-change** —— 开始或接手一个变更时先跑它：认领（防止与并行会话重复劳动）、 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1675–1675 | ### 项目 command（手动调用） | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1677–1677 | 交付、修复与合并由用户分别触发；流程文件放在 .claude/commands/，不另建同名 skill。 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1679–1687 | - **\[/deliver-pr\](.claude/commands/deliver-pr.md)** —— 按约定检查、commit、push， | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1689–1689 | ## 当前部署交接 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |
| 1691–1692 | V2 ECS、OSS 与飞书运维机器人的实时交接状态统一记录在 | 迁出/去重 | openspec/guides/workflow.md；CLAUDE.md 验证与开发 harness | 已批准范围内／已执行 |

## 已核实纠错和历史化

1. 旧 601–604 的“4C16G / RDS 2C4G 足以承载”属于已退役部署的容量估算，不迁为现机保证。
   磁盘>70%/缓存命中<95%保留为历史评估触发参考，是否扩容须按当前 4C8G 同机实测。
2. 旧 1591 后基线棘轮中的“当前 240、是否降到 160”不是现配置。历史债务保留概述，当前公式
   统一引用业务84+ops12=96/PG120；根据 config.py、database.py、ops_bot/database.py、Compose 核对。
3. OpenSpec context 的 RDS/400、MAX_WORKERS、BATCH_SIZE 旧参数改为主入口与当前配置引用。
4. 前端指南把“插件未上线/P3b客户端未做”改为“被动采集已实现，后台巡检未实现，采集覆盖不足”，
   与迁移前竞对章节 P3a 已实现的声明对齐，不对本次真实采集能力额外作证明。
5. 保留清理段把“统计表供无筛选总数”收窄为日期/掉榜/选项，在榜总数按黑名单实算，避免与查询章节冲突。
6. 已归档变更的 design 引用解析到现有 archive 路径；AGENTS 只修篇幅及附录入口，不改审核规则。

## 高风险保留核对

- 四事实只追加、同切片重导、ensure 分区、站点唯一来源、变化才写：主文件与分析/导入指南。
- 迁移白名单/手写分区/注释、保留统计/索引/锁：主文件与分析指南。
- 历史索引/锚点/五态、首次证据/顺序无关、私有权限：主文件与分析/前端指南。
- 单一签发入口、返回本地 session_id、条件 logout、前端异步令牌比较：主文件与认证指南。
- catalog_complete、新发现基线、零有效上报拒绝、完整排序键、插件一次上报/key：主文件与竞对指南。
- V1 隔离、V2 工作流三门、无自动备份、ops 互斥与池预算：主文件与运维/流程指南。
- 用户原部署授权、提交/评审/合并分开、并行会话/共享库、人工验收转移：主文件与流程指南。
- 三个维护机制、高风险首次晋升、真实 diff 反查、主文件≤800 与按域必读：主文件。

## 验证

- 主文件：1692 → 433 行（减少 74.4%），74309 → 18937 Unicode 字符（减少 74.5%）。
- 最长行：375 → 112 字符；没有通过拼长行、去空行或只改排版达标。
- 原文 284 个连续非空段、1409 个非空行均且仅有一次去向登记；领域正文与原段逐段 diff，差异为上列纠错、标题/交叉引用与旧基线说明去重。
- 15 个 Markdown 文件的本地链接存在性核对通过；现有归档 design 引用已修复；新文件 Git ignore 检查通过。
- AGENTS 的 Code Review Rules 及之后内容与原文逐字节一致；此前部署授权在 workflow 指南保留原文。
- `openspec validate --strict --all --no-interactive`：36 passed，0 failed。
- 复用已有环境运行 `pytest test/test_conventions.py -q`：15 passed（0.30 秒），无数据库/生产访问。
- `git diff --check`：通过；原主工作区 CLAUDE.md SHA256 与精简前一致，未覆盖原本地修改。
- 本记录为提交前文档自检；提交/推送状态以 PR 为准。PR/SHA 级外部审核与跨 Agent 真实使用验证尚未发生，不将自检当作外部审核。
不执行生产/真机/业务全量验证；不据此关闭 #142。
