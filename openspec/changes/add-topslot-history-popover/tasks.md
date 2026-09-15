## 1. 后端：Schema 与 CRUD

- [x] 1.1 `app/table/analysis/topslot_history_schemas.py`：`TopslotKind`
      (`ASIN`/`BRAND`)、`TopslotHistoryItem`（date, rank）、`TopslotHistoryResponse`
      （total, items）。请求走 GET 查询参数（与 `/scope`/`/categories`/`/trend`
      同风格），未单独建请求体 schema。
- [x] 1.2 `app/table/analysis/topslot_history_crud.py`：`resolve_asin_stmt`/
      `resolve_brand_stmt`（身份 → id）、`history_stmt`（`LEAST(CASE...)` 取最佳
      槽位名次 + 分页）、`count_stmt`（同条件精确计数，无 OFFSET/LIMIT）。
- [x] 1.3 `test/test_topslot_history_crud_unit.py`（10 用例，已跑绿）：语句结构、
      槽位匹配按 kind 区分、LEAST/CASE 表达式、周报列名、身份解析语句。

## 2. 后端：Service 与权限

- [x] 2.1 `app/table/analysis/topslot_history_service.py`：站点规整复用
      `analysis_service.ScopeError`；`keyword_id` 归属校验（
      `TopslotKeywordNotFoundError`/`TopslotSiteMismatchError`）；`page_size`
      上限 `MAX_PAGE_SIZE=20`，超限 `TopslotPageSizeExceededError`（400）；
      `retention_cutoff` 到 `on` 的历史范围；身份解析失败短路空结果不报错。
- [x] 2.2 `test/test_topslot_history_service_unit.py`（6 用例，已跑绿）：站点
      规整/非法、page/page_size 校验四类场景。

## 3. 后端：API 路由

- [x] 3.1 `GET /api/analysis/topslot-history` 挂在 `analysis_api.py`，权限
      `ANALYSIS_VIEW`。
- [x] 3.2 `test/test_topslot_history_integration.py`（12 用例，已在
      `keywordiq_test` 跑绿）覆盖：次数/逐期排名、重复占槽取最佳名次、跨关键词/
      跨站点/跨视图隔离、保留窗口外排除、分页 total 独立于页大小、身份解析失败
      返空、site/keyword_id 不匹配报错。另用 `EXPLAIN` 核对目标索引
      `(site, keyword_id, report_date)` 确实存在于每个月度分区（测试库数据量小，
      规划器在近空表上会挑别的索引，这是预期的近空表噪声，不是查询设计问题——
      `test_topslot_history_api_unit.py`（8 用例，已跑绿）另覆盖权限边界与错误码
      映射（401/403/422/404/400）。

## 4. 前端：服务层与类型

- [x] 4.1 `frontend/src/services/topslotHistory.ts`：`TopslotKind`、
      `TopslotHistoryItem`、`TopslotHistoryResponse`、`getTopslotHistory()`。
- [ ] 4.2 服务层单元测试——未单独写（此文件只是参数透传，行为已被
      `TopslotHistoryPopover.test.tsx` 里对 `getTopslotHistory` 调用参数的断言
      间接覆盖）。

## 5. 前端：悬浮历史面板组件

- [x] 5.1 `frontend/src/pages/Analysis/TopslotHistoryPopover.tsx`：Popover 承载，
      内部滚动容器 + `onScroll` 触底判定。
- [x] 5.2 请求序号（`requestSeq` ref）+ `mountedRef` 防止旧响应/卸载后 setState。
- [x] 5.3 五态渲染：loading / error(重试) / total===0 空态 / ready 列表（含
      loadingMore、moreErrored 重试、!hasMore 完成提示）。
- [x] 5.4 悬浮才发首次请求；标题次数只在 phase 变 ready 时写入 `total` state，
      翻页响应的 `total` 不回写。
- [x] 5.5 `frontend/src/pages/Analysis/TopslotHistoryPopover.test.tsx`（11 用例）
      已写：不预取、悬浮发请求且参数正确、五态、触底追加且 total 不被覆盖、未
      触底不追加、请求去重（防抖 loadingMore 期间的重复触底）。**尚未执行**——
      按仓库既有约定，前端测试/lint/tsc 由用户自己跑（`pnpm test
      src/pages/Analysis/TopslotHistoryPopover.test.tsx`），本会话未运行 vitest/tsc。
      hover 相关时序（`mouseEnterDelay`）用真实定时器 + `waitFor`，未用 fake timers，
      是本组件测试里唯一有一点不确定性的部分，若跑起来有超时请先检查这里。

## 6. 前端：接入 ASIN 卡牌与品牌列

- [x] 6.1 `TopProductCards.tsx`：`historyScope` 缺失时保留原 `Tooltip` 逻辑不变
      （现有测试不受影响）；到位时用 `TopslotHistoryPopover` 替代 Tooltip 承担
      "展示完整标题"这件事，`href`/`<a>` 结构不变，Popover 走独立浮层不冒泡点击。
- [x] 6.2 品牌列：选择的是**逐品牌独立悬浮**（issue 允许的轻量形态），未做
      左侧列表+右侧详情——`RankedList.tsx` 新增可选 `wrapValue` 钩子，仅品牌列传入，
      类目列不受影响。`site/view/on` 取自 `index.tsx` 新增的 `historyScope`
      （由 `loadedSite/loadedView/loadedDate`——已完成响应的活状态，与 `openTrend`
      同一组状态，不是可能仍在飞的 `appliedRef`——推导，随其变化重算 `columns`）。
- [x] 6.3 `TopProductCards.test.tsx`/`columns.test.tsx` 新增用例（mock
      `./TopslotHistoryPopover` 避免真实拉起 `@/services/topslotHistory` →
      `@umijs/max`，这是 `columns.tsx` 无条件引入该组件后新出现的必要 mock，
      不加会让这两个文件里的全部既有用例一起因 esbuild/`@umijs/max` 环境不变量
      报错炸掉——同 `KeywordTrendDrawer` 已踩过的坑）。**同样尚未执行**，理由同上。

## 7. 收尾验证

- [x] 7.1 后端定向测试已跑绿：`test_topslot_history_crud_unit.py`（10）、
      `test_topslot_history_service_unit.py`（6）、`test_topslot_history_api_unit.py`
      （8）、`test_topslot_history_integration.py`（12，`TEST_DATABASE_URL` 真库）。
      `ruff check` 已过。前端定向测试**未跑**（同上，用户自行执行）。
- [ ] 7.2 真实浏览器验证——未做。需要：起本地环境后在市场分析表格里悬浮 ASIN
      卡牌与品牌值，确认面板出现、可滚动触底加载、切换行/站点/视图后不串数据、
      断网或改错权限观察失败重试。这一步只能人工在浏览器里核实。
- [x] 7.3 `openspec validate add-topslot-history-popover --strict` 已过。

Refs #96
