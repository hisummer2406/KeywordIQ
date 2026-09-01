# 任务

> 进度留痕：并行会话只能通过这里知道做到哪了。勾选与说明必须一致——勾了 `[x]` 却在尾注写
> 「待运行」，读的人无从判断到底跑没跑。

> 2026-09-01：按 issue #56 的审查评论修订过一轮（措辞改「新发现」、加幂等键与溯源列、
> 删「已暂停」、把输入约束写进契约）。§1 已按修订后的模型重做。

## 1. 数据模型与迁移

- [x] 1.1 新建 `app/table/competitor/asin_model.py`：`CompetitorAsinReport`（上报元数据：
      `user_id` / `request_id` / `seller_id` / `site` / `reported_at` / `captured_at` /
      `item_count` / `new_count` / `ship_to_region` / `page_context`）与
      `CompetitorSellerAsin`（主键 `(seller_id, site, asin)`，含 `first_seen_at` /
      `first_report_id` / `title` / `image_url`）。**报表类必须定义在前**——在售关系的
      `first_report_id` 指向它。列注释按模块惯例写全，站点码注释复用
      `column_comments.SITE_COLUMN_COMMENT`
- [x] 1.2 建索引与约束：`idx_competitor_seller_asin_new (seller_id, site, first_seen_at)`
      支撑上新流的窗口过滤与排序；`idx_competitor_asin_report_latest (seller_id, site,
      reported_at)` 支撑「最近采集于」；`uq_competitor_asin_report_request
      (user_id, request_id)` 是幂等键——**按用户分域不是全局**（跨用户撞键会吞掉后者的
      上报并回放前者的计数）
- [x] 1.3 两张表登记进 `migration_scope.py` 的 `V2_TABLES`（漏登 autogenerate 既看不见
      也不报错）；**确认没有**登记进 `partitioning.PARTITIONED_TABLES`
- [x] 1.4 `uv run alembic revision --autogenerate -m "竞对卖家在售 ASIN 与上报记录"`，
      人工核对生成的 DDL：两张普通表、schema 为 `analysis`、列注释与模型一字不差、
      建表顺序是 report 在前、**不出现** `PARTITION BY`（迁移 `9e8d5acfc53f`）
- [x] 1.5 `uv run alembic upgrade head`；对迁移后的库再跑一次 autogenerate 探针，产出为
      空（`upgrade()` 只有 `pass`），探针文件已删除
- [ ] 1.6 在测试库上跑 `test_v2_integration.py::test_models_match_migrated_schema`

## 2. 上报端点

- [ ] 2.1 `asin_schemas.py`：`AsinReportRequest`（`request_id` **必填** / `seller_id` /
      `site` / `captured_at` 可选 / `ship_to_region` / `page_context` / `items[]`）与
      `AsinReportResponse`（`report_id` / `accepted` / `new_asins` / `baseline_established` /
      `duplicate_of` / `rejected[]` 逐条附原因）。条数上限用 Pydantic 约束，超限即 422；
      ASIN 统一大写去空白、请求内去重；标题/图片/页面上下文超长**截断不拒绝**
- [ ] 2.2 `asin_crud.py`：`insert_report_stmt`（`ON CONFLICT (user_id, request_id) DO
      NOTHING ... RETURNING`，未返回即命中重复，回查原行）、`insert_asins_stmt`
      （`ON CONFLICT DO NOTHING ... RETURNING asin`，带 `first_report_id`，不更新
      `first_seen_at` 与已落定属性）、`update_report_counts_stmt`（回填 `new_count`）、
      `set_baseline_stmt`（`COALESCE(baseline_at, :now)`——一旦落定不再改动）、
      `watched_seller_stmt`
- [ ] 2.3 `asin_service.py`：写入顺序 **report → ASIN（`RETURNING` 出新增数）→ 回填
      `new_count` → 落基线**，同一事务提交；时间一律取服务端事务时间，`captured_at` 只
      落库不参与判定；卖家不在**上报者本人**的 `user_seller_watch` 中则整个请求拒绝并
      附原因；命中幂等键则回放原记录（`duplicate_of`），不新增、不推进「最近采集于」
- [ ] 2.4 `asin_api.py`：`POST /api/competitor/asin-reports`，挂
      `Depends(require_permission(COMPETITOR_VIEW))`，`user_id` 一律取自令牌；
      命中重复返 **200 而非 409**（返错误码会让采集端继续重试）；注册进 `main.py` 的路由
