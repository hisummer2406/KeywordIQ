> 进度记录：2026-11-13 Claude（Opus 5.5）认领 `change/upgrade-antd-v6`，worktree `~/.claude-worktrees/upgrade-antd-v6`（基于 v2 `0938f1b`）。探路结论见 design Context（一次性分支 `probe/antd6-upgrade`，不合入）。用户授权「开始迁移」；合并与发布另行授权。
>
> 本变更不改后端行为，没有 `uv run pytest` 测试任务。前端 `pnpm lint`、`pnpm tsc`、`pnpm test` 以及浏览器验收由用户执行，实现方只跑 `pnpm install` 和 `pnpm build`。

## 1. 依赖与构建配置（Requirement「前端组件库版本基线」；design D1–D3）

- [x] 1.1 `frontend/package.json`：`antd` 升到 `^6.7.0`，`@ant-design/icons` 升到 `^6`。更新 `pnpm-lock.yaml`，并用 `pnpm why antd` 确认项目代码只解析到一份 antd 6（场景「依赖主版本一致」）。实测：`pnpm why antd` 项目侧只有 antd 6.7.0、icons 6.3.4；锁文件里另有 `antd@4.24.16`，基线 v2 上就有，是 `@umijs/plugins` 自带那份 pro-components 的 peer，与本变更无关
- [x] 1.2 `frontend/pnpm-workspace.yaml`：加一条 `peerDependencyRules.allowedVersions`，只针对 `@ant-design/pro-components` 及其子包与 antd 6，并注明原因、验证依据和日期（2026-11-13 探路）。不加全局放宽配置。确认 `pnpm install` 不再报这些 peer 告警，其他 peer 告警仍然显示（场景「peer 越界被显式记录」；D2）。实测：不带锁文件重新解析，加规则前 11 条 antd 相关 unmet peer（pro-components 及其 10 个子包），加规则后 0 条；其余 9 条（react 16、sass、postcss 等）是基线告警，仍然显示，所以规则只列了这 11 个包
- [x] 1.3 `frontend/.umirc.ts`：加 `esbuildMinifyIIFE: true` 并写注释说明原因。`build` 脚本改为 `max build && max esbuildHelperChecker dist`，`pnpm build` 通过，日志输出 `No conflicts found`（场景「生产构建通过产物检查」；D3）。实现时发现：开了 IIFE 后 Umi 内置的检查会提前返回、不再执行，所以串了独立命令；并临时关闭 IIFE 复现冲突，独立命令以退出码 1 失败，已确认它真的拦得住，临时配置已删

## 2. 清理废弃 API（Requirement「项目代码不使用组件库的废弃 API」；design D4）

- [x] 2.1 Space/Space.Compact 的 `direction` 改成 `orientation`，共 40 处。用脚本只改开标签上的顶层属性；先 dry-run，数量与 grep 统计吻合，替换后 `direction=` 剩余 0、`orientation="vertical"` 为 40
- [x] 2.2 Alert 的 `message` 改成 `title`（约 48 处，替换后脚本复查 Alert 开标签里 `message=` 剩余 0）；Drawer 的 `width`/`height` 改成 `size`；Table 分页 `position` 改成 `placement`（后两项是测试输出里的告警发现的，第一遍 grep 漏了）；Modal/Drawer/`modalProps` 的 `destroyOnClose` 改成 `destroyOnHidden`（13 处）
- [x] 2.3 零散项（另：`Conversation.tsx` 的 Select `suffixIcon` 改成 `suffix`，该页面没有测试渲染过，是 X 接入变更实现时发现的；之后用 antd 类型定义里全部 `@deprecated` 属性做了一次系统扫描，仅此一处遗漏）：Tag `bordered={false}` 改成 `variant="filled"`（ExpertsView）；Spin `tip` 改成 `description`（SsoCallback）；Descriptions `labelStyle` 改成 `styles.label`（MessageItems）；InputNumber `addonAfter` 改成 `Space.Compact` + `Space.Addon`（ScheduleModal）。**范围说明**：按 antd 6.7.0 运行时 `warning.deprecated` 和类型定义核对后确定；`size="middle"`、Descriptions/List 的 `bordered` 不发警告，Tooltip `overlay*`/`Menu.Item` 只在 `.umi/` 生成目录，均不改（design D4）
- [x] 2.4 `pnpm build` 已通过（含 `No conflicts found`）。测试输出里的 antd 告警已逐类核对：Table `pagination.position`、Drawer `height` 已清掉；`List` 废弃（5 次）为登记例外（design D4）；`[antd: message] Static function can not consume context`（4 次）不是废弃告警，是项目里静态调用 `message.xxx` 的既有写法，未改动。**待用户在浏览器里确认**开发环境控制台没有项目代码触发的废弃告警（场景「开发环境无项目代码触发的废弃告警」） ——2026-11-13 用户在浏览器验收，未报告控制台告警问题（只有总体结论，没有逐页记录）

