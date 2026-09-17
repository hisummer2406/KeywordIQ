## Context

V2 通过 GitHub Actions 部署后，需要一个可从容器日志读取的版本信号，辅助确认已切换至
预期的应用版本。

## Goals / Non-Goals

**Goals:**

- 在每个 worker 启动完成时记录配置中的应用版本。

**Non-Goals:**

- 不改变版本配置、接口响应或健康检查格式。

## Decisions

- 复用现有 `settings.VERSION`，在既有启动完成日志中用参数化日志输出。

## Risks / Trade-offs

- 多 worker 模式会产生多条相同版本日志，这是现有每 worker 启动日志语义的一部分。
