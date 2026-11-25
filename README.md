# 🚀 KeywordIQ

> 专业的亚马逊搜索词数据分析平台，支持 GB 级文件处理、实时数据分析和可视化展示
>
> **使用场景**：单用户数据导入（日报 ~30 万行 / 周报 ~300 万行）+ 约 200 用户高并发只读查询

> [!IMPORTANT]
> **禁止商用 / Noncommercial use only.** 本项目按 [PolyForm Noncommercial 1.0.0](LICENSE.md) 授权：仅允许非商业目的的学习、研究与评估；未经版权人书面授权，不得将本项目全部或任何部分用于任何商业用途。仓库公开可见不代表授予额外权利。

---

## ✨ 功能特性

- **📊 数据分析**：站点 + 视图（日/周）+ 锚定期 + 行窗口的多维度筛选，走势与历史排名，在榜/掉榜/全部三态切换
- **📁 大文件导入**：OSS 直传（浏览器直接上传，字节不经应用服务器）+ 服务端流式 `COPY` 装载，GB 级文件内存有界
- **🔍 智能检索**：单一检索框按格式自动判定关键词 / ASIN，类目多选、品牌/排名/份额等多维过滤
- **👥 权限管理**：自建 JWT + RBAC，`analysis:view` / `analysis:export` / `upload:import` 三个权限点
- **⚡ 高性能**：星型模型 + 月度分区，日期选择器与分页总数走预聚合统计表，不在事实表上实时聚合

---

## 🏗️ 技术架构

```
前端 (Ant Design Pro / Umi Max SPA) → 应用层 (FastAPI) → 数据层 (PostgreSQL, schema: analysis)
                                          ↓
                                ┌─────────┴─────────┐
                                ├─ 认证模块 (自建 JWT + RBAC)
                                ├─ 数据分析接口 (site-scoped-query)
                                ├─ 导入管线 (OSS 直传 → 流式 COPY → 三段式装载)
                                └─ 用户权限管理
```

数据模型是星型模型：`dim_keyword` / `dim_product` 两张维度表 + `fact_daily` / `fact_weekly` /
`fact_dropped` 三张按月分区的事实表，事实表只追加、不原地更新。详见 [CLAUDE.md](./CLAUDE.md)
「V2 数据模型」一节。

**核心技术**：FastAPI（sync + async 双引擎）+ PostgreSQL 15（分区表）+ Ant Design Pro

---

## 📚 业务知识库

[`knowledge/`](./knowledge/README.md) 放稳定可复用的业务知识（指标定义、分析方法、数据源
含义），与 `openspec/`（产品行为与接口契约的权威来源）、`CLAUDE.md`（工程约定与实现决策）
职责分开，边界与写作约定见索引。

- [亚马逊跨境电商核心指标指南](./knowledge/amazon-cross-border-ecommerce-metrics.md)：市场、竞争、流量、转化、广告、销售、利润、库存八类指标，以及选品重点关注的六个指标。每项均标注在 KeywordIQ 的可得性——八类里六类是外部输入，产品没有对应数据源。

---

## 🚀 快速开始

### 本地开发

```bash
# 1. 后端依赖（uv 按 .python-version 与 uv.lock 管理 .venv）
uv sync
cp .env.example .env        # 按注释填写数据库、ADMIN_SECRET_KEY 等；OSS_ENABLED=false 时走本地文件退路

# 2. 数据库迁移
uv run alembic upgrade head

# 3. 启动后端
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 4. 启动前端（另开一个终端）
cd frontend
pnpm install                # 首次安装，postinstall 会自动跑 max setup
pnpm dev                    # 默认 http://localhost:8000，/api 反代到后端 uvicorn
```

前端本地开发细节见 [frontend/README.md](./frontend/README.md)。

### 生产部署

部署形态是 **Docker Compose（应用 + 自建 PostgreSQL + Netdata）+ host nginx**：
nginx 托管前端 SPA 产物并反代 `/api` 到应用容器（`127.0.0.1:8000`），数据库与监控都是
容器、不对公网暴露端口。详见 `openspec/changes/dockerize-selfhosted-infra/design.md`。