## 3. DOM 相关的样式与测试（design D5）

- [x] 3.1 逐条核对 `global.less`、`AnalysisTable.less`、`ScopeBar.less`、`Login/index.less` 以及 `Analysis/index.tsx` 里 22 个不同的 `.ant-*` 类，方法是在 antd 6.7.0 和 rc 组件源码里确认类名仍在生成。只有 Select 的 `.ant-select-selector` 失效（根节点 `.ant-select` 接管了边框和高度变量），`ScopeBar.less` 已改成选根节点；其余（Menu、Pagination、Sider、Table、affix-wrapper）都还在，`.ant-pro-*` 来自 pro-components 未变。视觉效果待用户在浏览器里确认
- [x] 3.2 测试适配，只改定位方式、不改断言含义。静态核对改了 Select 相关 6 处（`ScopeBar.test`、`defaultsAndFirstPaint.test`、`scopeAndFilters.test`、`ImportModal.test`）；实际跑 `pnpm test` 后又修 5 个失败，原因都是 antd 6 的 DOM 变化：
  分页页码的点击事件挂到了 `li` 里的 `<a role="button">`（`exportAndPaging.test` 2 处，改成点 `li` 里的按钮）；
  Select 已选值节点 `pointer-events: none`，开下拉的 mouseDown 在根节点上（`scopeAndFilters.test` 改点 `.scope-bar__site`）；
  message 提示条也带 `role="alert"`，`Upload` 回显条不能再按 role 取唯一一个（`Upload/index.test` 改按回显条独有文案定位）。
  另有 1 个用例（`scopeAndFilters` 的"点开始筛选发一次"）在第一次全量运行时 20 秒超时、单独跑 2.7 秒通过、第二次全量运行通过，未发现逻辑原因，按并行负载下的抖动记录，之后再复现要重新查
- [x] 3.3 Tag 默认右边距等默认值变化：只在浏览器验收发现可见回归时才补，补的位置和原因写在本条 ——验收中未报告可见回归，无需补，故未改动任何默认值

## 4. 验证与交付

- [x] 4.1 在 worktree 的 `frontend` 下跑全量（`.umirc.ts` 是全局配置，不能用 related）：`pnpm lint` 通过；`pnpm tsc` 通过；`pnpm test` 62 个文件 650 个用例全部通过（Duration 87s）。以上针对本地工作区，PR 的审核 head 提交后以 CI 与门禁结果为准
- [x] 4.2 用户浏览器验收：布局与菜单、市场分析（筛选、分页、导出）、词库、竞对（ProTable、导入弹窗）、导入历史、用户管理（ModalForm）、定价、机会中心、登录页（账号密码和钉钉入口都要显示），并确认控制台没有报错 ——2026-11-13 用户验收通过，未发现问题
- [x] 4.3 `git diff --check`、`openspec validate --strict --all` 通过；OpenSpec 文档单独一个提交（`05f3cf0`）；代码合成一个提交，因为提交门禁每次跑前端 vitest，依赖升级单独提交时测试必然是红的（design 风险与取舍）。只暂存本变更的路径
- [x] 4.4 开 PR（`--base v2`），写上变更 id、Issue、spec delta 文件列表、实际跑过的验证和剩余风险 ——PR #192，Issue #191（后续事项：pro-components 3 与 List 迁移）；CI 因 GitHub 账号付款/额度问题未能运行（0 个步骤），不是代码失败，待恢复后重跑，合并前必须以 PR head 的 CI 结果为准
- [ ] 4.5 合并后清理一次性探路：worktree `~/.claude-worktrees/probe-antd6`、本地分支 `probe/antd6-upgrade`、8012 端口的 dev server、`keywordiq_demo_dossier` 库里的验收账号 `antd6probe`（删账号前先问用户）
