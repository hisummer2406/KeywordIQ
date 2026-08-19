## 1. 分析屏恢复首屏自动查询

- [x] 1.1 `pages/Analysis/index.tsx` 挂载 effect：`reloadScopeMeta(undefined, 'DAY')` 接回
      `.then((s) => apply(...))`，按解析出的默认作用域查一次。`if (restored) return;`
      **保持在最前**（决策 2），并把注释改成说明「为什么自动查」而不是「为什么不查」
- [x] 1.2 空状态判据从 `hasQueried` 换成「作用域有无可查日期」：无日期时文案说明该作用域
      暂无数据、不显示结果条数；有日期时照常显示「搜索结果：N」
- [x] 1.3 确认 `hasQueried` 仍只用于「有未生效改动」标签的显隐（`dirty` 前置），别处不再引用

## 2. 增长屏改为延迟查询

- [x] 2.1 `pages/Toolbox/GrowthMarket/index.tsx` 摘掉挂载 effect 里的 `apply(...)`，只保留
      `getSites()` 与 `reloadScope()`
- [x] 2.2 引入 `hasQueried = appliedSig !== ''`，`dirty` 前置为 `hasQueried && ...`
- [x] 2.3 未查询时 `headerTitle` 不显示「持续增长词：N」
- [x] 2.4 未查询时表格空状态提示去点「立即查询」；查询过则恢复 ProTable 默认空状态
- [x] 2.5 更新文件头注释：把「首屏拉完作用域立即查一次」改成延迟查询及其理由

## 3. 测试

- [x] 3.1 `pages/Analysis/index.test.tsx`：把 `renderSettled(expandFilters, query)` 的
      `query` 默认值改掉（自动查询之后不该再替测试点一次），并确认依赖它的 25 个用例仍绿
- [x] 3.2 `pages/Analysis/index.test.tsx`「首屏」那组用例反向：断言首次进入**发出**了
      `/analysis/search`；新增「站点无可用日期时不发请求且空状态说明无数据」
- [x] 3.3 `pages/Analysis/index.test.tsx`：确认「切走再切回一个请求都不发」那组仍绿
      （自动查询绝不能穿透缓存短路）
- [x] 3.4 `pages/Toolbox/GrowthMarket/index.test.tsx`：首屏用例反向，断言**未**发出
      `/toolbox/growth/search`；新增未查询态三条断言（无条数、无 dirty 标签、空状态文案）
- [x] 3.5 `cd frontend && pnpm test` 全绿；`pnpm lint` 与 `pnpm tsc` 通过
- [x] 3.6 `uv run pytest test/ -q` 全绿——本变更无后端改动，但改了 `CLAUDE.md`，
      `test_conventions.py` 会校验文档与配置的一致性

## 4. 文档与规格

- [x] 4.1 `CLAUDE.md`：分析查询屏一节里「首屏不查数据表」那条整条删除，改写为首屏自动
      查询；空表格那条的判据同步改成「有无可查日期」
- [x] 4.2 `CLAUDE.md`：工具箱 · 持续增长市场一节补上「首屏不自动查询」及其三处配套义务
- [x] 4.3 手动验证：起 dev server，进分析屏确认直接出数据；切到增长屏确认表格空、
      点「立即查询」才出数据；两屏来回切一次确认分析屏不重发请求
- [x] 4.4 `openspec validate --changes` 通过

---

**收尾记录（2026-08-16）**

- 3.1 的处理是把 `renderSettled` 的 `query` 参数**整个删掉**（没有任何用例显式传 `true`，
  留着就是死参数）。同时删掉了独立用例「作用域无可用日期时不发查询」——它被 3.2 新增的
  那条覆盖且更弱（新条多断言了空表格文案与条数不显示）。
- 3.5 / 3.6 实测：前端 `246 passed`（原 243，净增 3），`lint` 与 `tsc` 干净；
  后端 `384 passed, 128 skipped`（本变更无后端改动，skip 的是没有 TEST_DATABASE_URL 的集成测试）。
- **4.3 需人工**：两屏都在登录之后，agent 不能代填凭证，故由需求方目测。

**4.3 验证结果（2026-08-19，需求方目测通过）**

环境：worktree 前端 8003 + 后端 8010（8010 而非 8000，避开主仓约定端口；代理指向由
`frontend/.env.local` 覆盖，该文件被 `.gitignore` 排除、不入库）。库为 `keywordiq_v2_dev`，
US 日报 11 期至 2026-08-08、`fact_growth` 在 8/08 有 1 条。三条全部符合预期：

1. 分析屏进去直接出数据，无需点「立即查询」。
2. 增长屏进去表格为空并提示去点「立即查询」，点后才出数据。
3. 两屏来回切一次，分析屏条件与结果原样保留、未重发 `/api/analysis/search`
   ——自动查询没有穿透会话缓存的短路（决策 2 成立）。
