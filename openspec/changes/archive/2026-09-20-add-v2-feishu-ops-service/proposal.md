## Why

飞书运维机器人模板仍使用旧的 `/opt/keywordiq` 主机路径，且 V2 Nginx 站点没有将 `/ops/` 路由到机器人端口，飞书无法验证回调地址。

## What Changes

- V2 Nginx 站点将 `/ops/` 转发到仅本机监听的 ops-bot 端口 8101。
- systemd 服务使用已部署的 V2 工作目录和 `www` 运行账号。

## Capabilities

### New Capabilities
- `v2-feishu-ops-service`: V2 ECS 可承载飞书运维机器人事件回调。

## Impact

- 影响 V2 Nginx 模板、ops-bot systemd 单元和部署手册。
