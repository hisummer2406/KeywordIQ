## Context

V2 已在 `/var/www/keywordiq` 运行，业务 API 仅监听 8001。飞书 ops-bot 是执行 Docker 与受限 sudo 操作的 host 级进程，需独立监听 127.0.0.1:8101，再由 V2 Nginx 提供 HTTPS 回调。

## Decisions

- 保持 ops-bot 进程不容器化，避免其执行宿主机 Docker 与发布脚本时出现路径和权限错配。
- 使用已有 `www` 账号：它已在 docker 组，并已有只限 V2 发布所需命令的 sudoers 规则。
- systemd 通过 `uv run --no-sync` 使用仓库 `.venv`，部署前显式执行 `uv sync --no-dev`。
- `/ops/` 只添加到 `key.liangtong.online` 的 V2 站点，绝不修改 V1 域名或 8000 服务。
