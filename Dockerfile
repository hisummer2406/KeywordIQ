# syntax=docker/dockerfile:1
FROM python:3.11.15-slim

# uv 官方镜像里的静态二进制，比 `pip install uv` 更快也不占运行时依赖。
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

# 分两步 sync：先只拷贝依赖清单装依赖，源码变更不会使这一层失效，重复构建只重装
# 真正变化的那一小部分（--no-install-project 先跳过安装项目本身）。
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"
# logs/ 与 uploads/ 由 docker-compose 挂载宿主机目录，容器内先建好避免应用启动时
# 因目录不存在而报错（app 逻辑本身也会 makedirs，这里是为了让挂载点存在）。
RUN mkdir -p logs uploads/daily uploads/weekly

EXPOSE 8000

# 与 start.sh 保持同一份启动参数——单一事实来源；start.sh 保留给非容器环境使用。
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "4", \
     "--timeout-keep-alive", "600", \
     "--limit-concurrency", "1000", \
     "--loop", "uvloop", \
     "--log-level", "info", \
     "--no-access-log"]
