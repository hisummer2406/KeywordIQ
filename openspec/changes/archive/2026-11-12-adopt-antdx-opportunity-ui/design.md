## Context

机会中心对话界面由 antd 基础组件手拼，涉及文件与行数：`Sidebar.tsx` 340、`IpAgent/Conversation.tsx` 309、`IpAgent/MessageItems.tsx` 285、
`IpAgent/NewTaskComposer.tsx` 189、`Dossier/AskPanel.tsx` 134、`NewTaskView.tsx` 86。数据流是后端非流式：`timeline.ts` 从已存事实拼出消息序列，
有在途任务时每 3 秒 `getResearch` 轮询，没有就停。检索与取证必须在「待你确认的检索式」卡里确认调用次数后才入队；底部输入框发送的只是追问（只调大模型）。

前置：`upgrade-antd-v6`（本地分支 `change/upgrade-antd-v6`，提交 `05f3cf0`、`227ca3b`）已把前端升到 antd 6.7.0，本变更的分支基于它。

X 的 API 以安装后的包为准。`@ant-design/x@2.10.0` 已安装并读过类型定义：peer 为 `antd ^6.1.1`、`react >=18`；`sideEffects: false`；
依赖里带 `mermaid`、`react-syntax-highlighter`（供 `Mermaid`、`CodeHighlighter`，本变更不用）；包没有 `exports` 字段。

## Goals / Non-Goals

**Goals:**

- 气泡、输入框、会话列表用 X 的组件，删掉自写的 `Bubble` 外壳、`Row` 列表、手动滚动和各处重复的回车/组合态处理。
- 清掉 `MessageItems.tsx`、`AnalysisCard.tsx` 的 antd `List`（兑现 `upgrade-antd-v6` design D4 登记的例外）。
- 用户看到的行为不变：消息顺序、卡片内容、按钮的确认与禁用条件、URL 参数恢复、轮询节奏都不变。

**Non-Goals:**

- 不改后端、接口、数据库；不把后端改成 SSE，不上流式、打字机动画。
- 不引入 `x-markdown`、`x-sdk`；不用 `useXAgent`/`useXChat`/`XRequest`。
- 不改检索式确认卡、候选专利、覆盖事实、风险分析内容、成果抽屉、档案下各面板的业务逻辑。
- 不借此调整机会中心的页面结构与路由（仍是 `/opportunity` 单页、页内左栏，见 top-nav-structure）。

## Decisions

### D1：只装 `@ant-design/x`，用具名导入，先量产物再决定要不要深导入

`import { Bubble, Sender, Conversations, Welcome, Prompts } from '@ant-design/x'`。包标 `sideEffects: false`，生产构建应能摇掉 `Mermaid`、`CodeHighlighter`，
但 `mermaid` 体积大，不能凭这个标记放心。**先做 1.x 任务量一次**：接入前后各跑 `pnpm build`，对比 `dist` 总量和各 chunk，并检查产物里有没有 `mermaid` 的内容。
若出现 mermaid 或入口 chunk 明显变大，改为 `@ant-design/x/es/bubble` 这类深导入（包没有 `exports` 限制，路径可达）；仍不行再评估。

**备选**：一开始就深导入。写法更丑、升级时路径易变，先不选。

### D2：消息流用 `Bubble.List`，卡片内容走 `contentRender`，不重写业务卡

`timeline.ts` 的 `TimelineItem` 原样保留。映射成 `Bubble.List` 的 `items`（`key` 用 `item.key`）：

| 时间线项 | role | 说明 |
|---|---|---|
| `brief`、用户的 `message` | `user` | `placement: 'end'`，简报卡作为 `contentRender` |
| `plan`、`search`、`detail`、`analysis`、AI 的 `message` | `ai` | 业务卡各自作为 `contentRender`；宽卡（检索结果、分析）用 `styles`/`classNames` 放开宽度 |
| `review`、`task-failed`、`schedule-skipped` | `system` | 内容是现有的 `Alert`，`system` 角色没有头像和气泡底色，与现状一致 |
| 待确认检索式卡（`draft`） | `ai` | 作为列表最后的临时项，`QueryComposer` 不动 |
| 在途任务 | `ai`，`loading` | 用 `loadingRender` 保留「已排队/正在…」两行文案 |

`content` 放 `TimelineItem` 对象（`BubbleContentType` 允许 `AnyObject`），渲染逻辑集中在一个 `contentRender`，`MessageItems.tsx` 的 `switch` 保留，只去掉外壳。
头像、标题和时间用 `avatar`/`header` 槽位：专家头像和「我」头像沿用现在的颜色和字号。`typing`、`streaming` 一律不开（非流式，不能假装在打字）。
滚动到底部改由 `autoScroll` 负责，删除 `Conversation.tsx` 里 `scroller` 的手动 `scrollTop` 和对应 effect；**行为要求**：用户在翻看历史时，3 秒轮询刷新不能把页面拽到底部。
这点要实测，做不到就保留手动滚动（见风险）。