- [ ] 2.5 新增配置 `COMPETITOR_MAX_REPORT_ITEMS`（默认 200）到 `config.py` 与
      `.env.example`（带单位与理由，`test_conventions.py` 会比对）

## 3. 上新流合并两类来源

- [ ] 3.1 `store_crud.new_arrivals_stmt` 改为两来源统一投影后 `UNION ALL`，再统一排序
      分页；Amazon 侧条件是 `baseline_at IS NOT NULL AND first_seen_at > baseline_at`
      且落在时间窗内
- [ ] 3.2 `store_service.new_arrivals` 按合并后的行组装响应：Amazon 行
      `source="AMAZON"` / `time_kind="DISCOVERED"`；`total` 对同一个子查询计数
- [ ] 3.3 卖家清单查询补「近 7 日新发现」与「最近采集于」：无基线的卖家两项都返回 null
      （前端显示占位符），有基线的返回真实条数（含 0）与最近一次 `reported_at`

## 4. 前端

- [ ] 4.1 `pages/Competitor/columns.tsx`：卖家状态按有无基线显示「待建立基线 / 监控中」
      **两态**；列改名为「近 7 日新发现」，无基线时显示占位符、有基线时显示真实数字（含 0）；
      有基线的行显示「最近采集于」并在陈旧时提示
- [ ] 4.2 从 `WatchStatus` 与状态映射里删掉 `PAUSED`/「已暂停」（`services/competitor.ts`、
      `columns.tsx`、`app/table/competitor/watch_schemas.py` 三处一起），并在注释里写明
      暂停若要做属于私有层 `user_seller_watch`
- [ ] 4.3 `NewArrivalsTab` 打通 AMAZON 分支：来源标记与「发现于」措辞（组件已有形状，
      确认无需为此改契约）；Amazon 条目详情展示采集元数据，**不出现 BSR/评价数**
- [ ] 4.4 `services/competitor.ts` 补类型；无新增页面、无新增菜单项

## 5. 测试

- [ ] 5.1 单元：上报服务的硬约束——未关注卖家整个请求拒绝且附原因、条数超限整批不写、
      单条格式非法逐条拒且其余照写、重复上报不新增行且 `first_seen_at` 不变、基线只在
      首次落定（`uv run pytest test/`）
- [ ] 5.2 单元：`user_id` 只取自令牌——请求体里塞 `user_id` 不生效
- [ ] 5.3 单元：幂等键——同 `request_id` 重发返 200 + `duplicate_of`、上报记录数不变、
      「最近采集于」不推进；两个用户用同一个 `request_id` 互不影响
- [ ] 5.4 单元：`captured_at` 偏移（早于基线/晚于现在）都不影响判定，`first_seen_at`
      仍取服务端时间
- [ ] 5.5 集成（`TEST_DATABASE_URL` gate）：首次上报不产生上新条目、第二次上报的新 ASIN
      产生条目、`first_report_id` 指向正确的那次上报、两来源合并后翻页无重复无遗漏
- [ ] 5.6 前端：卖家行两态显示（占位符 vs 真实 0）、「近 7 日新发现」措辞、状态里没有
      「已暂停」、上新流 AMAZON 行的「发现于」措辞（`pnpm test src/pages/Competitor`）
- [ ] 5.7 全量回归：`uv run pytest test/`（含集成部分）与
      `pnpm lint && pnpm tsc && pnpm test` 全绿

## 6. 文档与收尾

- [ ] 6.1 CLAUDE.md 的竞对监控一节补：两张新表、推模型的边界（不承诺完整性、不做下架
      判定、产出是「新发现」不是「上新」）、基线一旦落定不再改动、时间权威只在服务端、
      不建快照表而用 `first_report_id` 溯源的理由
- [x] 6.2 与 issue #56 的偏离及审查回应同步回 issue 评论（不建快照表、粒度按卖家、
      两个前置条件主动判定不阻塞服务端侧、措辞改「新发现」、加幂等键与溯源列、删「已暂停」）
- [ ] 6.3 按路径提交 → 推分支 → `gh pr create --base v2`（PR 正文含变更 id、
      `Refs #56`、spec delta 文件列表）
