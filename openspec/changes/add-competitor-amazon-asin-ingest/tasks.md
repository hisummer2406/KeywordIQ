# 任务

> 进度留痕：并行会话只能通过这里知道做到哪了。勾选与说明必须一致——勾了 `[x]` 却在尾注写
> 「待运行」，读的人无从判断到底跑没跑。

> 2026-09-01 第一轮修订：措辞改「新发现」、加幂等键与溯源列、把输入约束写进契约。
> 2026-09-01 第二轮修订（复核意见）：幂等键补 `request_hash`（防同键异体被静默吞掉）；
> 「已暂停」**代码真的删掉了**——上一轮只改了 spec，§4.2 当时仍是 `[ ]`，而 issue 回复
> 却说成已删，这是勾选与说明不一致的反面教材。§1 与 §4.2 已按修订后重做。
> 2026-09-01 第三轮（复核意见）：摘要范围**改为把 `captured_at`/`ship_to_region`/
> `page_context` 纳入**（原先排除，理由「重读 DOM 后重试会撞假 409」站不住——那不是重试）；
> §2 上报端点与 §5.1–5.4 已实现并跑通，含真实数据库集成验收（786 passed）。
> 2026-09-02：§3 全部完成（3.1/3.2/3.3，两来源合并上新流 + 卖家清单两指标，含真库验收
> 805 passed）；§4 全部完成（4.1/4.3/4.4，含一条"这不是本变更范围"的诚实标注：详情视图
> 对两类来源都未实现，不是 Amazon 分支特有的缺口）；§5.6 前端测试完成（374 passed）。
> 剩 §6：CLAUDE.md 一节 + 开 PR。

## 1. 数据模型与迁移

- [x] 1.1 新建 `app/table/competitor/asin_model.py`：`CompetitorAsinReport`（上报元数据：
      `user_id` / `request_id` / `seller_id` / `site` / `reported_at` / `captured_at` /
      `request_hash` / `item_count` / `new_count` / `ship_to_region` / `page_context`）与
      `CompetitorSellerAsin`（主键 `(seller_id, site, asin)`，含 `first_seen_at` /
      `first_report_id` / `title` / `image_url`）。**报表类必须定义在前**——在售关系的
      `first_report_id` 指向它。列注释按模块惯例写全，站点码注释复用
      `column_comments.SITE_COLUMN_COMMENT`
- [x] 1.2 建索引与约束：`idx_competitor_seller_asin_new (seller_id, site, first_seen_at)`
      支撑上新流的窗口过滤与排序；`idx_competitor_asin_report_latest (seller_id, site,
      reported_at)` 支撑「最近采集于」；`uq_competitor_asin_report_request
      (user_id, request_id)` 是幂等键——**按用户分域不是全局**（跨用户撞键会吞掉后者的
      上报并回放前者的计数）；`request_hash` 列是配套的第二道锁——命中键后再比对摘要，
      不一致返 409，防止同键被复用于另一次上报而静默吞掉
- [x] 1.3 两张表登记进 `migration_scope.py` 的 `V2_TABLES`（漏登 autogenerate 既看不见
      也不报错）；**确认没有**登记进 `partitioning.PARTITIONED_TABLES`
- [x] 1.4 `uv run alembic revision --autogenerate -m "竞对卖家在售 ASIN 与上报记录"`，
      人工核对生成的 DDL：两张普通表、schema 为 `analysis`、列注释与模型一字不差、
      建表顺序是 report 在前、**不出现** `PARTITION BY`（迁移 `ba67eac57b82`）
- [x] 1.5 `uv run alembic upgrade head`；对迁移后的库再跑一次 autogenerate 探针，产出为
      空（`upgrade()` 只有 `pass`），探针文件已删除
- [x] 1.6 在测试库上跑 `test_v2_integration.py::test_models_match_migrated_schema` —— 通过。
      **顺带修了一个真缺陷**：该用例逐个 import 模型模块（与 `alembic/env.py` 同样靠 import
      的副作用注册进 `Base.metadata`），`asin_model` 漏在名单外，表现是 autogenerate 反过来
      要 DROP 这两张新表。登记进 `V2_TABLES` 只让它对 `include_object` 可见，**不等于
      被 import 到 metadata**——两件事都要做

