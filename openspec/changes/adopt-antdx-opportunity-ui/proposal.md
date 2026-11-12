## Why

机会中心的对话界面（气泡、输入框、会话列表、欢迎页）是用 antd 基础组件手拼的：`MessageItems.tsx` 自带一个 `Bubble` 外壳，`Sidebar.tsx` 340 行里有自写的 `Row`（悬停、选中态、键盘）、
`Conversation.tsx` 自己管滚动到底部，三处输入框各自处理 Enter/Shift+Enter/输入法组合态。这些都是 Ant Design X 2.x 现成的能力。
前置的 `upgrade-antd-v6` 已把基线抬到 antd 6，X 2.x（要求 `antd ^6.1`）现在可以接入。
同时 `List` 在 antd 6 整体被废弃，`MessageItems.tsx`、`AnalysisCard.tsx` 里的 3 处 `<List>` 在该变更 design D4 里登记为例外，约定由本变更在重写这两个文件时清掉。

## What Changes

- **依赖**：新增 `@ant-design/x@^2.10.0`（peer：`antd ^6.1.1`、`react >=18`，满足）。**不**引入 `@ant-design/x-markdown`、`@ant-design/x-sdk`。
- **交互层替换**（只换壳，不改数据流和业务规则）：
  - `Bubble.List` / `Bubble` 替换 `MessageItems.tsx` 自写的 `Bubble` 与 `Conversation.tsx` 的消息容器和手动滚动；每种消息的卡片内容保留，作为 `contentRender`。
  - `Sender` 替换 `Conversation` 底部追问框、`Dossier/AskPanel` 的 `Input.TextArea` + 发送按钮；`NewTaskComposer` 的描述框改用 `Sender`，其余表单项进 `Sender` 的 `header`。
  - `Conversations` 替换 `Sidebar.tsx` 里自写的 `Row` 列表（导航项、工作空间、专家任务三组）。
  - `Welcome` 替换 `NewTaskView` 的标题与说明；`Prompts` 替换 `AskPanel` 的建议问题标签；`ExpertsView` 的专家名片和 `ThoughtChain` **不用**（见 design D6、D7）。
- **清掉 antd `List`**：`MessageItems.tsx` 2 处、`AnalysisCard.tsx` 1 处共 3 处 `<List>` 改成普通结构；`Competitor/BatchAddResult.tsx` 的 3 处不在本变更范围。
- **保留不动**：待你确认的检索式卡（`QueryComposer` 的业务逻辑与 Popconfirm 确认调用次数）、候选专利、覆盖事实、风险分析卡的内容、成果抽屉、档案下的定价/证据/决策/缺口面板——X 里没有对应组件，换壳也没有收益。
- **不变的约束**：
  - 不用 `useXAgent`/`useXChat`/`XRequest`。数据流仍是 `timeline.ts` 从已存事实拼消息、有在途任务时每 3 秒轮询。
  - Sender 的提交永远只等价于「追问」（只调大模型）；检索、取证仍然必须在卡片里确认调用次数后才入队。
  - 模型与供应商文本仍一律按纯文本渲染，不渲染 Markdown。
- **不影响日报路径，也不影响周报路径**：只涉及前端展示层，后端、接口、数据库、导入和统计口径都不动。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `admin-spa`：新增「对话类界面的组件与安全边界」要求——X 的版本基线、对话输入不得绕过计费确认、模型文本不渲染 Markdown。
  机会中心界面的具体布局属于展示实现，不写进规格，取舍写在 design。

## Impact

- **前端依赖**：`frontend/package.json`、`frontend/pnpm-lock.yaml`（X 及其传递依赖，含 `mermaid`、`react-syntax-highlighter`，见 design 风险）。
- **代码**：`frontend/src/pages/Opportunity/` 下 `Sidebar.tsx`、`NewTaskView.tsx`、`IpAgent/{Conversation,MessageItems,AnalysisCard,NewTaskComposer}.tsx`、`Dossier/AskPanel.tsx`；`timeline.ts` 的数据结构不变。
- **测试**：现有 UI 用例只有 `Dossier/DossierReview.test.tsx`，对话与侧栏没有组件测试；本变更要补（见 tasks）。
- **不涉及**：后端、浏览器插件、数据库、生产配置、`.env`。
- **发布**：只要求重新构建前端；回滚 revert 合并提交即可。
- **前置依赖**：PR 必须等 `upgrade-antd-v6` 合入 v2 之后再基于 v2 开，否则会把升级提交一并带进去。
