## 1. Docker 编排脚手架

- [x] 1.1 编写应用 `Dockerfile`（基于 `uv` 安装依赖、构建产物，复用 `.python-version`/`uv.lock`）
- [x] 1.2 编写 `docker-compose.yml`：`app` 服务（构建自 1.1 的 Dockerfile，`ports: ["127.0.0.1:8000:8000"]`，`restart: unless-stopped`）与 `db` 服务（`postgres:15.8` 锁定小版本镜像，bind mount 数据目录，`restart: unless-stopped`，不对外暴露端口）
- [x] 1.3 本地起 `docker compose up`，确认 `app` 能通过服务名 `db` 连上 `db:5432`（先用空库验证网络连通，不涉及真实数据）——`/health` 200，日志里能看到 `SELECT 1` 走通两个引擎。
  **过程中发现并修复一个与 Docker 无关的预先存在的真实 bug**：`httpx` 被错放在
  `pyproject.toml` 的 `dev` 分组，但 `app/auth/dingtalk_client.py`、
  `app/table/competitor/store_service.py`/`store_fetcher.py` 在运行时导入它——本地
  开发一直带 dev 分组装依赖，从未暴露；`uv sync --no-dev`（Dockerfile 的标准做法）
  第一次真实装出一个启动即 `ModuleNotFoundError` 的镜像。已移到主依赖，`uv lock`
  重新生成，全量测试仍 584 passed。

## 2. 自建 PostgreSQL 的 schema 建库

- [x] 2.1 在全新空的 `db` 容器上执行 `alembic upgrade head`（经 `docker compose exec app`），
  24 个迁移全部跑通，`fact_daily`/`fact_weekly`/`fact_dropped`/`fact_growth` 四张分区表
  确认建出（`pg_class.relkind='p'`）。**无法直接 diff 阿里云 RDS 的实际 schema**（本会话
  没有生产库访问权限）——已核对的是"迁移链路自洽、能从空库重建出完整 schema"，不是
  "与生产 RDS 逐字段比对"，后者需要有生产访问权限的人补跑一次
  `pg_dump --schema-only` 对比。
- [x] 2.2 迁移链路本身没有发现缺口。**过程中发现并修复了与本变更无关的 CLAUDE.md
  文档漂移**：`category_stats`/`brand_stats` 早在 2026-08-05（迁移 `f5a6b7c8d9e0`）就
  从物化视图改造成按日期分片的普通表，但 CLAUDE.md 三处仍写着"物化视图"、且引用了
  已不存在的函数名 `_refresh_category_stats`（实际是 `_rebuild_filter_stats`）——已更正。
  另外发现 `scripts/seed_demo_data.py`、`scripts/generate_synthetic_data.py` 两个开发
  工具脚本引用了已在同一次迁移中删除的 `dim_product.brand`/`.category` 列，运行即报
  `UndefinedColumn`——**未在本次范围内修复**（与容器化/自建 PG 无关，是另一笔独立的
  维护债，记在这里避免下次又要重新发现一遍）。

## 3. 配置与连接池重新核算

- [x] 3.1 `.env.example`：`DATABASE_URL`/`DATABASE_URL_ASYNC` 新增指向 `db` 的注释示例
  （保持与其余环境一样"注释、按需取用"的约定，不强改成唯一活跃值），连接池改为目标值
  （同步 `2+3`，异步 `8+8`），新增自建 PG 容器专用变量段
  （`POSTGRES_DB`/`POSTGRES_PASSWORD`/`PGDATA_HOST_PATH`/`PG_*`）
- [x] 3.2 `db` 容器的 `max_connections`/`shared_buffers`/`effective_cache_size`/
  `work_mem` 通过 compose 的 `command:` 从 `.env` 变量注入，起点值见 `.env.example`
- [x] 3.3 `uv run pytest -m conventions` 通过（15 passed）——曾在改连接池默认值时先红
  过一次（CLAUDE.md 公示值与 config.py 默认值不一致），随 3.4 一并改完后转绿
- [x] 3.4 CLAUDE.md 更新：「Performance Optimizations」连接池算式、「Key Configuration」
  示例值、「Production Environment」表格与容量含义，均已反映同机部署与新池值；
  一并修正了 2.2 发现的 `category_stats` 文档漂移

## 4. 数据迁移