## 2. 上报端点

- [x] 2.1 `asin_schemas.py`：`AsinReportRequest`（`request_id` **必填** / `seller_id` /
      `site` / `captured_at` 可选 / `ship_to_region` / `page_context` / `items[]`）与
      `AsinReportResponse`（`report_id` / `accepted` / `new_asins` / `baseline_established` /
      `duplicate_of` / `rejected[]` 逐条附原因）。条数上限用 Pydantic 约束，超限即 422；
      ASIN 统一大写去空白、请求内去重；标题/图片/页面上下文超长**截断不拒绝**
- [x] 2.2 `asin_crud.py`：`compute_request_hash`（纯函数：sha256 over
      `seller_id` + `site` + 规范化去重排序后的 ASIN 集合，**不含 `request_id`**、
      不含审计与展示字段）、`insert_report_stmt`（`ON CONFLICT (user_id, request_id) DO
      NOTHING ... RETURNING`，未返回即命中重复，回查原行并比对 `request_hash`）、`insert_asins_stmt`
      （`ON CONFLICT DO NOTHING ... RETURNING asin`，带 `first_report_id`，不更新
      `first_seen_at` 与已落定属性）、`update_report_counts_stmt`（回填 `new_count`）、
      `set_baseline_stmt`（**实现用 `WHERE baseline_at IS NULL` 而非
      `SET = COALESCE(...)`**：保证等价，但常态下根本不写——后者每次上报都 UPDATE 一行、
      标死旧元组、重写索引，而 99% 的上报是「基线早就有了」；且「本次是否落定」由
      `RETURNING` 有没有出行免费得到）、`watched_seller_stmt`
- [x] 2.3 `asin_service.py`：写入顺序 **report → ASIN（`RETURNING` 出新增数）→ 回填
      `new_count` → 落基线**，同一事务提交；时间一律取服务端事务时间，`captured_at` 只
      落库不参与判定；卖家不在**上报者本人**的 `user_seller_watch` 中则整个请求拒绝并
      附原因；命中幂等键且摘要一致则回放原记录（`duplicate_of`），不新增、不推进
      「最近采集于」；**摘要不一致则抛 409 `competitor.idempotency_key_reused`**，
      不写入也不回放
- [x] 2.4 `asin_api.py`：`POST /api/competitor/asin-reports`，挂
      `Depends(require_permission(COMPETITOR_VIEW))`，`user_id` 一律取自令牌；
      命中重复**且摘要一致**返 200 而非 409（返错误码会让采集端继续重试）；
      **摘要不一致才返 409**；注册进 `main.py` 的路由
- [x] 2.5 新增配置 `COMPETITOR_MAX_REPORT_ITEMS`（默认 200）到 `config.py` 与
      `.env.example`（带单位与理由，`test_conventions.py` 会比对）

## 3. 上新流合并两类来源

- [x] 3.1 `store_crud.new_arrivals_stmt` 改为两来源统一投影后 `UNION ALL`，再统一排序
      分页；Amazon 侧条件是 `baseline_at IS NOT NULL AND first_seen_at > baseline_at`
      且落在时间窗内。三处实现细节写在 docstring 里：
      **(a)** 排序键改为界面显示的那个 `at`（独立站原先按 `first_seen_at` 排却显示
      `published_at`，单来源时看不出来，合并后就是一张乱序表）；**成员判定仍按
      `first_seen_at`**，两个口径不同是既有决策。
      **(b)** 必须有次级排序键 `(source, source_key, product_key)`——`published_at` 只
      精确到分钟，同一分钟多条是常态，无次级键时同一行会在两页各出现一次。
      **(c)** Amazon 侧 `source_name` 恒 NULL（我们从未取过卖家名，不拿用户私有的
      `note` 顶替）、`title` 用 `COALESCE(title, asin)`（上报可不带标题，而契约里必填）
