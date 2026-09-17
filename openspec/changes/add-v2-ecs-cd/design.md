## Context

V1 在同一台 ECS 以裸进程运行并占用 8000；V2 的 Docker Compose 应用监听 8001，静态文件
发布到 `/var/www/keywordiq-web`，Nginx 对外提供 `key.liangtong.online`。V2 发布不得修改
V1 的端口、域名、静态文件或 logrotate 配置。

## Goals / Non-Goals

**Goals:**

- 只发布 V2 的精确、已通过 CI 的 SHA。
- 保留 `deploy.sh` 作为唯一实际发布路径。
- 通过手动 `workflow_dispatch` 保留发布确认点。

**Non-Goals:**

- 不自动部署每次 push。
- 不自动回滚或执行 Alembic downgrade。
- 不改变 V1 的部署方式。

## Decisions

- 工作流仅接受 `refs/heads/v2`，并校验三个 CI check-run 均完成且成功。
- ECS 先验证 SHA 属于 `origin/v2`，检查本地已跟踪修改为空，再快进到该 SHA；不能精确到达
  目标 SHA 时失败，不使用 `reset --hard`。
- ECS 上实际构建、迁移、前端发布和健康检查都复用 `deploy.sh --yes`，避免平行发布逻辑。
- SSH 使用专用 Ed25519 密钥和固定 known-hosts，禁止密码和交互式主机密钥确认。

## Risks / Trade-offs

- ECS 构建仍依赖其可访问的镜像与依赖源；Docker Hub 不可达时需要保留已导入的基础镜像。
- Alembic upgrade 成功后工作流不会自动降级，失败应按兼容性人工回滚。
