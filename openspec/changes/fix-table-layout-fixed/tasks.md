## 1. 修复

- [x] 1.1 分析查询屏与词库盯盘屏的 ProTable 加 `tableLayout="fixed"`
- [x] 1.2 修正 `columns.tsx` 与 `index.tsx` 里那句失效的因果（"给了总宽 → 固定布局"）

## 2. 回归

- [x] 2.1 在 `index.test.tsx` 补两条：渲染出的 `<table>` 是 `table-layout: fixed`、
      每一列（含复选框列）都带宽度
- [x] 2.2 验证这条回归确实能抓住问题：去掉修复后它必须红（已验，报
      `expected 'auto' to be 'fixed'`）
- [x] 2.3 `vitest.config.ts` 的 `testTimeout` 放宽到 20 秒，消除并行下的随机超时红

## 3. 验收

- [x] 3.1 浏览器实测两屏：`table-layout` 为 `fixed`，两个同宽声明的列渲染等宽
      （类目/品牌修复前 206 与 143，修复后相等）
- [x] 3.2 `pnpm lint && pnpm tsc && pnpm test` 全绿，全量连跑三次不翻红
- [x] 3.3 同步规格并归档（合并后）
