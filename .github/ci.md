# CI 用量控制

开发中的 V2 PR 使用 Draft；准备审核时点击 **Ready for review**，自动启动云端检查。
再次转为 Draft 会取消该 PR 尚在运行的旧检查。正式 PR 后续推送仍自动检查，新的提交会取消旧运行。

| 事件 / 改动 | 执行范围 |
|---|---|
| `push` 到 `main` | 按最近已验证祖先至目标 SHA 的累计改动检查，供发布门禁核验 |
| V2 草稿 PR | 不分配 runner |
| V2 正式 PR：`frontend/` | 前端 |
| V2 正式 PR：`extension/` | 插件 |
| V2 正式 PR：后端代码、迁移、测试、Python 依赖 | 后端 |
| V2 正式 PR：`docs/`、`openspec/`、根说明文档 | 只运行范围判定，三项检查按 job 条件跳过 |
| V2 正式 PR：CI、公共脚本、其他配置或未知路径 | 三项完整检查 |

混合改动取并集；重命名同时检查新旧路径。范围使用事件中的 base/head SHA 比较整个 PR，
不只比较最后一次提交。API 返回达到 300 文件上限、空结果、无效结果或失败时，运行全套。
判定 job 失败或输出缺失时，下游同样全量执行，避免漏检。

所有 job（含发布）跑在 GitHub 托管 runner `ubuntu-latest`：仓库公开后不得再挂自托管 runner（fork PR 可在其上执行任意代码）。uv 由 `setup-uv` 按 `uv.lock` 缓存，pnpm 由 `setup-node` 的 `cache: pnpm` 按各自锁文件缓存；每个 job 设 `timeout-minutes` 防挂死。自动取消旧任务沿用现有配置。工作流不使用顶层 `paths` 过滤，避免必需检查一直 Pending。
三个检查名称不变；main push 运行「发布就绪（按范围校验）」汇总门禁，核验受影响项成功、无关项按规则跳过。

本地验证策略与调度条件：`node --test .github/ci-scope.test.cjs .github/ci-evidence.test.cjs .github/deploy-v2.test.cjs`。
此优化不会补回本月已使用的额度，也不会解除账户现有的计费限制。

## main 按范围检查

正式 PR 比较固定 base/head SHA；main push 从本仓同分支最近通过「发布就绪」门禁且为目标祖先的 CI 提交累计比较，覆盖此前失败/取消/尚未完成的改动。草稿 PR 不跑。新 main 首次 push 缺少通过发布就绪门禁的同分支基线，执行全量检查。公共配置、未知范围、比较截断、找不到基线或 API 错误时跑全套。

发布仅接受目标 SHA 最新的 main push CI；「发布就绪（按范围校验）」逐项核对受影响项 success、无关项 skipped。缺失、失败、取消、范围不明不能伪装成通过。发布仍由管理员手动触发。