- [x] 4.1 `scripts/migrate_to_selfhosted_pg.py`：确认源库无
  `PROCESSING`/`QUEUED`/`UPLOADING` 批次 → 容器内 `pg_dump --data-only`（排除迁移自带
  种子表，见下）→ 补齐目标分区 → `pg_restore --data-only`。**本地演练过程中发现并修复
  两个真实 bug，不是靠代码审查发现的**：
  (a) `pg_restore` 不显式给 `--username postgres` 会用 `docker compose exec` 的默认
  执行用户（root）连接，报 `role "root" does not exist"`；
  (b) `permission`/`role`/`role_permission`/`alembic_version` 四张表由 Alembic 迁移
  在建库时直接写入固定种子数据，`--data-only` 原样搬一遍会主键冲突——已在 `pg_dump`
  阶段用 `--exclude-table` 排除。
- [x] 4.2 `scripts/verify_selfhosted_migration.py`：比对 `fact_daily`/`fact_weekly`/
  `dim_keyword`/`dim_product`/`site_data_stats` 的行数与最新期次。**演练中发现并修复
  一个 bug**：源库（psycopg2）里空表的 `MAX()` 是 Python `None`，目标库（`psql -tAc`）
  里同样的空值渲染成空字符串，两者直接比较恒不相等——表现是"两边明明都是 0 行，核对却
  报不一致"。已统一按 `None` 归一化。
- [x] 4.3 本地演练（不是"跳过、直接标记完成"）：起一个独立的 `postgres:15.8` 容器
  模拟 RDS，跑 `alembic upgrade head` + 少量手写数据（`scripts/seed_demo_data.py` 与
  `scripts/generate_synthetic_data.py` 两个现成的生成器都因 2.2 提到的 schema 漂移
  跑不通，改用针对当前 schema 手写的最小数据集）；对真实的 `docker-compose.yml` `db`
  服务完整跑一遍 4.1 + 4.2，`verify_selfhosted_migration.py` 最终报告全部
  `[OK]`（`fact_daily`/`fact_weekly`/`dim_keyword`/`dim_product`/`site_data_stats`
  行数与最新期次一致）。新发现的一个真实拓扑限制：`_check_no_active_import` 的
  host 侧预检与容器内执行的 `pg_dump`/`pg_restore` 命令要求源库地址在两种网络视角下
  都可达——生产环境下 RDS 天然满足（同一 VPC 域名解析一致），本地演练环境不满足
  （host 与容器看到"同一台机器"的方式不同），已在脚本注释里写明这不是需要修的缺陷。
- [ ] 4.4 **未执行**——需要生产阿里云 RDS 的访问权限与实际低峰时段窗口，本会话不具备，
  留给有生产访问权限的人执行。跑法见 `migrate_to_selfhosted_pg.py` 与
  `verify_selfhosted_migration.py` 的用法说明。

## 5. 备份自动化

- [x] 5.1 `scripts/backup_selfhosted_pg.py`：容器内 `pg_dump`（全量，非 `--data-only`——
  备份要能在自建库整个消失时独立重建，不能依赖恢复时还记得先跑迁移）→
  `docker compose cp` 取出 → 上传到 OSS `backups/postgres/` 前缀。**已用真实的开发环境
  OSS 桶（`keywordiq-dev`）实际跑通**，不是只读代码；测试产物用完已从桶里删除。
- [x] 5.2 恢复步骤写进了 `backup_selfhosted_pg.py` 的模块文档字符串（验证过的具体命令，
  含一个真实踩过的坑：`pg_restore --create` 会尝试 DROP/CREATE 自己当前连接的数据库，
  报错但被当作可忽略继续跑完——最终数据没问题，但生产恢复时不该让人对着一堆报错怀疑
  恢复是否成功，故文档里明确写"不要加 --create"，用 `--clean --if-exists` 即可）。
- [x] 5.3 实际跑了一次"备份 → 恢复到全新空库 → 核对"演练：备份产出真实上传到 OSS
  又下载下来、恢复进一个从未跑过迁移的全新 `db` 容器、核对 `permission`（6 行）、
  `role`（3 行）、`alembic_version`（1 行）均与备份时刻一致。
- [ ] 5.4 **未配置**——往生产机器上装 cron 是生产环境操作，本会话不具备执行环境。
  待生产部署时按 `backup_selfhosted_pg.py` 的用法说明配置定时任务，保留策略建议直接
  用 OSS 生命周期规则管理该前缀（脚本文档里已说明理由，不在脚本里重新实现）。

