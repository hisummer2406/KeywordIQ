## Context

GitHub Actions 的 `workflow_dispatch` 需要 workflow 文件位于默认分支，才会在网页 Actions
界面显示运行入口。完整的 V2 部署实现属于 `v2`，不能将其复制到 `main` 并冒险使用错误的
代码和部署边界。

## Goals / Non-Goals

**Goals:**

- 让 Actions 页面显示 V2 部署入口。
- 确保在 `main` 误运行时没有服务器副作用。

**Non-Goals:**

- 不部署 `main` 或 V1。
- 不读取 SSH 或生产环境 Secrets。

## Decisions

- 在 `main` 添加与 V2 完整工作流同路径、同名称的最小入口。
- 默认分支 job 固定失败并提示用户在 Branch 下拉框选择 `v2`；选择 `v2` 后 GitHub 执行该
  分支中同一路径的完整 V2 工作流。

## Risks / Trade-offs

- 入口文件和 V2 工作流必须保持相同路径；重命名时两处都需要更新。