```bash
# 首次部署
git clone <project-repo> && cd KeywordIQ
cp .env.example .env        # 填写生产配置：POSTGRES_*/PG_*、ADMIN_SECRET_KEY、OSS_*、ENABLED_SITES 等
mkdir -p logs uploads       # 必须在第一次 docker compose run/up 之前建好：docker-compose.yml
                             # 的 `./logs:/app/logs` 是 bind mount，源目录不存在时由 Docker
                             # 自动创建，属主是运行 dockerd 的 root——之后 crontab 里非特权用户
                             # 追加写 logs/backup.log 会因权限不足直接失败（Codex 审核 PR #84
                             # 第二十二轮）。这里用当前用户先建好目录，之后 docker compose
                             # 只会挂载已存在的目录、不会改属主；容器内进程是 root（Dockerfile
                             # 未声明 USER），写入不受宿主机目录属主影响，不受此步骤影响。
docker compose build app
docker compose up -d db
docker compose run --rm app alembic upgrade head   # db 不对宿主机暴露端口，迁移必须在容器内跑
cd frontend && pnpm install && pnpm build && cd ..
rsync -a --delete frontend/dist/ /var/www/keywordiq/

sudo cp etc/keywordiq-nginx.conf /etc/nginx/sites-available/keywordiq
sudo ln -sf /etc/nginx/sites-available/keywordiq /etc/nginx/sites-enabled/keywordiq
sudo nginx -t && sudo systemctl reload nginx

docker compose run --rm app python scripts/seed_admin.py   # 初始管理员 admin / pwd123，首次登录后请修改
docker compose up -d   # 拉起应用容器（restart: unless-stopped，崩溃/宿主机重启由 Docker 自动拉起，不需要额外的 systemd unit）
```

后续发布用 `./deploy.sh`（构建镜像 → 容器内迁移 → 构建前端 → 发布产物 → reload nginx →
切换容器到新镜像）：

```bash
./deploy.sh                  # 全量发布
./deploy.sh --skip-migrate   # 只发前端，不动数据库
./deploy.sh --yes            # 不交互确认迁移（无人值守）
```

> **备份**：自建 PostgreSQL 没有阿里云 RDS 那样的自动备份，用
> `scripts/backup_selfhosted_pg.py` 定时执行，产物存到 OSS（脚本文档里同时给了验证过的
> 恢复命令）。**这一步同样需要手动安装**（与上面的 nginx 反代配置一样，不随 `./deploy.sh`
> 自动发生）——`crontab -e` 加一行，例如每天 03:00 执行一次全量备份：
>
> ```bash
> 0 3 * * * cd /path/to/KeywordIQ && /home/<user>/.local/bin/uv run python scripts/backup_selfhosted_pg.py >> logs/backup.log 2>&1
> ```
>
> **`uv` 必须写字面绝对路径，不能写 `$(command -v uv)`。** 后者是 cron **在触发这条
> job 的那一刻**才求值的 shell 命令替换，而 cron 执行 job 时用的是它自己精简到几乎
> 找不到任何用户程序的默认 `PATH`（这正是这里要绕开的问题）——`command -v uv` 在那个
> `PATH` 下什么也找不到，展开成空字符串，实际执行的命令变成 `run python ...`，每次
> 定时备份都会失败。写安装步骤前先在交互 shell 里跑一次 `command -v uv`，把它打印出的
> 绝对路径（如 `/home/ubuntu/.local/bin/uv`）抄进这一行；`cd` 到项目目录是因为
> `config.py` 的 `env_file=".env"` 按当前工作目录解析。
> 保留策略见脚本文档，用 OSS 生命周期规则管理即可，不需要在这条 cron 里额外处理。
>
> **已知边界**：进程在导入中途被杀（崩溃/OOM/发布重启）时，该批次记录会停留在
> `PROCESSING`（回收逻辑只覆盖 `UPLOADING`/`QUEUED`）。数据无损——事实写入在单事务里
> 回滚、OSS 对象仍在，重新上传同一期即可恢复；受影响的只是历史屏里那一行状态。

---

## 🖥️ 运行环境

| 类别 | 规格 |
|------|------|
| 应用服务器 | 阿里云 ECS · Alibaba Cloud Linux 3.2104 LTS 64 位 · 4 vCPU / 8 GiB |
| 数据库 | PostgreSQL 15.8 · Docker Compose 容器，与应用同机（独立库 `keywordiq_v2`） · 连接数以 `.env` / Compose 为准 |

