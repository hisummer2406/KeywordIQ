# v2-ecs-cd Specification

## Purpose

提供 V2 独立 ECS 环境的受控手动持续部署。

## ADDED Requirements

### Requirement: V2 手动部署只接受已验证的精确 SHA

部署工作流 SHALL 只能由 `v2` 分支手动触发，并且 MUST 在连接 ECS 前确认目标 SHA 的后端、
前端和扩展 CI 检查均已完成且成功。

#### Scenario: CI 检查未通过

- **WHEN** 任一必需 CI 检查缺失、进行中、跳过或失败
- **THEN** 工作流 MUST 在连接 ECS 前失败，且不得执行部署命令

#### Scenario: 从非 V2 分支触发

- **WHEN** 在 GitHub Actions 页面选择非 `v2` 分支运行工作流
- **THEN** 工作流 MUST 不执行部署 job

### Requirement: V2 部署与 V1 环境隔离

V2 部署 SHALL 使用 `127.0.0.1:8001` 健康检查、`/var/www/keywordiq-web` 静态目录、
`/etc/logrotate.d/keywordiq-v2` 日志轮转配置，并且 MUST NOT 操作 V1 的 8000 端口或
`amazon.liangtong.online` 的文件。

#### Scenario: V2 部署完成

- **WHEN** 目标 SHA 在 ECS 发布完成
- **THEN** `https://key.liangtong.online/health` MUST 返回成功，且 V1 的 8000 监听不被停止

### Requirement: ECS 只发布目标 SHA 且保留本地修改保护

ECS 发布前 SHALL 确认目标 SHA 位于 `origin/v2` 历史中；若工作树存在已跟踪本地修改，
MUST 拒绝覆盖。发布过程 MUST 调用现有 `deploy.sh --yes`，不得另建平行发布路径。

#### Scenario: ECS 有已跟踪本地修改

- **WHEN** ECS 仓库有已跟踪但未提交的文件修改
- **THEN** 工作流 MUST 失败且不得切换提交或执行迁移
