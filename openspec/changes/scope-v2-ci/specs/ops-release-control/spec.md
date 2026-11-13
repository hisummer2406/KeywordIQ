## MODIFIED Requirements

### Requirement: 发布仅接受确切 SHA 且 CI 全部通过
发布 SHALL 只接受允许分支历史上的完整 commit SHA，且该 SHA 的受影响检查全部成功。未受影响检查仅在 GitHub Actions 同一 check suite 的范围汇总门禁成功且 SHA 匹配时允许跳过；无门禁的旧 SHA 仍要求三项全成功。等待、失败、取消、缺失或无法核实 MUST 阻止发布。

#### Scenario: CI 仍在运行
- **WHEN** 目标 SHA 的某个必需 CI job 状态为进行中
- **THEN** 系统拒绝生成发布确认卡片，提示等待 CI 完成

#### Scenario: CI 失败
- **WHEN** 目标 SHA 的某个必需 CI job 结论为失败
- **THEN** 系统拒绝发布

#### Scenario: 无法核实 CI 状态
- **WHEN** 查询 CI 状态的请求本身失败或找不到某个必需 job 的记录
- **THEN** 系统按"不放行"处理，不将无法核实等同于通过

#### Scenario: SHA 不在允许分支历史上
- **WHEN** 目标 SHA 无法在允许分支的提交历史中找到
- **THEN** 系统拒绝发布

#### Scenario: 范围门禁核验无关项
- **WHEN** SHA 匹配的同一运行范围门禁成功且仅无关项跳过
- **THEN** 机器人允许生成发布确认卡，展示无关项已经范围核验

