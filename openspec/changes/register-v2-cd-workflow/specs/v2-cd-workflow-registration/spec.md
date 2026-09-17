# v2-cd-workflow-registration Specification

## Purpose

让 V2 的手动部署工作流可从 GitHub Actions 页面发现和运行。

## ADDED Requirements

### Requirement: 默认分支注册 V2 手动工作流入口

默认分支 SHALL 包含与 V2 工作流同路径的 `workflow_dispatch` 文件，以便 Actions 页面显示
V2 工作流的 Run workflow 入口。默认分支版本 MUST NOT 连接 ECS 或读取部署凭证。

#### Scenario: 在默认分支误运行

- **WHEN** 用户没有切换 Branch 而从 `main` 运行 V2 工作流
- **THEN** 工作流 MUST 在连接任何服务器前失败，并提示选择 `v2`

#### Scenario: 选择 V2 分支运行

- **WHEN** 用户在 Run workflow 的 Branch 下拉框选择 `v2`
- **THEN** GitHub SHALL 使用 `v2` 中同路径的完整 V2 工作流定义执行
