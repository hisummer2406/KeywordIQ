## 1. 分析屏恢复首屏自动查询

- [ ] 1.1 `pages/Analysis/index.tsx` 挂载 effect：`reloadScopeMeta(undefined, 'DAY')` 接回
      `.then((s) => apply(...))`，按解析出的默认作用域查一次。`if (restored) return;`
      **保持在最前**（决策 2），并把注释改成说明「为什么自动查」而不是「为什么不查」
- [ ] 1.2 空状态判据从 `hasQueried` 换成「作用域有无可查日期」：无日期时文案说明该作用域
      暂无数据、不显示结果条数；有日期时照常显示「搜索结果：N」
- [ ] 1.3 确认 `hasQueried` 仍只用于「有未生效改动」标签的显隐（`dirty` 前置），别处不再引用

## 2. 增长屏改为延迟查询

- [ ] 2.1 `pages/Toolbox/GrowthMarket/index.tsx` 摘掉挂载 effect 里的 `apply(...)`，只保留
      `getSites()` 与 `reloadScope()`
- [ ] 2.2 引入 `hasQueried = appliedSig !== ''`，`dirty` 前置为 `hasQueried && ...`
- [ ] 2.3 未查询时 `headerTitle` 不显示「持续增长词：N」
- [ ] 2.4 未查询时表格空状态提示去点「立即查询」；查询过则恢复 ProTable 默认空状态
- [ ] 2.5 更新文件头注释：把「首屏拉完作用域立即查一次」改成延迟查询及其理由

## 3. 测试

- [ ] 3.1 `pages/Analysis/index.test.tsx`：把 `renderSettled(expandFilters, query)` 的
      `query` 默认值改掉（自动查询之后不该再替测试点一次），并确认依赖它的 25 个用例仍绿
- [ ] 3.2 `pages/Analysis/index.test.tsx`「首屏」那组用例反向：断言首次进入**发出**了
      `/analysis/search`；新增「站点无可用日期时不发请求且空状态说明无数据」
- [ ] 3.3 `pages/Analysis/index.test.tsx`：确认「切走再切回一个请求都不发」那组仍绿
      （自动查询绝不能穿透缓存短路）
- [ ] 3.4 `pages/Toolbox/GrowthMarket/index.test.tsx`：首屏用例反向，断言**未**发出
      `/toolbox/growth/search`；新增未查询态三条断言（无条数、无 dirty 标签、空状态文案）
- [ ] 3.5 `cd frontend && pnpm test` 全绿；`pnpm lint` 与 `pnpm tsc` 通过
- [ ] 3.6 `uv run pytest test/ -q` 全绿——本变更无后端改动，但改了 `CLAUDE.md`，
      `test_conventions.py` 会校验文档与配置的一致性

## 4. 文档与规格

- [ ] 4.1 `CLAUDE.md`：分析查询屏一节里「首屏不查数据表」那条整条删除，改写为首屏自动
      查询；空表格那条的判据同步改成「有无可查日期」
- [ ] 4.2 `CLAUDE.md`：工具箱 · 持续增长市场一节补上「首屏不自动查询」及其三处配套义务
- [ ] 4.3 手动验证：起 dev server，进分析屏确认直接出数据；切到增长屏确认表格空、
      点「立即查询」才出数据；两屏来回切一次确认分析屏不重发请求
- [ ] 4.4 `openspec validate --changes` 通过
