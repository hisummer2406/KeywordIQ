## MODIFIED Requirements

### Requirement: V2 手动部署只接受已验证的精确 SHA

部署工作流 SHALL 只能由 `v2` 分支手动触发，并且 MUST 在连接 ECS 前确认目标 SHA 的后端、
前端和扩展检查的发布证据均已验证：受影响项 MUST 成功；未受影响项仅在同一 v2 push CI 运行的范围汇总门禁成功时允许跳过。部署 MUST 核验目标 SHA 最新的 ci.yml push 运行，不能混用 PR 或其他运行。

#### Scenario: CI 检查未通过

- **WHEN** 任一受影响检查缺失、进行中、跳过或失败，或范围汇总门禁未通过
- **THEN** 工作流 MUST 在连接 ECS 前失败，且不得执行部署命令

#### Scenario: 从非 V2 分支触发

- **WHEN** 在 GitHub Actions 页面选择非 `v2` 分支运行工作流
- **THEN** 工作流 MUST 不执行部署 job

#### Scenario: 无关检查经核验后跳过

- **WHEN** v2 push 的范围判定成功，受影响检查全部成功，无关检查按规则跳过且汇总门禁成功
- **THEN** 手动部署允许使用该 SHA 的 CI 结果，不要求重跑无关项

#### Scenario: 累计未验证改动

- **WHEN** 之前的 push 检查失败或被取消，而最新 push 改动另一个目录
- **THEN** CI 从最近已验证的祖先累计比较，之前的未验证改动仍纳入检查；缺少可信基线时全量检查
