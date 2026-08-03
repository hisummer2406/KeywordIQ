# ABA 数据选品作用域栏居中与站点标识实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ABA 数据选品页面第一行的全部控件作为一个整体水平居中，并将站点展示为 `国旗 国家站（代码）`，同时保持现有查询行为和响应式换行能力。

**Architecture:** 保留 `ScopeBar` 当前以 Ant Design `Space` 作为根容器的结构，只在根容器增加 `justifyContent: 'center'`。新增一个无 UI 依赖的站点文案模块，根据 ISO 两位站点码生成 Emoji 国旗并组合中文站点名；组件只消费格式化结果。用纯函数测试和独立组件测试锁定契约，再运行现有分析页测试、类型检查和生产构建，最后在宽屏、窄屏、条件提示和站点下拉状态下做视觉验证。

**Tech Stack:** React 18、TypeScript 5.7、Ant Design 5、Umi Max 4、Vitest 2、Testing Library、jsdom。

## Global Constraints

- 站点、视图、日期、搜索框、立即查询按钮和条件修改提示必须作为一个整体水平居中。
- 站点选项和选中值必须展示为 `国旗 国家站（代码）`，例如 `🇺🇸 美国站（US）`。
- 国旗必须由 ISO 两位站点码计算，不维护逐站点映射，不新增图片资源。
- 保留现有控件顺序、宽度、间距、props 和交互行为。
- 保留 `wrap`；窗口宽度不足时允许换行，并保持换行内容居中。
- 不修改后端接口、数据模型、查询参数、状态管理或其他页面区域。
- 不新增依赖，不重构页面整体布局。
- 实现必须先有失败测试，再写最小代码使其通过。

---

## 文件结构

- Create: `frontend/src/pages/Analysis/ScopeBar.test.tsx` — 只验证作用域栏自身的布局契约，不加载完整分析页面或调用接口。
- Create: `frontend/src/pages/Analysis/siteLabel.ts` — 站点码转 Emoji 国旗以及站点统一文案的纯函数。
- Create: `frontend/src/pages/Analysis/siteLabel.test.ts` — 覆盖三个现有站点、代码规范化和非法代码回退。
- Modify: `frontend/src/pages/Analysis/ScopeBar.tsx` — 在现有根 `Space` 上增加主轴居中样式，并复用统一站点文案。
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

- [ ] **Step 7: 将视觉验证延后到站点标识完成之后**

站点标识是同一个组件上的追加需求。为避免启动两次开发环境和重复登录，本任务只完成自动验证，宽屏、条件提示和窄屏三种视觉状态统一放到 Task 2 Step 9 验证。

- [ ] **Step 8: 检查居中改动边界，暂不提交**

Run:

```bash
git diff --check
git status --short
git diff -- frontend/src/pages/Analysis/ScopeBar.tsx frontend/src/pages/Analysis/ScopeBar.test.tsx
```

Expected: 居中改动只涉及 `ScopeBar.tsx` 和 `ScopeBar.test.tsx`；与 Task 2 的站点标识改动一起完成最终视觉验证后再提交。

---

### Task 2: 用测试驱动实现国旗站点文案

**Files:**
- Create: `frontend/src/pages/Analysis/siteLabel.ts`
- Create: `frontend/src/pages/Analysis/siteLabel.test.ts`
- Modify: `frontend/src/pages/Analysis/ScopeBar.tsx:3,60`
- Modify: `frontend/src/pages/Analysis/ScopeBar.test.tsx`

**Interfaces:**
- Consumes: `SiteOption`，字段为 `code: string`、`name_zh: string`、`domain: string`。
- Produces: `siteCodeToFlag(code: string): string` 和 `formatSiteLabel(site: SiteOption): string`。
- Produces: `ScopeBar` 站点选择项 label 为 `🇺🇸 美国站（US）` 格式，value 仍为原始站点码。

- [ ] **Step 1: 创建站点文案纯函数的失败测试**

在 `frontend/src/pages/Analysis/siteLabel.test.ts` 写入：

```ts
import { describe, expect, it } from 'vitest';
import { formatSiteLabel, siteCodeToFlag } from './siteLabel';

describe('siteCodeToFlag', () => {
  it.each([
    ['US', '🇺🇸'],
    ['GB', '🇬🇧'],
    ['DE', '🇩🇪'],
    [' us ', '🇺🇸'],
  ])('将 %s 转为 %s', (code, flag) => {
    expect(siteCodeToFlag(code)).toBe(flag);
  });

  it.each(['', 'U', 'USA', '1S'])('非法代码 %s 不生成国旗', (code) => {
    expect(siteCodeToFlag(code)).toBe('');
  });
});

describe('formatSiteLabel', () => {
  it('组合国旗、中文站点名和全角括号代码', () => {
    expect(formatSiteLabel({ code: 'US', name_zh: '美国', domain: 'amazon.com' })).toBe(
      '🇺🇸 美国站（US）',
    );
  });
});
```

