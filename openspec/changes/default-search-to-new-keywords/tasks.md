## 1. 默认值与更名

- [ ] 1.1 `pages/Analysis/filters.ts`：`emptyFilters` 更名为 `defaultFilters`，`isNew` 从
      `undefined` 改为 `true`。函数上方注释写明「这是进屏与重置共用的那一份默认」，并点出
      `isNew: true` 是默认值而非空值——名字曾经叫 `emptyFilters`，改名的理由就在这里
- [ ] 1.2 `pages/Analysis/index.tsx`：4 处引用改名（`:68` 初始草稿、`:223` `submitScope`、
      `:248` `resetFilters`、`:320` 首屏自动查询）。改名靠 `tsc` 全量兜底，漏改即编译不过
- [ ] 1.3 `index.tsx:223` `submitScope` 处补注释：它产出的筛选值通常被 `appliedRef.current`
      覆盖，**但 `appliedRef.current` 为 null 时（还没查过就点作用域栏「立即查询」）会被
      整体采用**，故此处依赖 `defaultFilters()` 的值，改默认值时这里也跟着变（决策 3）
- [ ] 1.4 确认 `resetFilters` 仍保留 `q`（`{...defaultFilters(), q: current.q}`）——重置的是
      过滤区，不该清掉作用域栏的检索框

## 2. 测试

- [ ] 2.1 `filters.test.ts`：把 `is_new` 三态映射那组（当前 `:99-101`）改为显式传值
      （`isNew: true` / `false` / `undefined`），不再依赖基线恰好是 `undefined`
- [ ] 2.2 `filters.test.ts`：新增一条用例断言 `defaultFilters().isNew === true`，
      且 `toSearchBody(scope, defaultFilters()).is_new === true`——默认值必须有专门的
      断言守着，否则它下次被改动时没有任何测试会红
- [ ] 2.3 `filters.test.ts`：其余约 17 处把该工厂当「其他维度不筛」基线的用例，确认改名后
      仍绿（它们断言的是各自维度的映射，多一个 `is_new: true` 不改变结论）
- [ ] 2.4 `index.test.tsx`：首屏自动查询那条用例的请求体断言加上 `is_new: true`
- [ ] 2.5 `index.test.tsx`：新增用例——点「重置」后新词筛选回到「只看新词」而非「全部」，
      且检索框内容不变
- [ ] 2.6 `index.test.tsx`：确认「切走再切回一个请求都不发」那组仍绿，并新增/加强一条：
      用户把新词拨到「全部」后切走再切回，复原的是「全部」而不是默认值（决策 1）
- [ ] 2.7 `cd frontend && pnpm test` 全绿；`pnpm lint` 与 `pnpm tsc` 通过
- [ ] 2.8 `uv run pytest test/ -q` 全绿——本变更无后端改动，但改了 `CLAUDE.md`，
      `test_conventions.py` 会校验文档与配置的一致性

## 3. 文档与验收

- [ ] 3.1 `CLAUDE.md` 分析查询屏一节：在「首屏按默认作用域自动查一次」那条里补上默认筛选
      是「只看新词」，并写明重置回默认而非清空、不设第二个「恢复默认」入口的理由
- [ ] 3.2 手动验证：起 dev server，进分析屏确认①表格只有新词且新词控件显示「只看新词」；
      ②把新词拨到「全部」查询后切走再切回，复原的是「全部」；③点「重置」回到「只看新词」
      且检索框内容不变；④切到周视图筛选不变
- [ ] 3.3 确认首屏那次查询的掉榜提示文案仍然成立——首屏现在走带筛选路径，掉榜行不在结果
      内（design「风险」第三条）
- [ ] 3.4 `openspec validate --changes` 通过