## 6. 切换与回滚准备

- [ ] 6.1 **未执行**——切换生产 `.env` 是生产环境操作，本会话不具备执行环境
- [ ] 6.2 **未执行**——同上，依赖 6.1
- [ ] 6.3 **未执行**——需要真实的日报/周报导入周期与生产资源监控，本会话不具备
- [ ] 6.4 **未执行**——释放生产 RDS 实例是不可逆的基础设施操作，必须由有权限、且已
  完成前面几步验证的人在确认无误后执行，不应由实现变更的会话代为决定

## 7. 旧部署路径退役

- [x] 7.1 `etc/keywordiq.service` 最初只是主仓库工作树里一份未提交的文件，本 worktree
  开出时不含它；合并本分支进 `v2` 前发现 `v2` 上又有一次独立提交（`1cff6e2`）把它连同
  CLAUDE.md 的 systemd 说明真正提交了进去，与本变更的 merge 因此在 CLAUDE.md 冲突。
  按 design.md 决策 8（依赖 Docker 原生 `restart` 策略，不需要额外 systemd unit，经用户
  确认）：合并时移除了 `etc/keywordiq.service`、保留本分支的 CLAUDE.md 版本，并同步
  更新了 README.md 里引用该文件的安装步骤（另一处遗漏，随手一并改掉）。
- [x] 7.2 `deploy.sh` 重写：`uv sync`/裸 `alembic`/`systemctl restart` 替换为
  `docker compose build` → `docker compose run --rm app alembic upgrade head`
  （`db` 不对宿主机暴露端口，迁移必须在容器内跑）→ `docker compose up -d`；nginx
  reload 步骤不变（host nginx 仍然只反代前端不变）
- [x] 7.3 CLAUDE.md「Development Commands」更新反映 Docker 化命令；`etc/keywordiq-nginx.conf`
  顶部注释更新 + 追加一段注释掉的 Netdata Basic Auth 反代示例（呼应第 8 组）
- [x] 7.4 `uv run pytest test/` 全量：584 passed, 260 skipped（跳过的是需要真实
  `keywordiq_test` 集成库的部分，与本次改动无关，本机未接入集成库时属预期）

## 8. 可视化监控

- [x] 8.1 `docker-compose.yml` 新增 `netdata` 服务，只读挂载 Docker socket 与
  `/proc`/`/sys`/`/etc/os-release`，只绑 `127.0.0.1:19999`
- [x] 8.2 `docker/postgres-initdb/01_netdata_monitor_role.sh`：容器首次初始化时建
  `netdata` 角色并 `GRANT pg_monitor`；`docker/netdata/go.d/postgres.conf` 提供采集
  配置。**已实测验证**：Netdata 的 PostgreSQL 采集任务实际连上、成功采集（API 返回
  67 个 `postgres_*` 图表），不是只装了角色没验证能不能用
- [x] 8.3 `docker-compose.yml` 里 `db`/`netdata` 均只绑 `127.0.0.1`；
  `etc/keywordiq-nginx.conf` 追加注释掉的 Basic Auth 反代示例作为 SSH 隧道之外的另一
  个可选路径
- [x] 8.4 实测 Netdata 自身资源占用：本地 Docker Desktop（Apple Silicon）快照约
  136 MB 内存、瞬时 CPU 6.76%（周期性采集，非持续），落在设计文档预估的 100-200MB
  区间内。**这是本地开发机的数字，不等于生产 x86 ECS 的实际值**——生产部署后应该
  用 `docker stats` 重新量一次，不要直接拿这个数字当生产依据
- [ ] 8.5 **未执行**——需要真实的生产周报导入周期与部署环境，本会话不具备

## 9. Celery 结论记录

- [ ] 9.1 **未执行**——issue #83 目前没有可勾选的"验收清单"结构，建议在 PR 描述里
  引用 design.md 决策 7 的结论作为对该验收项的回应，而不是去改 issue 正文
- [x] 9.2 已在 issue #81 下留言，说明本变更对其"不建议引入 Celery"结论的重新核对与
  维持原结论（Docker 化削弱了运维成本这条理由，但根因是代码写法、且单机没有跨机分布式
  前提这两条依然成立）
