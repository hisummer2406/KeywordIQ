# v2-ecs-cd Specification

## Purpose
TBD - created by archiving change add-v2-ecs-cd. Update Purpose after archive.
## Requirements
### Requirement: V2 手动部署只接受已验证的精确 SHA

`Deploy Main to ECS` SHALL 仅接受 `main` 的手动触发。连接 ECS 前 MUST 核对精确目标
SHA 在该分支上最新的 `ci.yml` push 运行。该运行及其「发布就绪（按范围校验）」必须成功，
受影响检查 MUST 成功，未受影响检查仅经同一运行的门禁核验后允许跳过。

#### Scenario: CI 检查未通过

- **WHEN** 最新 main push CI、必需检查或发布就绪门禁缺失、进行中、失败或取消，或受影响检查被跳过
- **THEN** 工作流 MUST 在连接 ECS 前失败，且不得执行部署命令

#### Scenario: 从非 V2 分支触发

- **WHEN** 操作者选择非 `main` 分支运行工作流
- **THEN** 工作流 MUST 不执行部署 job

#### Scenario: 无关检查经核验后跳过

- **WHEN** 最新 main push 的范围判定成功，受影响检查均通过，未受影响检查按范围跳过且发布就绪门禁成功
- **THEN** 手动部署 SHALL 接受此运行的 CI 证据

#### Scenario: 累计未验证改动

- **WHEN** 先前的 push CI 失败或被取消
- **THEN** CI SHALL 从同一分支最近通过发布就绪门禁的祖先累计比较，缺少可信基线或范围不明时运行全部检查

#### Scenario: 切换后的首次发布

- **WHEN** `v2` 改名为 `main`，旧分支已有绿色 CI，或仅有 PR CI 通过
- **THEN** 发布 SHALL 等待新的 main push CI 完成，首次运行找不到同分支合格祖先时 MUST 执行完整检查

### Requirement: V2 部署与 V1 环境隔离

V2 部署 SHALL 使用 `127.0.0.1:8001` 健康检查、`/var/www/keywordiq-web` 静态目录、
`/etc/logrotate.d/keywordiq-v2` 日志轮转配置，并且 MUST NOT 操作 V1 的 8000 端口或
`amazon.liangtong.online` 的文件。

#### Scenario: V2 部署完成

- **WHEN** 目标 SHA 在 ECS 发布完成
- **THEN** `https://key.liangtong.online/health` MUST 返回成功，且 V1 的 8000 监听不被停止

### Requirement: ECS 只发布目标 SHA 且保留本地修改保护

ECS 发布 SHALL 确认完整目标 SHA 位于 `origin/main` 历史，且当前检出能快进到目标。
切换前 MUST 核实实际运行版本也是目标的祖先。发布 SHALL 精确检出该 SHA，并沿用
`deploy.sh --yes`。

#### Scenario: ECS 有已跟踪本地修改

- **WHEN** ECS 仓库有已跟踪但未提交的文件修改
- **THEN** 工作流 MUST 失败且不得切换提交或执行迁移

#### Scenario: 首次在仅跟踪 v2 的 ECS 仓库发布

- **WHEN** ECS 当前检出 V2 的 `v2`，远端 fetch 配置仅跟踪该旧分支
- **THEN** 发布 SHALL 显式 fetch `origin/main`，保留原 `v2` 引用并快进到已核验 SHA

#### Scenario: ECS 已有不同的本地 main

- **WHEN** 首发前发现 ECS 在 `v2` 上，已有本地 `main` 与当前 V2 HEAD 不同
- **THEN** 工作流 MUST 拒绝检出和部署，并保留现有引用供人工核实

