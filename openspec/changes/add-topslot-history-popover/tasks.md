## 1. 后端：Schema 与 CRUD

- [ ] 1.1 在 `app/table/search/search_schemas.py`（或新建
      `app/table/analysis/topslot_history_schemas.py`）定义请求/响应 Pydantic v2
      模型：请求含 `site/view/keyword_id/on/kind(ASIN|BRAND)/value/page/page_size`，
      响应含 `total` 与 `items: list[{date, rank}]`。
- [ ] 1.2 在 `app/table/analysis/analysis_crud.py`（或新建
      `topslot_history_crud.py`，与 `trend_crud.py` 同级）新增：
      - 按 `kind` 解析身份到 id 的语句（`dim_product.asin = :value` /
        `dim_brand.name = :value`，各自走既有唯一约束）
      - 主查询：`fact_and_datecol(view)` 取事实表与日期列，
        `WHERE site=:site AND keyword_id=:kw AND date_col BETWEEN :start AND :end
        AND (topN_xxx_id = :id ...)`，投影用 `LEAST(CASE WHEN...THEN 1 ... END)`
        取最佳槽位名次，`ORDER BY date_col DESC` 分页（`OFFSET/LIMIT`）
      - 与主查询同条件的精确 `COUNT(*)` 语句（不含 OFFSET/LIMIT）
- [ ] 1.3 单元测试（不连库）：断言语句结构——WHERE 子句同时包含 `site`/`keyword_id`
      前缀条件与三槽位 OR 条件；`LEAST(CASE...)` 表达式对"多槽位命中"取最小值的
      纯 Python/SQL 编译层断言（可用 `str(stmt.compile(...))` 或直接构造内存数据
      跑一次 `LEAST` 语义的单元覆盖）。

## 2. 后端：Service 与权限

- [ ] 2.1 新建 `app/table/analysis/topslot_history_service.py`：
      - `normalize_site_code` 规整站点码，非法站点 422（复用
        `KeywordTrendScopeError` 同款错误码风格或新增 `analysis.scope_invalid`
        复用既有 `CODE_ANALYSIS_SCOPE_INVALID`）
      - 校验 `keyword_id` 属于 `site`（参照 `trend_service._require_keyword_site`）
      - `page_size` 上限校验，超限拒绝（复用 `import_batch_crud.MAX_PER_PAGE`
        同款拒绝式范式，在本模块定义独立的页大小上限常量）
      - 计算 `start = partitioning.retention_cutoff(today, settings.HOT_WINDOW_MONTHS)`，
        `end = on`（请求参数，必填，不做退化重算）
      - 身份解析查不到时短路返回 `total=0, items=[]`，不报错
- [ ] 2.2 单元测试：站点非法、keyword_id 与 site 不一致、page_size 超限、
      身份解析失败（空结果非报错）四类场景。

## 3. 后端：API 路由

- [ ] 3.1 新增 `GET /api/analysis/topslot-history` 路由（在 `analysis_api.py`
      追加或新建 `topslot_history_api.py` 并挂载到既有 analysis 路由前缀），
      鉴权 `Depends(simple_auth.require_permission(ANALYSIS_VIEW))`。
- [ ] 3.2 路由集成测试（`TEST_DATABASE_URL` gate，仿照
      `test/test_site_scoped_query_integration.py` 的夹具风格）：
      - 同一 ASIN 三期分别 TOP1/TOP3/TOP2 → 次数 3、逐期排名正确、按日期倒序
      - 同一品牌跨期换槽位仍正确汇总
      - 其他关键词/其他站点的同名 ASIN／品牌不计入
      - 日视图查询不返回周报期次，反之亦然
      - 同期同对象重复占多槽位 → 只计一期、取最佳名次
      - 分页：记录数超过一页时首页 `total` 等于整体命中数而非首页行数
      - `page_size` 超上限 → 4xx
      - 未登录 / 无 `analysis:view` 权限 → 401/403
      - `keyword_id` 与 `site` 不一致 → 4xx
      - `EXPLAIN ANALYZE` 核对命中 `idx_fact_{daily,weekly}_keyword_history`，
        不出现 `Seq Scan`（记录在测试注释或独立验证脚本里，若无法在 pytest
        断言执行计划文本，至少断言查询在大数据量 fixture 下的响应时间量级）

## 4. 前端：服务层与类型

- [ ] 4.1 在 `frontend/src/services/analysis.ts`（或新建
      `frontend/src/services/topslotHistory.ts`）新增请求函数与 TS 类型，
      对齐后端 schema（`kind`/`value`/`page`/`page_size`/`total`/`items`）。
- [ ] 4.2 服务层单元测试：请求参数拼装正确性（站点/视图/keyword_id/on 来自调用方
      传入，不在服务层做默认值兜底）。

## 5. 前端：悬浮历史面板组件

- [ ] 5.1 新建可复用组件（如
      `frontend/src/pages/Analysis/TopslotHistoryPopover.tsx`）：Popover/Tooltip
      承载 + 内部列表，固定最大高度 + 内部滚动，`onScroll` 判定触底后请求下一页。
- [ ] 5.2 实现请求令牌竞态防护：组件内自增序号，响应到达时比对，不是最新则丢弃。
- [ ] 5.3 实现五种状态渲染：首次加载中 / 追加加载中 / 空记录 / 失败（含重试按钮）/
      全部加载完成（不再触发请求，标题次数保持不变）。
- [ ] 5.4 首次悬浮才发起请求（无预取），面板标题显示「ABA 上榜次数（N）」，
      `N` 只在首次加载完成时写入，后续翻页响应的 `total` 不覆盖已显示数字。
- [ ] 5.5 组件测试（vitest + testing-library）：悬浮触发请求、触底加载、快速切换
      查看对象时旧响应被丢弃、失败重试、空状态、全部加载完成后不再请求。

## 6. 前端：接入 ASIN 卡牌与品牌列

- [ ] 6.1 接入 `TopProductCards.tsx`：每个非空槽位卡牌包一层悬浮入口，
      确保悬浮不误触发外链跳转、不破坏现有复制/跳转行为；请求参数（`site/view/on`）
      取自**生效态**查询作用域，`keyword_id`/`asin` 取自当前行。
- [ ] 6.2 接入品牌列（`RankedList.tsx` / `columns.tsx`）：每个非空品牌可悬浮，
      面板明确标注当前查看的品牌全名；确认所选 UI 形态（逐品牌独立悬浮，或
      左侧列表 + 右侧详情）并落实，保证次数与记录不串位。
- [ ] 6.3 组件测试：ASIN 卡牌与品牌列悬浮后请求参数正确（含站点/视图/keyword_id/
      on 与生效作用域一致，而非草稿态）；现有卡牌/品牌列测试（`TopProductCards.test.tsx`
      等）保持通过。

## 7. 收尾验证

- [ ] 7.1 按改动范围跑定向测试：`uv run pytest test/<新增测试路径> -q`，
      前端 `pnpm exec vitest related <改动文件>`。
- [ ] 7.2 真实浏览器验证：悬浮打开面板、面板内滚动加载更多、切换行/站点/视图后
      悬浮内容不串、失败重试可用（可临时断网或 mock 4xx 观察）。
- [ ] 7.3 提交前跑一次 `openspec validate add-topslot-history-popover --strict`
      确认规格文件格式无误。

Refs #96