- [x] 3.2 `store_service.new_arrivals` 按合并后的行组装响应。**来源判别与时间语义都在
      SQL 里定完，service 只做映射**——UNION 之后"这一行来自哪张表"的信息已经没有了，
      在 Python 里按行推断是不可能的；`total` 对同一个 union 子查询计数
- [x] 3.3 卖家清单查询补「近 7 日新发现」与「最近采集于」（`watch_crud.watches_stmt` 两个
      相关子查询 + `WatchOut` 两个字段）。**唯一容易写错、且错了不会报错的地方**：相关子
      查询里的 `count(*)` 恒返回 0 而不是 NULL，所以无基线时必须显式用
      `CASE WHEN baseline_at IS NULL THEN NULL` 罩一层——写成 `COALESCE(...,0)` 或裸用
      count，整个「待建立基线 vs 监控中」的界面故事就地塌掉。已验证：去掉那层 CASE，
      `test_无基线的卖家两个指标都是None而不是0` 即红。
      「最近采集于」不必罩：`max()` 对空集天然 NULL，且有上报就必然有基线

## 4. 前端

- [x] 4.1 `pages/Competitor/columns.tsx`：卖家状态按有无基线显示「待建立基线 / 监控中」
      **两态**（状态列本身在 4.2 已收成两态，本条只是补两列数据）；列改名为
      `近 ${NEW_DISCOVERY_WINDOW_DAYS} 日新发现`，无基线（`new_discoveries_7d === null`）
      显示占位符「待建立基线」、有基线显示真实数字（含 0）；「最近采集于」无上报显示
      「未采集」，有上报显示时刻，**超过窗口天数没有新上报时追加陈旧提示**
      （`Tooltip` + `Typography.Text type="warning"`）。

      窗口天数 `NEW_DISCOVERY_WINDOW_DAYS=7` 在前端硬编码，与后端同名常量手动保持同步
      （后端没有把它通过接口下发，它是代码常量不是运行时配置）——注释里写明了这个耦合，
      改后端那个常量必须记得回来改这里。
- [x] 4.2 从 `WatchStatus` 与状态映射里删掉 `PAUSED`/「已暂停」（`services/competitor.ts`、
      `columns.tsx`、`app/table/competitor/watch_schemas.py` 三处一起），注释里写明暂停
      若要做属于私有层 `user_seller_watch`；回归测试
      `test_competitor_watch_unit.py::test_状态只有两态_没有已暂停` 断言取值集合
      （已验证：把 `PAUSED` 加回去该用例即红）。`pnpm tsc` 与
      `pnpm test src/pages/Competitor`（38 passed）通过
- [x] 4.3 `NewArrivalsTab` 的 AMAZON 分支**零代码改动即打通**——组件此前就已按两类来源
      的形状写好（`SOURCE_META`/`TIME_KIND_META` 都含 AMAZON 分支、`source_name` 已有
      `|| source_key` 回落），§3.1/3.2 让后端真的产出 AMAZON 行之后直接生效。只更新了
      一处过时的 docstring（不再说"当前只有独立站一类有数据"）。

      **「Amazon 条目详情展示采集元数据」未实现，且这不是 Amazon 分支特有的缺口**：
      `NewArrivalsTab` 现在对**两类来源都没有**点开一行看详情的交互——DTC 行同样没有。
      `competitor-storefront-watch` spec 的「查看条目详情」场景目前对两个来源都不成立，
      是遗留缺口（预先存在，与本变更引入的东西无关），不在本变更范围内补，已在组件
      docstring 里如实标注。**不要把这条静默勾成"已实现"。**
- [x] 4.4 `services/competitor.ts` 的 `WatchRow` 补 `new_discoveries_7d` / `last_reported_at`
      两个字段（`NewArrivalRow` 已经与后端契约一致，未改）；无新增页面、无新增菜单项

## 5. 测试

