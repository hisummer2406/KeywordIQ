# 任务

> 2026-11-13 Claude（Opus 5.5）认领 #7，分支 `change/refine-opportunity-chat-ui`，worktree `~/.claude-worktrees/refine-opportunity-chat-ui`。用户选定 react-markdown；改前/改后截图由用户截取。

## 1. 改前核实

- [ ] 1.1 用户截取改前截图（场景清单见 PR 说明）：IP 专家任务对话各阶段、档案追问、窄窗口

## 2. 共用表现层与受限 Markdown（design D1、D2；admin-spa 场景「回答含 HTML 或脚本」「回答含链接或图片语法」「回答含标题、列表与表格」）

- [x] 2.1 新增依赖 `react-markdown@10.1.0`、`remark-gfm@4.0.1`
- [x] 2.2 `chat/AnswerMarkdown.tsx`：HTML 转字面文本插件、元素白名单、链接/图片降为文字、`urlTransform` 置空、表格与代码块横向滚动、段内换行保留
- [x] 2.3 `chat/roles.tsx`：user/ai/event 三种角色、`MetaLine`、`CitationTags`
- [x] 2.4 用例：`<script>`/`<img onerror>` 字面显示且无元素；`javascript:` 链接与图片无 `<a>`/`<img>`；标题、列表、表格结构；表格在滚动容器内

## 3. IP 专家任务对话（design D3、D4；场景「按进度突出主要动作」「检索记录折叠但失败可见」「检索方案与分析仍为纯文本」「回车只发送追问」「对话输入忙时不可提交」）

- [x] 3.1 `nextAction` 纯函数及用例（每一行条件）
- [x] 3.2 顶栏收为任务名 + 元信息行；定时复检、移动工作空间、任务记录进入「更多」
- [x] 3.3 输入区一个主按钮 + 「更多」，禁用项写明原因；占位符合并调用说明
- [x] 3.4 消息映射为 user/ai/event；标题去掉模型名，元信息移到底部
- [x] 3.5 检索事件折叠摘要（失败/未知数可见），展开显示逐条结果
- [x] 3.6 Agent 回答用 `AnswerMarkdown`；方案、分析保持纯文本
- [x] 3.7 更新 `Conversation.test.tsx`、`MessageItems.test.tsx`：既有计费确认、互斥、回车只追问用例保持通过

## 4. 档案追问（design D5）

- [x] 4.1 `AskPanel` 改为一问一答两种气泡，回答用 `AnswerMarkdown`，引用与模型名进底部
- [x] 4.2 更新 `AskPanel.test.tsx`：排队、完成、失败、未知四种状态与引用标签

> 实现位置：`frontend/src/pages/Opportunity/chat/{AnswerMarkdown.tsx,roles.tsx,chat.less}`、`IpAgent/{Conversation,MessageItems,nextAction}.ts(x)`、`Dossier/AskPanel.tsx`；用例在同目录 `*.test.ts(x)`。
> 2026-11-13 Claude：用例已写，未手动运行前端检查（按用户约定由提交门禁/CI/用户执行），结果见 5.2。

## 5. 验证与交付

- [x] 5.1 `openspec validate --strict --all`（50 passed）、`git diff --check` 通过
- [ ] 5.2 前端 `pnpm lint && pnpm tsc && pnpm test`（由用户执行，按记忆约定）
- [ ] 5.3 浏览器实屏验收（用户）：改后截图、长回复/长标题/检索式/窄窗口无横向溢出、自动滚动与输入区可达、键盘焦点与对比度
- [ ] 5.4 开 PR（`--base v2`，备用仓库），附 Refs #7、spec delta、截图与实际验证
