## Why

V2 已独立部署在 ECS，但发布仍依赖登录服务器逐条执行命令。现有 `deploy.sh` 的默认值仍
指向历史 V1 端口和目录，不能作为 V2 自动化发布入口。

## What Changes

- 将 `deploy.sh` 默认目标改为 V2 的 8001、独立前端目录与独立 logrotate 配置。
- 提供与 V2 默认值一致的独立 Nginx 站点模板。
- 新增只可手动触发的 GitHub Actions 工作流，核验目标 SHA 的全部 CI 后，经 SSH 在 ECS
  上调用 `deploy.sh --yes`。
- 记录 ECS 与 GitHub Secrets 的一次性引导步骤。

## Capabilities

### New Capabilities
- `v2-ecs-cd`: V2 可按已验证的精确 SHA 手动持续部署到独立 ECS 环境。

### Modified Capabilities
- `containerized-deployment`: 部署脚本的默认目标切换为 V2 独立运行环境。

## Impact

- 影响 `.github/workflows/`、`deploy.sh` 和 V2 运维文档。
- 需要一次性配置部署 SSH 密钥、Docker 组权限和受限 sudo 规则。