- [x] 5.1 上报服务的硬约束。`test_competitor_asin_unit.py`（24 项，不连库：schema 上限、
      ASIN 规范化/去重/逐条拒、摘要字段边界）+ `test_competitor_asin_integration.py`
      （17 项，真库：未关注卖家整批拒、重复上报不新增行且 `first_seen_at` 不变、
      标题只在首次落定、`first_report_id` 指向正确的那次上报）
- [x] 5.2 `user_id` 只取自令牌——`test_请求体里的user_id被无视`。schema 用
      `extra="ignore"` 而非 `forbid`：后者把它变成 422，反而暗示「这个字段是被认识的」
- [x] 5.3 幂等四态，全部是真库行为测试（不是 spec 描述）：**同键同体**回放、上报记录数
      不变、「最近采集于」不推进；**同键异体**（换 ASIN 集合 / 换卖家 / 换 `captured_at`）
      三条各一个用例，均返 409 且一条不写；**跨用户同键**互不影响；**只有标题不同**仍按
      重放处理（展示属性不进摘要）
- [x] 5.4 `captured_at` 偏移不影响判定、`first_seen_at` 仍取服务端时间；且
      `competitor_seller_asin` 表上根本没有 `captured_at` 列（断言列名单）
- [x] 5.5 集成：首次上报不产生上新条目 ✅、`first_report_id` 指向正确 ✅、
      **跨事务下第二次上报的新品严格晚于基线** ✅（单独的 committing 夹具——夹具那个外层
      事务里 `now()` 恒定，这条只有真提交才验得到，而它错了不会报错、只表现为"上新流永远
      是空的"）、**两来源合并后翻页无重复无遗漏** ✅（逐页取完与一次取全逐项相等）。

      **次级排序键那条改用结构性断言**（`test_上新流排序带次级键`，比 ORDER BY 子句）：
      跑数据验不出来——去掉次级键后集成用例照样全绿，6 行数据下 Postgres 的顺序碰巧稳定，
      乱序要更大数据量或不同执行计划才现形。实测确认过这个假绿，用例注释里写明了
- [x] 5.6 前端。`index.test.tsx` 新增 3 项：无基线两指标皆 `null`（占位符「待建立基线」+
      「未采集」，且断言用 `getAllByText` 而不是 `getByText`——状态列与指标列此时显示
      同一个字符串，`getByText` 遇多个匹配会抛错，这是写的时候真的踩到的）；有基线显示
      真实 0；陈旧提示（`.ant-typography-warning` 存在）。**已验证 `isStale` 判断写死
      返回 false 时该用例即红**，不是假绿。上新流 AMAZON 措辞的 5 项此前已在
      （`NewArrivalsTab.test.tsx`，本轮零改动）。
      全量 `pnpm lint && pnpm tsc && pnpm test` → **374 passed**
- [~] 5.7 全量回归（§3 全部落地后）：`TEST_DATABASE_URL=... uv run pytest test/` →
      **805 passed, 1 skipped**（唯一的 skip 是 OSS 真实回路，需 `TEST_OSS_*`，与本变更无关）；
      `ruff` 通过。§4 落地后需再跑一次，含前端全量

## 6. 文档与收尾

- [x] 6.1 CLAUDE.md 新增独立小节「竞对卖家 ASIN 上报」（`## 竞对监控` 下、`### 独立站
      监控` 前）：推模型边界、基线不可改及其"同一事务"机制、时间权威只在服务端、幂等
      两把锁（含摘要范围曾写反过一次的教训）、`first_report_id` 溯源、输入约束的拒绝
      粒度、上新流合并的两个"错了不报错"陷阱（次级排序键、null-vs-0 的 CASE）、保留清理
      排除、配送地区不参与判定。共享表清单与「六张普通表」一并更新。
      `pytest -m conventions` 15 passed（文档一致性检查）
- [x] 6.2 与 issue #56 的偏离及审查回应同步回 issue 评论。**第一轮回复里「已暂停三处
      一起删了」是错的**（当时只改了 spec），第二轮已更正并真的删掉
- [ ] 6.3 按路径提交 → 推分支 → `gh pr create --base v2`（PR 正文含变更 id、
      `Refs #56`、spec delta 文件列表）