**备选**：只借用 `Bubble` 的样式、自己渲染列表。放弃了 `autoScroll` 与 role 配置，替换价值很小，不选。

### D3：`Sender` 只承担「追问」，不承担「执行」

- **专家任务对话**：`Sender` 的 `onSubmit` 调现有 `send`（即 `askResearch`）；`submitType` 用默认的 Enter 发送。
  任务忙（`busy`）或大模型未配置时用 `disabled`，说明文字放进 `placeholder`/`footer`，原来 Tooltip 上的解释保留（「等当前任务完成后再发送」「只调用大模型，不调用智慧芽、不计 MCP 预算」）。
  `loading` 只在追问请求进行中使用；**不提供 `onCancel`**——后端没有取消追问的能力，不能放出一个什么都取消不了的按钮。
- 「生成检索方案」「手写检索式」「生成风险分析」「签署人工意见」这几个快捷按钮**保留为普通 `Button`**，位置不变（Sender 上方）。它们是带禁用原因 Tooltip 的操作，`Prompts` 没有 Tooltip，也不是「点了就发消息」。
- **`Dossier/AskPanel`**：同样换成 `Sender`；`SUGGESTIONS` 的三个标签换成 `Prompts`，点击行为不变（直接发送该问题）。
- **`NewTaskComposer`**：`Sender` 的限制是内容为空时不触发 `onSubmit`（`Sender.js` 里 `submitDisabled = !innerValue`），而「产品描述」目前是可选的，产品名称才是必填。所以：
  描述框用 `Sender`（`value`/`onChange` 与 `Form.Item` 兼容），`suffix={false}`，发送用 `footer` 里自己的按钮走 `form.validateFields()`，不依赖 Sender 自带的提交。
  销售市场、法域、工作空间、关键词、洛迦诺分类放进 `Sender` 的 `header`，保留现有 `Form`，`Collapse` 的 `forceRender` 保留，保证折叠时仍参与校验。
  这一项收益主要是视觉统一，见「需要用户决定」。
- 输入法组合态下回车不得提交：X 的 Sender 是否已处理要在 1.x 任务里实测，没处理就用 `onKeyDown` 返回 `false` 拦截（类型允许 `void | false`），并为此写用例。

**备选**：`Sender` 的 `onSubmit` 兼作「发起检索」。违反检索必须先确认调用次数的约束，直接排除。

### D4：`Conversations` 替换侧栏的三组 `Row`，加载更多保留

侧栏现在有三块用同一个自写 `Row`：导航（机会档案、线索、专家、定时任务）、工作空间（全部、未归类、各工作空间）、当前范围下的专家任务。分别换成三个 `Conversations`：

- 导航：`items` 四项，`activeKey` 由当前 `view` 推出（机会档案对应 `dossiers`/`dossier`/`new-dossier` 三个视图）；无 `menu`。
- 工作空间：`items` 为 `all`、`none`、`ws-<id>`，`label` 带任务数，`activeKey` 由 `scope` 推出；`menu` 只给真实工作空间返回「重命名」「删除」，删除的 `Modal.confirm` 与禁用条件原样保留；「新建工作空间」用 `creation`。
- 专家任务：`items` 为任务，`label` 保留「产品名 + 状态点 + 候选数」两行，定时复检图标放进 `label`；`activeKey` 为当前任务；「加载更多」仍是列表下方的按钮（服务端分页每页 30，不改成无限滚动）。

不用 `groupable`：工作空间在这里是**筛选范围**而不是任务列表的分组，列表只显示选中范围的任务；改成分组展示会变成「全部任务按空间分组」，是行为变化，不做。
「移动任务到别的工作空间」目前在对话页顶部的下拉里，不在侧栏，本变更也不挪。
自写的悬停、选中、键盘处理（`Row`、`SectionTitle` 的悬停部分）随之删除。X 的会话条目带不带 `role`/键盘可达性要在 1.x 实测，不满足时补 `aria` 与 `tabIndex` 或保留 `Row` 里的键盘处理。

**备选**：只换任务列表，导航与工作空间保留 `Row`。改动小，但页面里会并存两种列表样式，所以不选。

### D5：antd `List` 直接换成普通结构，不迁到 `Listy`、也不借 ThoughtChain

