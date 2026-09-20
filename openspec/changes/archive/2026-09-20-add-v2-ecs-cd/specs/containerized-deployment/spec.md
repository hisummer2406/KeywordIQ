## ADDED Requirements

### Requirement: V2 部署脚本默认目标

V2 分支的 `deploy.sh` SHALL 默认发布独立 V2 环境：前端产物目录为
`/var/www/keywordiq-web`，健康检查地址为 `http://127.0.0.1:8001/health`，遗留 systemd
服务名为 `keywordiq-v2`，日志轮转配置为 `/etc/logrotate.d/keywordiq-v2`。这些默认值
MUST NOT 覆盖或停止同机 V1 环境。

#### Scenario: 未传覆盖变量时部署 V2

- **WHEN** 在 V2 ECS 仓库根目录执行 `./deploy.sh --yes`
- **THEN** 脚本将 V2 SPA 发布到 `/var/www/keywordiq-web`，并通过 8001 执行健康检查，不会
  停止名为 `keywordiq` 的 V1 遗留服务
