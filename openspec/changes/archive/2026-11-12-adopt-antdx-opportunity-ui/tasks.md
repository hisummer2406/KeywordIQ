> 进度记录：2026-11-13 Claude（Sonnet 5.5）受 keywordiq-84 调度认领 `change/adopt-antdx-opportunity-ui`，worktree `~/.claude-worktrees/adopt-antdx-opportunity-ui`，
> 原基于 `change/upgrade-antd-v6`；升级 PR #192 已合入 v2（`7d7c006`），2026-11-13 已把分支快进到 v2 `7d7c006`（无冲突，分支上没有自己的提交，改动仍是未提交的工作区改动）。
> 2026-11-13 用户说「继续做」后开始实现；design 里三个待决问题按推荐处理（纳入 NewTaskComposer、不渲染 Markdown、不用 ThoughtChain/专家标签 Prompts），用户未另行答复，可随时改。
> 用户同意我**定向**跑本变更新增的 vitest；lint 与前端全量仍由用户跑。
> 不推送、不开 PR、不合并、不发布；PR 要等 `upgrade-antd-v6` 合入 v2 后基于 v2 开。

## 1. 先量风险（实现前，design D1、D3、D9 与「风险」）

- [x] 1.1 `pnpm build` 基线（接入前）记 `dist` 总量与各 chunk；接入 X 后再跑一次对比，检查产物里有没有 mermaid；超标则改深导入（D1）。实测：dist 4584 KB → 4696 KB（+112 KB，+2.4%），JS 合计 4235223 → 4353154 字节；产物里没有 mermaid 库，只有 X 自带英文文案里的 "Mermaid" 字样；`No conflicts found`。不需要深导入
- [x] 1.2 最小渲染用例确认 jsdom 下 `Bubble.List`、`Sender`、`Conversations` 可测；缺 `ResizeObserver`/`scrollTo` 等 polyfill 时加进 `src/test/setup.ts`（全局文件，需前端全量）。实测：只缺 `IntersectionObserver`（`Bubble.List` 的滚动兼容 hook 用），已在 `setup.ts` 补桩；**这是全局测试配置，前端全量必须跑，由用户跑**
- [x] 1.3 2026-11-13 用户在浏览器验收通过（总体结论，未逐项记录；下面三点没有单独的实测记录，不要写成「三点都实测过」）。验收前这三点是「未验证」。三点 jsdom 之外的验证（要在浏览器里逐点核对，开发服务 `http://127.0.0.1:8013`，进「机会中心」，需超管账号；后端接 8010）：
  1. **输入法回车**：任务对话页底部输入框，用中文输入法（拼音）输入，**选词时按 Enter**——期望只确认候选词，输入框内容保留、**不发送**（网络面板里没有 `POST …/messages`）；选完词再按一次 Enter 才发送，且只出现一个追问请求，没有检索或取证请求。档案页追问框同样验证一遍。jsdom 里只验证了带 `isComposing` 的 Enter 不提交（`Conversation.test`），真实输入法行为要在浏览器里看。
  2. **轮询时滚动**：打开一个消息较多的任务，向上滚动离开底部；让任务在途（发起一次追问或在对话里发起检索，在途时每 3 秒轮询一次），在途期间保持停在历史位置——期望**不被拽到底部**；回到底部时，新消息到来仍自动滚到底。做不到则保留手动「靠近底部才滚到底」的逻辑（design D2、风险）。
  3. **X 内置文案是否英文**：看对话输入框、侧栏「新建工作空间」「向专家发起任务」按钮、气泡里是否出现英文（如 Sender 的发送/取消提示、Conversations 的菜单）。已知 X 的默认 locale 是英文（打包产物里有 `editableOk:"OK"` 之类的字符串），但本变更没有用到可编辑气泡、语音等带内置文案的功能，预期不出现；若出现，在机会中心页面根节点加 `XProvider` 并传 `zh_CN`（design D9）。
  4. 另外请顺带看：宽卡（检索结果、分析）宽度是否占满、气泡头部（标题、时间）排版、侧栏三组外观，这几项我只在 jsdom 验过结构（tasks 4.3）。

## 2. 先补回归用例（换壳前写，换壳后必须仍通过；Requirement「对话类界面的组件与安全边界」）

- [x] 2.1 `Conversation`：回车只调 `askResearch`，不调 `requestSearch`/`requestDetail`；`busy` 时不可提交且不发请求；大模型未配置时不可提交（场景「回车只发送追问」「对话输入忙时不可提交」）
- [x] 2.2 `MessageItems`/`AnalysisCard`：含 `<script>`、`<img onerror>`、Markdown 链接的模型文本按字面显示，DOM 里没有对应元素（场景「大模型返回含标记的文本」）
- [x] 2.3 `MessageItems`：每种时间线项的关键文案与按钮仍在。`MessageItems.test.tsx` 7 条覆盖检索、取证、分析（含模型文本按字面显示）、签署、任务失败、结果未知、定时跳过；简报、方案、待确认卡、在途进度在 `Conversation.test.tsx`。补用例时没有发现实现缺口
- [x] 2.4 `Sidebar`：导航切换、工作空间切换、重命名、删除（有任务时确认按钮禁用）、任务点击、加载更多
- [x] 2.5 `AskPanel`：建议问题点击即发送；忙时不可发送
- [x] 2.6 `NewTaskComposer`：产品名称必填、描述可空仍可发送；折叠区内字段（关键词、洛迦诺格式）仍参与校验

