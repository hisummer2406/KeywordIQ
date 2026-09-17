# startup-version-log Specification

## Purpose

使运行中的应用版本可从启动日志直接确认。

## ADDED Requirements

### Requirement: 启动完成日志包含应用版本

应用每个 worker 在完成启动并开始接收请求时 SHALL 在启动完成日志中记录当前
`settings.VERSION`。

#### Scenario: 容器启动完成

- **WHEN** 应用 worker 完成初始化
- **THEN** 日志包含启动完成消息及当前应用版本
