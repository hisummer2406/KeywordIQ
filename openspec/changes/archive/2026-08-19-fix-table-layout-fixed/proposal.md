## Why

结果表格的列宽**没有生效**：列上写的 `width` 只是建议值，浏览器按内容抢宽度。1920 视口
实测，两列都声明 130px，渲染出来一列 206、一列 143——正是规格里明令禁止的那种表现。

根因是规格与代码都押在一条**已经失效的机制**上：rc-table 早期版本里「给了 `scroll.x`
就切 `table-layout: fixed`」成立，7.54 之后判据只剩四条（显式 `tableLayout` / 有固定列 /
有 `scroll.y` 或粘性头 / 某列开 `ellipsis`），横向滚动本身不再触发。升级没有任何报错，
列宽就这么悄悄退回建议值。见 issue #43。

**影响路径**：仅前端展示，日报与周报**两者**的结果表格同受影响（同一套列定义）。
不涉及导入、查询与任何接口。

## What Changes

- 分析查询屏与词库盯盘屏的 ProTable **显式声明 `tableLayout="fixed"`**——它是 rc-table
  判据的第一条，不随版本变动。
- `scroll.x` 保留，但职责说清楚：它决定窗口不够宽时**横向滚动**而不是把列压扁，
  与"列宽生不生效"是两件事。
- 修正代码注释与规格里那句失效的因果（"给了总宽 → 切固定布局"）。
- 补一条回归：断言渲染出来的 `<table>` 上确实是 `table-layout: fixed`，且每一列都有宽度。
- 顺带把 vitest 的 `testTimeout` 从默认 5 秒放宽到 20 秒：分析屏那类整屏渲染用例单条实测
  4 秒上下，贴着默认值，并行跑满 CPU 时零星翻红，且每次翻的用例都不同、报的是超时而非
  断言失败——那种随机红会让人开始忽略红灯。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `analysis-screen-layout`: 「结果表格的列宽必须真正生效且按信息密度分配」——把生效机制
  从"提供总宽即固定布局"改成"显式声明固定布局"，总宽的职责改述为横向滚动。

## Impact

- `frontend/src/pages/Analysis/index.tsx`、`frontend/src/pages/Toolbox/KeywordLibrary/index.tsx`
  （各加一个 `tableLayout="fixed"`）
- `frontend/src/pages/Analysis/columns.tsx`（注释）、`frontend/vitest.config.ts`（超时）
- `frontend/src/pages/Analysis/index.test.tsx`（两条回归）
- 不改后端、不改接口、无迁移
