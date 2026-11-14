# CI 用量控制

开发中的 V2 PR 使用 Draft；准备审核时点击 **Ready for review**，自动启动云端检查。
再次转为 Draft 会取消该 PR 尚在运行的旧检查。正式 PR 后续推送仍自动检查，新的提交会取消旧运行。

| 事件 / 改动 | 执行范围 |
|---|---|
| `push` 到 `v2` | 按最近已验证祖先至目标 SHA 的累计改动检查，供发布门禁核验 |
| V2 草稿 PR | 不分配 runner |
| V2 正式 PR：`frontend/` | 前端 |
| V2 正式 PR：`extension/` | 插件 |
| V2 正式 PR：后端代码、迁移、测试、Python 依赖 | 后端 |
| V2 正式 PR：`docs/`、`openspec/`、根说明文档 | 只运行范围判定，三项检查按 job 条件跳过 |
| V2 正式 PR：CI、公共脚本、其他配置或未知路径 | 三项完整检查 |
| 原有 `main` push / PR | 保持三项完整检查 |

混合改动取并集；重命名同时检查新旧路径。范围使用事件中的 base/head SHA 比较整个 PR，
不只比较最后一次提交。API 返回达到 300 文件上限、空结果、无效结果或失败时，运行全套。
判定 job 失败或输出缺失时，下游同样全量执行，避免漏检。

所有 job 跑在自托管 runner（标签 `mbp-ci`），不用 GitHub Actions 缓存：pnpm store 和 uv 缓存（runner `.env` 的 `UV_CACHE_DIR`）在本机持久，每个 job 设 `timeout-minutes` 防挂死。自动取消旧任务沿用现有配置。工作流不使用顶层 `paths` 过滤，避免必需检查一直 Pending。
三个检查名称不变；v2 push 增加「发布就绪（按范围校验）」汇总门禁，核验受影响项成功、无关项按规则跳过。

本地验证策略与调度条件：`node --test .github/ci-scope.test.cjs .github/ci-evidence.test.cjs`。
此优化不会补回本月已使用的额度，也不会解除账户现有的计费限制。

## v2 按范围检查（2026-11-13）

正式 PR 比较固定 base/head SHA；v2 push 从本仓最近已验证成功且为目标祖先的 CI 提交累计比较，覆盖此前失败/取消/尚未完成的改动。草稿 PR 不跑；main 行为不变。公共配置、未知范围、比较截断、找不到基线或 API 错误时跑全套。

发布仅接受目标 SHA 最新的 v2 push CI；「发布就绪（按范围校验）」逐项核对受影响项 success、无关项 skipped。缺失、失败、取消、范围不明不能伪装成通过。旧 SHA 仅在同一 CI 运行三项全部成功时兼容。发布仍为手动操作，不触发 ECS。
