# ABA 数据选品作用域栏居中实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ABA 数据选品页面第一行的全部控件作为一个整体水平居中，同时保持现有查询行为和响应式换行能力。

**Architecture:** 保留 `ScopeBar` 当前以 Ant Design `Space` 作为根容器的结构，只在根容器增加 `justifyContent: 'center'`。用独立组件测试锁定布局契约，再运行现有分析页测试、类型检查和生产构建，最后在宽屏、窄屏和条件提示状态下做视觉验证。

**Tech Stack:** React 18、TypeScript 5.7、Ant Design 5、Umi Max 4、Vitest 2、Testing Library、jsdom。

## Global Constraints

- 站点、视图、日期、搜索框、立即查询按钮和条件修改提示必须作为一个整体水平居中。
- 保留现有控件顺序、宽度、间距、props 和交互行为。
- 保留 `wrap`；窗口宽度不足时允许换行，并保持换行内容居中。
- 不修改后端接口、数据模型、查询参数、状态管理或其他页面区域。
- 不新增依赖，不重构页面整体布局。
- 实现必须先有失败测试，再写最小代码使其通过。

---

## 文件结构

- Create: `frontend/src/pages/Analysis/ScopeBar.test.tsx` — 只验证作用域栏自身的布局契约，不加载完整分析页面或调用接口。
- Modify: `frontend/src/pages/Analysis/ScopeBar.tsx` — 在现有根 `Space` 上增加主轴居中样式。
- Reference: `docs/superpowers/specs/2026-08-04-analysis-scope-bar-centering-design.md` — 已确认的设计和验收边界。

---

### Task 1: 用测试驱动实现作用域栏整体居中

**Files:**
- Create: `frontend/src/pages/Analysis/ScopeBar.test.tsx`
- Modify: `frontend/src/pages/Analysis/ScopeBar.tsx:47`
- Test: `frontend/src/pages/Analysis/ScopeBar.test.tsx`

**Interfaces:**
- Consumes: `ScopeBarProps`、`ScopeDraft` 以及现有 Ant Design `Space` 根容器。
- Produces: 根容器内联样式 `width: 100%`、`justify-content: center`，并继续具有 Ant Design `Space` 的换行类名 `ant-space-wrap`。

- [ ] **Step 1: 创建只验证布局契约的失败测试**

在 `frontend/src/pages/Analysis/ScopeBar.test.tsx` 写入：

```tsx
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ScopeBar from './ScopeBar';

describe('ScopeBar', () => {
  it('将整组作用域控件水平居中并保留自动换行', () => {
    const { container } = render(
      <ScopeBar
        sites={[{ code: 'US', name_zh: '美国', domain: 'amazon.com' }]}
        availableDates={['2026-07-18']}
        scope={{ site: 'US', view: 'DAY', date: '2026-07-18' }}
        q=""
        dirty={false}
        onScopeChange={vi.fn()}
        onQChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const scopeBar = container.firstElementChild;
    expect(scopeBar).toHaveStyle({ width: '100%', justifyContent: 'center' });
    expect(scopeBar).toHaveClass('ant-space-wrap');
  });
});
```

- [ ] **Step 2: 运行新测试，确认它因缺少居中样式而失败**

Run:

```bash
cd frontend
pnpm test -- src/pages/Analysis/ScopeBar.test.tsx
```

Expected: 测试失败；根容器具有 `width: 100%` 和 `ant-space-wrap`，但缺少 `justify-content: center`。

- [ ] **Step 3: 写入最小实现**

把 `frontend/src/pages/Analysis/ScopeBar.tsx` 的根 `Space` 从：

```tsx
<Space size="middle" wrap style={{ width: '100%' }}>
```

修改为：

```tsx
<Space
  size="middle"
  wrap
  style={{
    width: '100%',
    justifyContent: 'center',
  }}
>
```

不要修改任何子控件、props、事件处理或条件渲染。

- [ ] **Step 4: 运行新测试，确认布局契约通过**

Run:

```bash
cd frontend
pnpm test -- src/pages/Analysis/ScopeBar.test.tsx
```

Expected: `ScopeBar.test.tsx` 全部通过。

- [ ] **Step 5: 运行现有分析页回归测试**

Run:

```bash
cd frontend
pnpm test -- src/pages/Analysis/index.test.tsx src/pages/Analysis/filters.test.ts
```

Expected: 分析页作用域解析、显式查询、类目筛选、导出和空日期保护相关用例全部通过。

- [ ] **Step 6: 运行前端静态检查和生产构建**

Run:

```bash
cd frontend
pnpm tsc
pnpm build
```

Expected: TypeScript 无错误，Umi Max 生产构建成功生成 `frontend/dist/`。

- [ ] **Step 7: 启动页面并完成三种视觉状态验证**

Run:

```bash
cd frontend
pnpm dev --port 5174
```

在浏览器打开开发服务器输出的 ABA 数据选品页面，依次验证：

1. 宽屏：站点、视图、日期、搜索框和立即查询按钮作为一组水平居中。
2. 条件提示：修改任一条件但不查询，橙色提示出现后整组仍然居中。
3. 窄屏：缩窄到控件需要换行，确认无横向滚动，换行后的控件仍然居中。

Expected: 三种状态均符合设计文档；查询控件仍可正常操作。

- [ ] **Step 8: 检查最终差异并提交功能代码**

Run:

```bash
git diff --check
git status --short
git diff -- frontend/src/pages/Analysis/ScopeBar.tsx frontend/src/pages/Analysis/ScopeBar.test.tsx
git add frontend/src/pages/Analysis/ScopeBar.tsx frontend/src/pages/Analysis/ScopeBar.test.tsx
git commit -m "style(frontend): center analysis scope bar"
```

Expected: 提交只包含 `ScopeBar.tsx` 和 `ScopeBar.test.tsx`，不包含 `frontend/dist/`、缓存、日志或其他无关文件。

---

## 试点复盘记录

功能提交完成后，在交付说明中记录以下四项，不额外创建平台代码：

- 需求从提出到确认的关键口径：整行全部控件整体居中。
- 实际修改边界：一个组件和一个组件测试，无后端变化。
- 自动验证与视觉验证的真实结果。
- 这套“设计确认 → 实施计划 → TDD → 构建 → 视觉验收”流程中需要保留或简化的步骤。

本次不创建独立 Worktree 或并行 Agent。需求只有一个实现单元，拆分会增加协调和合并成本，不能验证有效的并行收益。