- `MessageItems.tsx` 检索方案：`Space orientation="vertical"` + 每条一个带分隔线的块；检索结果列表同理（每条：检索式、状态 `Tag`、结果文案）。
- `AnalysisCard.tsx` 的信号列表同理。
- 这三处都是几条到十几条的静态展示，不需要虚拟列表。`Listy` 的接口（`data`/`itemRender`/`ref`）为大数据而设计，不适合；已在 `upgrade-antd-v6` D4 论证过。
- `Competitor/BatchAddResult.tsx` 的 3 处 `<List>` 不在本变更，仍登记为例外。

### D6：`Welcome` 只用在新建任务页；专家名片不换

`NewTaskView` 的标题和说明换成 `Welcome`。页面上的专家小标签现在是**不可点**的展示，换成 `Prompts` 就得发明点击行为，所以不用，保持原样。
`ExpertsView` 的名片带「可用/未接入」「负责阶段」「数据与工具」等业务信息，X 没有对应组件，不动。

### D7：不用 `ThoughtChain`

后端只提供任务的 `queued/running/completed/failed/unknown` 状态，没有分步进度，进度展示只有一行文案，用不上一条链。
检索结果的逐条状态倒是像链，但 `ThoughtChain` 的状态只有 `loading/success/error/abort`，没有「结果未知（unknown）」，而区分失败和未知是这个功能的硬要求（失败可重来，未知不能自动重发），所以不借用。

### D8：不引入 Markdown 渲染

`AnalysisCard` 明确写「模型文本一律作为纯文本渲染」，这是有意的安全取舍：模型输出来源于供应商返回的专利文本，是不可信输入。
`Bubble` 的 `contentRender` 里一律用 `Typography.Text`/`Paragraph` 加 `white-space: pre-wrap`，不使用任何会把字符串当标记解析的渲染器。写进规格（场景「大模型返回含标记的文本」）并有用例。
要不要渲染 Markdown 须另行决定，不在本变更。

### D9：不引入 `XProvider`，除非实测需要

页面已在 antd 的 `ConfigProvider`/`App` 里。X 组件读取主题 token；先不加 `XProvider`，在 1.x 任务里实测 X 组件的内置文案（如 `Sender`、`Conversations.Creation`）是否出现英文，出现再在机会中心页面根节点加 `XProvider` 并传 `zh_CN` locale。

## Risks / Trade-offs

- **[风险] 产物体积**：X 间接依赖 `mermaid`、`react-syntax-highlighter` → D1：接入后立刻量；超标回退深导入。
- **[风险] `autoScroll` 在 3 秒轮询刷新时抢滚动位置** → 实测；不行就保留手动「靠近底部才滚到底」的逻辑，只把气泡换成 X。
- **[风险] jsdom 下 `Bubble.List`/`Sender` 需要 `ResizeObserver`、`scrollTo`、布局尺寸** → 1.x 任务先写一个最小渲染用例确认可测；缺的 polyfill 放进 `src/test/setup.ts`（这是全局测试配置，改动要跑前端全量）。
- **[风险] 现有 UI 几乎没有测试**（只有 `DossierReview.test.tsx`），换壳后回归全靠新用例和用户验收 → tasks 里先补用例再换壳，用例断言用户可见行为，不依赖 X 的内部类名。
- **[取舍] `NewTaskComposer` 用 `Sender` 的收益主要是视觉统一**，且需要绕开「空内容不提交」→ 见「需要用户决定」。
- **[取舍] 与在飞变更的冲突面**：`add-opportunity-ip-agent` 的前端代码已随 PR #184 合入 v2（`343a3d1`），当前没有未合入的 IpAgent 前端改动；仍在飞的 `add-patent-fulltext-evidence`、`add-pricing-calculation` 不改这些文件，开 PR 前再 `git diff` 一次确认。

## Migration Plan

1. 只涉及前端，没有迁移、没有 `.env` 变更。
2. 先合 `upgrade-antd-v6`，再把本分支 rebase 到 v2 并基于 v2 开 PR（`--base v2`）；合入后在用户明确要求发布时走 `Deploy V2 to ECS`。
3. **回滚**：revert 合并提交并重新发布前端。

## Open Questions（需要用户决定）

1. **`NewTaskComposer` 是否纳入**：纳入（推荐，页面风格一致，做法见 D3）；或不纳入，保留现状，范围缩到对话、侧栏、追问。
2. **是否要渲染 Markdown**：本变更不做（D8）。如果你希望模型回答能显示加粗、列表，请单独决定，需要先评估注入面。
3. **是否同意不用 `ThoughtChain` 和 `Prompts`（专家标签）**：理由见 D6、D7。
