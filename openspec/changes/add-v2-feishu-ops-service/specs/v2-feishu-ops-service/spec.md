# v2-feishu-ops-service Specification

## Purpose

让 V2 ECS 的飞书运维机器人经 HTTPS 接收事件并安全执行受限运维操作。

## ADDED Requirements

### Requirement: V2 站点提供独立飞书回调入口

V2 Nginx 站点 SHALL 将 `/ops/` 转发到 127.0.0.1:8101 的 ops-bot，并且 MUST NOT 改动 V1 域名或 8000 端口。

#### Scenario: 飞书验证回调地址

- **WHEN** 飞书向 `https://key.liangtong.online/ops/feishu/webhook` 发送 URL 验证请求
- **THEN** 请求 MUST 到达 ops-bot 服务

### Requirement: ops-bot 使用 V2 部署身份与路径

systemd 服务 SHALL 在 `/var/www/keywordiq` 运行，并以已有的 `www` 用户启动。

#### Scenario: 机器人执行受限发布操作

- **WHEN** 已授权的飞书用户确认发布
- **THEN** ops-bot MUST 使用 V2 工作目录调用现有部署脚本
