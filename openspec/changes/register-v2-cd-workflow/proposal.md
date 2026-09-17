## Why

V2 的手动部署工作流位于 `v2` 分支，但 GitHub 只在 workflow 文件存在于默认分支时显示
`workflow_dispatch` 入口。没有默认分支入口，V2 工作流无法从 Actions 页面首次运行。

## What Changes

- 在 `main` 注册同路径的 V2 手动部署工作流入口。
- 默认分支版本只提示选择 `v2`，不读取 Secrets、不连接 ECS。

## Capabilities

### New Capabilities
- `v2-cd-workflow-registration`: 默认分支提供 V2 手动部署工作流的可发现入口。

### Modified Capabilities

- 无。

## Impact

- 仅影响 `main` 的 `.github/workflows/deploy-v2.yml` 和此 OpenSpec 变更。
