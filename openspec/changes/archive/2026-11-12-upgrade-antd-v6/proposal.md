## Why

机会中心的对话界面（气泡、输入框、会话列表）现在是用 antd 基础组件自己拼的。现成的 Ant Design X 当前主线 2.x 要求 `antd ^6.1`，而前端还停在 antd 5.29.3。X 1.x 虽然兼容 antd 5，但已经停止更新（最后一版 1.6.1，2025-09），用了它以后还得再迁一次。所以先单独把全站升到 antd 6。机会中心改用 X 2.x 是后续的另一个变更。

2026-11-13 已经在一次性分支 `probe/antd6-upgrade` 上探过路：
- 升级后构建能通过，前提是打开 `esbuildMinifyIIFE`。
- pro-components 2.8.10 用到的 124 个 antd 内部路径，在 antd 6.7.0 里全部存在。
- 用户在浏览器里验收了布局、ProTable、ModalForm 和机会中心，没有发现问题。

## What Changes

- **依赖**：
  - `antd` 从 5.29.3 升到 6.7.x。
  - `@ant-design/icons` 从 5 升到 6。antd 6 强制要求 icons 6，两者不能混用。
  - `@ant-design/pro-components` 保持 2.8.10。它官方只声明支持 antd 4 和 5，所以要在 pnpm 配置里**显式放行**这个 peer 依赖不匹配，并写明原因。不能靠忽略安装告警来过关。
- **构建配置**：`.umirc.ts` 加上 `esbuildMinifyIIFE: true`。原因是 antd 6 升级后，Umi 的 esbuild helper 检查报告各 chunk 的压缩 helper 重名冲突。开了这个开关后 Umi 内置的检查会被跳过，所以 `build` 脚本串上独立的 `max esbuildHelperChecker dist`，保证检查仍然强制执行。
- **清理项目代码里的 antd 6 废弃 API**：
  - Space 的 `direction` 改成 `orientation`（40 处）。
  - Alert 的 `message` 改成 `title`（约 48 处）。
  - Modal/Drawer 的 `destroyOnClose` 改成 `destroyOnHidden`（13 处）。
  - Drawer 的 `width`/`height` 改成 `size`；Table 分页的 `position` 改成 `placement`；Tag 的 `bordered={false}` 改成 `variant="filled"`；
    Spin 的 `tip` 改成 `description`；Descriptions 的 `labelStyle` 改成 `styles.label`；InputNumber 的 `addonAfter` 改成 `Space.Compact` + `Space.Addon`。
  - **例外**：`List` 组件整体被废弃，但替代品 `Listy` 不是等价接口，6 处 `<List>`（各带 `List.Item`）不在本次迁移，登记在 design D4。
  - 范围按 antd 6.7.0 实际会在运行时发警告的写法确定，不照抄迁移表：例如 `size="middle"` 在 6.7.0 里不发警告，项目代码也没有
    `size="default"`，所以不改；Tooltip 的 `overlay*`、`Menu.Item` 只出现在 Umi 生成的 `.umi/` 目录里，不是项目代码。
  - 目标是开发环境的控制台里，不再有项目代码触发的 antd 废弃告警。pro-components 内部触发的告警不在本次范围内。
- **适配 antd 6 的 DOM 变化**：逐条核对样式和测试里的 `.ant-*` 选择器。已确认失效的是 Select：antd 6 去掉了内层 `.ant-select-selector`，
  边框等样式挪到了根节点 `.ant-select`，已选值的类从 `.ant-select-selection-item` 变成 `.ant-select-content-value`，
  影响 `ScopeBar.less` 一处样式和 4 个测试文件里的 6 处定位。
- 不改任何业务行为、接口或数据。**不影响日报路径，也不影响周报路径**：本变更只涉及前端依赖和展示层，查询、导入和统计口径都不变。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `admin-spa`：新增"前端组件库版本基线"要求。内容包括：
  - antd 和 icons 的主版本基线；
  - pro-components 越过 peer 依赖声明的约束方式；
  - 构建产物检查不能关闭；
  - 项目代码不能使用 antd 废弃 API。

## Impact

- **前端依赖**：`frontend/package.json`、`frontend/pnpm-lock.yaml`、`frontend/pnpm-workspace.yaml`（peer 依赖放行规则）。
- **构建**：`frontend/.umirc.ts`。这是全局配置，按项目规则要跑前端全量检查。
- **代码**：`frontend/src` 下用到废弃 API 的页面和组件，涉及 3 个 `.less` 样式文件。
- **测试**：11 个用 `.ant-*` 选择器定位元素的测试文件，可能要随 DOM 变化调整。
- **不涉及**：后端、浏览器插件（`extension/` 不依赖 antd）、数据库、生产配置。
- **发布**：只要求重新构建前端产物，没有迁移，不需要改 `.env`。
- **后续**：机会中心改用 Ant Design X 2.x 是独立变更，要等本变更合入 v2 之后再开。