三个月保留窗口下满窗约 2 亿行 / 50 GB，当前规格通常无需扩容；扩容触发条件与连接池取值算式
见 [CLAUDE.md](./CLAUDE.md)「Production Environment」一节。

---

## 📦 关键配置（.env）

完整项与单位说明见 [.env.example](./.env.example)，新增配置务必同步过去。核心几项：

```bash
DATABASE_URL=postgresql://user:pass@host/db
DATABASE_URL_ASYNC=postgresql+asyncpg://user:pass@host/db
DATABASE_SCHEMA=analysis

ENABLED_SITES=US,GB,DE      # 必须是 app/site_registry.py 已登记的站点码
DEFAULT_SITE=US
HOT_WINDOW_MONTHS=3         # 数据保留窗口（月）

MAX_FILE_SIZE=3221225472    # 3GB
OSS_ENABLED=false           # true 时走前端 OSS 直传；false 时走本地文件上传（仅供本地/CI）
```

---

## 💻 开发指南

### 项目结构

```
KeywordIQ/
├── app/
│   ├── auth/               # 自建 JWT + RBAC
│   ├── site_registry.py    # 站点码唯一事实来源
│   ├── table/
│   │   ├── analysis/       # V2 查询层（model/crud/service/api + retention/partitioning）
│   │   ├── search/         # 查询请求/响应 schemas
│   │   └── upload/         # OSS 直传 + 三段式导入 + 批次历史
│   └── user/                # 用户与角色管理
├── alembic/                 # 数据库迁移（分区表/物化视图需手写）
├── frontend/                 # Ant Design Pro SPA（Umi Max），独立构建，见下方目录说明
├── scripts/                  # seed_admin / prune_retention / check_oss 等运维脚本
├── test/                     # pytest（单元 + 需真实库的集成测试）
├── main.py                   # FastAPI 入口
├── config.py                 # Pydantic Settings
└── database.py               # SQLAlchemy 2.0 sync + async 双引擎
```

**`frontend/`**（独立 pnpm 项目，构建产物由 nginx 托管，不随后端启动）：

```
frontend/src/
├── app.tsx           # 运行时配置：getInitialState / layout / request 拦截器（token 注入、401 跳转）
├── access.ts          # 权限工厂，由 initialState.currentUser 派生，控制路由/菜单显隐
├── typings.d.ts        # 全局 API 类型（API.CurrentUser、API.TableResponse 等）
├── services/            # 后端接口封装，按域拆分
├── components/           # 跨页复用组件（ImportStatus、RightContent 等）
└── pages/                 # 屏：Login / Analysis / Upload / ImportHistory / User
```

前端本地开发、环境变量与常用脚本见 [frontend/README.md](./frontend/README.md)。

### 开发约束（节选）

- **SQLAlchemy 2.0 / Pydantic 2.0 Only**：`DeclarativeBase` + `Mapped`/`mapped_column` + `select()`；
  `ConfigDict` + `field_validator` + `model_validate()`。禁止 1.x / v1 风格 API。
- 数据库结构变更一律走 Alembic migration；分区表与物化视图不在 autogenerate 范围内，需手写并用
  `alembic upgrade head --sql` 核对。
- Service 层控制事务边界，CRUD 层不提交；大文件导入用流式 `COPY`，不整文件读入内存。
- FastAPI 请求处理保持轻量，阻塞数据库操作、CPU 密集任务（如 bcrypt）不直接跑在事件循环上。

完整的架构说明、层次约定、导入/查询链路细节、错误契约与并行开发纪律见
**[CLAUDE.md](./CLAUDE.md)**（本仓库的权威开发文档）。

### 常用命令

```bash
# 测试
uv run pytest test/

# 依赖管理
uv add <package>              # 运行时依赖
uv add --dev <package>        # 仅测试/工具

# 数据库迁移
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "说明"
uv run alembic current

# 监控导入日志
tail -f logs/app.log | grep -i import

# 磁盘占用排查
du -ah /path/to/search | sort -rh | head -n 20
```

### API 文档

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

<div align="center">

**© 2026 KeywordIQ**

技术支持: stone_summer24

</div>