- [ ] **Step 2: 运行纯函数测试，确认模块尚不存在**

Run:

```bash
cd frontend
pnpm exec vitest run src/pages/Analysis/siteLabel.test.ts
```

Expected: FAIL，提示无法解析 `./siteLabel`。

- [ ] **Step 3: 实现最小纯函数模块**

在 `frontend/src/pages/Analysis/siteLabel.ts` 写入：

```ts
import type { SiteOption } from '@/services/upload';

const REGIONAL_INDICATOR_A = 0x1f1e6;

export function siteCodeToFlag(code: string): string {
  const normalized = code.trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(normalized)) return '';

  return [...normalized]
    .map((letter) => String.fromCodePoint(REGIONAL_INDICATOR_A + letter.charCodeAt(0) - 65))
    .join('');
}

export function formatSiteLabel(site: SiteOption): string {
  const code = site.code.trim().toUpperCase();
  const flag = siteCodeToFlag(code);
  return `${flag ? `${flag} ` : ''}${site.name_zh}站（${code}）`;
}
```

- [ ] **Step 4: 运行纯函数测试，确认全部通过**

Run:

```bash
cd frontend
pnpm exec vitest run src/pages/Analysis/siteLabel.test.ts
```

Expected: `siteLabel.test.ts` 全部通过。

- [ ] **Step 5: 先扩展组件测试，要求显示统一站点文案**

在 `ScopeBar.test.tsx` 的现有用例末尾增加：

```tsx
expect(container).toHaveTextContent('🇺🇸 美国站（US）');
```

Run:

```bash
cd frontend
pnpm exec vitest run src/pages/Analysis/ScopeBar.test.tsx
```

Expected: FAIL；当前组件仍显示 `美国 (US)`。

- [ ] **Step 6: 让 ScopeBar 使用统一文案函数**

在 `ScopeBar.tsx` 引入：

```ts
import { formatSiteLabel } from './siteLabel';
```

将站点 options 调整为：

```tsx
options={sites.map((site) => ({ label: formatSiteLabel(site), value: site.code }))}
```

不要修改 `value`、`onChange` 或站点接口类型。

- [ ] **Step 7: 运行新增测试和分析页回归测试**

Run:

```bash
cd frontend
pnpm exec vitest run \
  src/pages/Analysis/siteLabel.test.ts \
  src/pages/Analysis/ScopeBar.test.tsx \
  src/pages/Analysis/index.test.tsx \
  src/pages/Analysis/filters.test.ts
```

Expected: 四个测试文件全部通过，站点切换仍向接口发送原始站点码。

- [ ] **Step 8: 运行类型检查和生产构建**

Run:

```bash
cd frontend
pnpm tsc
pnpm build
```

Expected: TypeScript 无错误，Umi Max 生产构建成功。

- [ ] **Step 9: 完成最终视觉验证**

启动本地页面后验证：

1. 选中值显示 `🇺🇸 美国站（US）`。
2. 展开站点下拉，三个选项分别显示美国、英国和德国国旗站点文案。
3. 宽屏下全部控件整体居中。
4. 修改条件后提示出现，整组仍然居中。
5. 窄屏下正常换行，无横向滚动，换行内容居中。

- [ ] **Step 10: 检查最终差异并提交功能代码**

Run:

```bash
git diff --check
git status --short
git diff -- \
  frontend/src/pages/Analysis/ScopeBar.tsx \
  frontend/src/pages/Analysis/ScopeBar.test.tsx \
  frontend/src/pages/Analysis/siteLabel.ts \
  frontend/src/pages/Analysis/siteLabel.test.ts
git add \
  frontend/src/pages/Analysis/ScopeBar.tsx \
  frontend/src/pages/Analysis/ScopeBar.test.tsx \
  frontend/src/pages/Analysis/siteLabel.ts \
  frontend/src/pages/Analysis/siteLabel.test.ts
git commit -m "style(frontend): center scope bar and label sites"
```

Expected: 功能提交只包含上述四个前端文件。

---

## 试点复盘记录

功能提交完成后，在交付说明中记录以下四项，不额外创建平台代码：

- 需求从提出到确认的关键口径：整行全部控件整体居中。
- 实际修改边界：一个组件和一个组件测试，无后端变化。
- 自动验证与视觉验证的真实结果。
- 这套“设计确认 → 实施计划 → TDD → 构建 → 视觉验收”流程中需要保留或简化的步骤。

本次不创建独立 Worktree 或并行 Agent。需求只有一个实现单元，拆分会增加协调和合并成本，不能验证有效的并行收益。
