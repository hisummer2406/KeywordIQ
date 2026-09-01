# 任务

> 进度留痕：并行会话只能通过这里知道做到哪了。勾选与说明必须一致——勾了 `[x]` 却在尾注写
> 「待运行」，读的人无从判断到底跑没跑。

## 1. 数据模型与迁移

- [ ] 1.1 新建 `app/table/competitor/asin_model.py`：`CompetitorSellerAsin`
      （主键 `(seller_id, site, asin)`，含 `first_seen_at` / `title` / `image_url`）与
      `CompetitorAsinReport`（上报元数据：`user_id` / `seller_id` / `site` /
      `reported_at` / `item_count` / `ship_to_region` / `page_context`），
      列注释按模块惯例写全，站点码注释复用 `column_comments.SITE_COLUMN_COMMENT`
- [ ] 1.2 建索引：`idx_competitor_seller_asin_new (seller_id, site, first_seen_at)`
      支撑上新流的窗口过滤与排序；`idx_competitor_asin_report_latest (seller_id, site,
      reported_at DESC)` 支撑「最近采集于」
- [ ] 1.3 两张表登记进 `migration_scope.py` 的 `V2_TABLES`（漏登 autogenerate 既看不见
      也不报错）；**确认没有**登记进 `partitioning.PARTITIONED_TABLES`
- [ ] 1.4 `uv run alembic revision --autogenerate -m "竞对卖家在售 ASIN 与上报记录"`，
      人工核对生成的 DDL：两张普通表、schema 为 `analysis`、列注释与模型一字不差、
      **不出现** `PARTITION BY`
- [ ] 1.5 `uv run alembic upgrade head`，并跑
      `test_v2_integration.py::test_models_match_migrated_schema` 确认 autogenerate 对
      迁移后的库产出**空**结果

## 2. 上报端点

- [ ] 2.1 `asin_schemas.py`：`AsinReportRequest`（`seller_id` / `site` / `captured_at` /
      `ship_to_region` / `page_context` / `items[]`）与 `AsinReportResponse`
      （`accepted` / `new_asins` / `baseline_established` / `rejected[]` 逐条附原因）；
      条数上限用 Pydantic 约束，超限即 422
- [ ] 2.2 `asin_crud.py`：`insert_asins_stmt`（`ON CONFLICT DO NOTHING`，不更新
      `first_seen_at` 与已落定属性）、`set_baseline_stmt`（`COALESCE(baseline_at, :now)`
      ——一旦落定不再改动）、`insert_report_stmt`、`watched_sellers_stmt`
- [ ] 2.3 `asin_service.py`：校验每条上报的 `(seller_id, site)` 在**上报者本人**的
      `user_seller_watch` 中，不在则整批拒绝并附原因；落基线 → 写 ASIN → 写上报记录，
      同一事务提交
- [ ] 2.4 `asin_api.py`：`POST /api/competitor/asin-reports`，挂
      `Depends(require_permission(COMPETITOR_VIEW))`，`user_id` 一律取自令牌；
      注册进 `main.py` 的路由
- [ ] 2.5 新增配置 `COMPETITOR_MAX_REPORT_ITEMS`（默认 200）到 `config.py` 与
      `.env.example`（带单位与理由，`test_conventions.py` 会比对）

## 3. 上新流合并两类来源

- [ ] 3.1 `store_crud.new_arrivals_stmt` 改为两来源统一投影后 `UNION ALL`，再统一排序
      分页；Amazon 侧条件是 `baseline_at IS NOT NULL AND first_seen_at > baseline_at`
      且落在时间窗内
- [ ] 3.2 `store_service.new_arrivals` 按合并后的行组装响应：Amazon 行
      `source="AMAZON"` / `time_kind="DISCOVERED"`；`total` 对同一个子查询计数
- [ ] 3.3 卖家清单查询补「近 7 日上新数」与「最近采集于」：无基线的卖家返回 null
      （前端显示占位符），有基线的返回真实条数（含 0）

## 4. 前端

- [ ] 4.1 `pages/Competitor/columns.tsx`：卖家状态按有无基线显示「待建立基线 / 监控中」，
      「近 7 日上新数」在无基线时显示占位符、有基线时显示真实数字（含 0）；
      有基线的行显示「最近采集于」
- [ ] 4.2 `NewArrivalsTab` 打通 AMAZON 分支：来源标记与「发现于」措辞（组件已有形状，
      确认无需为此改契约）
- [ ] 4.3 `services/competitor.ts` 补类型；无新增页面、无新增菜单项

## 5. 测试

- [ ] 5.1 单元：上报服务的四条硬约束——未关注卖家整批拒绝且附原因、超限整批不写、
      重复上报不新增行且 `first_seen_at` 不变、基线只在首次落定（`uv run pytest test/`）
- [ ] 5.2 单元：`user_id` 只取自令牌——请求体里塞 `user_id` 不生效
- [ ] 5.3 集成（`TEST_DATABASE_URL` gate）：首次上报不产生上新条目、第二次上报的新 ASIN
      产生条目、两来源合并后翻页无重复无遗漏
- [ ] 5.4 前端：卖家行两态显示（占位符 vs 真实 0）与上新流 AMAZON 行的「发现于」措辞
      （`pnpm test src/pages/Competitor`）
- [ ] 5.5 全量回归：`uv run pytest test/`（含集成部分）与
      `pnpm lint && pnpm tsc && pnpm test` 全绿

## 6. 文档与收尾

- [ ] 6.1 CLAUDE.md 的竞对监控一节补：两张新表、推模型的边界（不承诺完整性、不做下架
      判定）、基线一旦落定不再改动、不建快照表的理由
- [ ] 6.2 `openspec/changes/.../design.md` 里与 issue #56 的两处偏离（不建快照表、
      粒度按卖家）同步回 issue 评论
- [ ] 6.3 按路径提交 → 推分支 → `gh pr create --base v2`（PR 正文含变更 id、
      `Refs #56`、spec delta 文件列表）