## 3. 实现（design D1–D9）

- [x] 3.1 `pnpm add @ant-design/x@^2.10.0`（已在 worktree 安装，`package.json`/锁文件待提交）；确认 `pnpm why antd` 仍只有一份 antd 6
- [x] 3.2 `Conversation.tsx` + `MessageItems.tsx`：`Bubble.List`，删自写 `Bubble` 外壳与手动滚动；3 处 `<List>`（方案、检索结果）改普通结构（D2、D5）
- [x] 3.3 `AnalysisCard.tsx`：信号 `<List>` 改普通结构（D5）
- [x] 3.4 `Conversation.tsx` 追问输入、`AskPanel.tsx` 输入与建议问题：`Sender`、`Prompts`；不提供 `onCancel`（D3）
- [x] 3.5 `NewTaskComposer.tsx`：按 D3 改（待用户决定是否纳入）
- [x] 3.6 `Sidebar.tsx`：三组 `Conversations`，删 `Row`/`SectionTitle`（D4）
- [x] 3.7 `NewTaskView.tsx`：`Welcome`（D6）
- [x] 3.8 清理（`grep` 已无 `List`；`Opportunity` 目录定向 vitest 无项目代码的 antd 废弃告警；顺手修了 `Conversation.tsx` 工作空间下拉的 `suffixIcon` → `suffix`，那是升级变更漏掉的，因为没有测试渲染过它）：`grep` 确认 `Opportunity/IpAgent` 下不再有 `antd` 的 `List`；测试输出里没有项目代码触发的 `[antd: …] … is deprecated`（`BatchAddResult` 的 `List` 除外）

## 4. 验证与交付

- [x] 4.1 2026-11-13 在 worktree 的 `frontend` 下由实现方跑（用户经 keywordiq-84 转达：lint 与全量由实现方跑），基于 v2 `7d7c006` + 本变更未提交改动，输出存文件看过：
  - `pnpm lint`：第一次 1 个错误（`Conversation.tsx` 留下未使用的 `latestPlanId`，是本变更引入），已删；重跑通过，0 错误 0 警告。
  - `pnpm tsc`：通过。
  - `pnpm test` 全量（`src/test/setup.ts` 是全局文件，不能用定向代替）：**第一次** 66 个文件里 3 个失败、5 条失败（`Analysis/exportAndPaging`「导出用当下生效的条件」、`Analysis/scopeAndFilters`「顶部立即查询…」各 1 条 20 秒超时；`ImportHistory/index` 3 条，含 20 秒超时与找不到文件名文本），总耗时 205 秒，均不在本变更改动的文件里；**不改代码、直接重跑全量**：**第二次** 66 个文件 671 条全部通过，耗时 120 秒。按负载抖动记录，没有查到逻辑原因；第一次的 5 条失败之后没有复现，若再出现要重新查。
  - `pnpm build`：通过，`No conflicts found`（见 1.1）。
  - 测试输出里的告警：无新的 `[antd: …] … is deprecated`；`[antd: List]` 5 次来自登记例外 `Competitor/BatchAddResult`，`[antd: message] Static function…` 4 次是既有写法，两者数量与升级变更一致；新增 `[antdx: XNotification] Notification API is not supported in this environment.` 5 次，是 jsdom 没有 Notification API，不影响结果。
  - 2026-11-13 补完 `MessageItems.test.tsx` 后**再跑一遍**：`pnpm lint` 通过、`pnpm tsc` 通过、`pnpm test` 全量 67 个文件 678 条全部通过（耗时 102 秒，无失败、无跳过），告警同上（无新的 antd 废弃告警）。
  - **没跑**：浏览器验收（4.3，交给用户）；官方 CI（账单问题可能没有步骤，见 4.4）。
- [x] 4.2 `openspec validate --strict --all` 49 项通过、`git diff --check` 无输出
- [x] 4.3 2026-11-13 用户在浏览器验收通过（总体结论，未逐项记录）。验收清单：用户浏览器验收：新建任务（含从档案带入）、对话页消息流各类卡片、追问发送、忙时禁用、翻看历史时轮询不拽滚动、侧栏三组切换/重命名/删除工作空间/加载更多、档案页追问、控制台无报错
- [x] 4.4 rebase 到 v2 已完成（见顶部）。用户已授权提交与推送；PR 开在备用仓库 `codingwork-zhang/KeywordIQ`（base `v2`），由 keywordiq-84 推送并开，开发中用 Draft（`.github/ci.md`）；主仓 PR 等账号切换另行决定。PR 描述的验证记录要写清本地跑了什么、没跑什么（官方 CI 可能因账单问题没有步骤，见 4.1）
