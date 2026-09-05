## 1. Docker 编排脚手架

- [ ] 1.1 编写应用 `Dockerfile`（基于 `uv` 安装依赖、构建产物，复用 `.python-version`/`uv.lock`）
- [ ] 1.2 编写 `docker-compose.yml`：`app` 服务（构建自 1.1 的 Dockerfile，`ports: ["127.0.0.1:8000:8000"]`，`restart: unless-stopped`）与 `db` 服务（`postgres:15.x` 锁定小版本镜像，bind mount 数据目录，`restart: unless-stopped`，不对外暴露端口）
- [ ] 1.3 本地起 `docker compose up`，确认 `app` 能通过服务名 `db` 连上 `db:5432`（先用空库验证网络连通，不涉及真实数据）

## 2. 自建 PostgreSQL 的 schema 建库

- [ ] 2.1 在全新空的 `db` 容器上执行 `alembic upgrade head`，核对生成的 schema（`\d+`
  或 `pg_dump --schema-only`）与阿里云 RDS 当前实际 schema 一致，尤其是分区表与物化视图
  的 DDL
- [ ] 2.2 若发现迁移链路与 RDS 实际 schema 有出入，修复缺失的迁移（不得手工在自建库上
  补 DDL）

## 3. 配置与连接池重新核算

- [ ] 3.1 更新 `.env.example`：`DATABASE_URL`/`DATABASE_URL_ASYNC` 的 host 改为 `db`
  （容器内部网络地址），连接池取设计文档定的起点值（同步 `2+3`，异步 `8+8`）
- [ ] 3.2 `db` 容器的 `max_connections`/`shared_buffers`/`effective_cache_size`/
  `work_mem` 按设计文档决策 5 的方向配置初始值（写入 compose 的 `command:` 或挂载的
  `postgresql.conf`）
- [ ] 3.3 `uv run pytest test/ -m conventions` 验证 `.env.example` 与 `config.py`
  默认值、CLAUDE.md 公示值三处一致（`test_conventions.py` 的既有回归）
- [ ] 3.4 更新 CLAUDE.md「Production Environment」「Key Configuration」两节反映新的
  连接池取值与同机部署形态

## 4. 数据迁移

- [ ] 4.1 编写迁移脚本（`scripts/migrate_to_selfhosted_pg.py` 或 shell 脚本）：确认
  `import_batch` 无 `PROCESSING`/`QUEUED` 状态 → `pg_dump --data-only --schema=analysis
  -Fc`（RDS）→ `pg_restore --data-only -j <并行度>`（自建库）
- [ ] 4.2 编写核对脚本：比对 `fact_daily`/`fact_weekly`/`dim_keyword`/`dim_product`/
  `site_data_stats` 等关键表在两侧的行数与最新 `report_date`/主键
- [ ] 4.3 在测试环境（`keywordiq_test` 或临时库）用小规模数据先跑一遍 4.1+4.2 全流程，
  验证脚本本身正确，不在真实 RDS 上首次试跑
- [ ] 4.4 选低峰时段对生产 RDS 数据执行真实迁移，跑通 4.1+4.2，核对通过后记录结果

## 5. 备份自动化

- [ ] 5.1 编写备份脚本（host cron 调用）：`docker exec db pg_dump ...` → 上传至阿里云
  OSS 的 `backups/postgres/` 前缀（复用现有 OSS 凭证）
- [ ] 5.2 编写恢复演练脚本或文档：用最近一次备份文件重建出一致的 `analysis` schema 数据
- [ ] 5.3 实际跑一次"备份 → 恢复到临时库 → 核对行数"演练，验证备份链路真实可用（不能
  只验证"备份文件生成了"就当作完成）
- [ ] 5.4 配置 cron 任务的定时执行与备份保留/轮转策略，写入部署文档

## 6. 切换与回滚准备

- [ ] 6.1 切换 `.env` 的 `DATABASE_URL`/`DATABASE_URL_ASYNC` 指向自建 `db`，
  `docker compose up -d` 拉起应用容器
- [ ] 6.2 健康检查：`/health` 与一次只读查询（如 `/api/analysis/scope`）确认应用可正常
  访问自建 PostgreSQL
- [ ] 6.3 保留 RDS 只读可用至少 7 天（覆盖至少一次日报 + 一次周报导入），验证 6.2 期间
  资源指标（`docker stats`、`pg_stat_activity`）无异常争抢
- [ ] 6.4 观察窗口结束、确认无问题后，释放 RDS 实例

## 7. 旧部署路径退役

- [ ] 7.1 移除 `etc/keywordiq.service`，更新其安装说明（若仍需非容器环境 fallback，
  在此明确记录去留决定，不留下悬而未决的文件）
- [ ] 7.2 更新 `deploy.sh`：移除 `systemctl restart`/host nginx 相关的后端重启步骤，
  改为 `docker compose build && docker compose up -d`
- [ ] 7.3 更新 CLAUDE.md「Development Commands」「Production Environment」等相关章节，
  反映 Docker 化部署形态，移除对 `start.sh`/systemd 托管的过时描述
- [ ] 7.4 `uv run pytest test/` 跑一次全量，确认配置/文档改动未破坏既有测试

## 8. 可视化监控

- [ ] 8.1 在 `docker-compose.yml` 新增 `netdata` 服务（官方镜像，只读挂载 Docker socket
  与宿主机 `/proc`/`/sys`/`/sys/fs/cgroup`，`restart: unless-stopped`），不对外暴露
  端口（不写 `ports:`，或只绑 `127.0.0.1`）
- [ ] 8.2 在自建 `db` 里建一个只读监控角色（如 `pg_monitor` 权限组），供 Netdata 的
  PostgreSQL 采集插件使用，不复用应用连接账号
- [ ] 8.3 配置访问路径：SSH 隧道或 nginx 反代 + Basic Auth（二选一，写进部署文档），
  确认公网直接访问监控端口不可达
- [ ] 8.4 实测 Netdata 自身的内存/CPU 占用，并入决策 5/9 的资源核算——若显著超出预期
  （如内存占用远高于 100-200MB 量级），重新评估是否需要调低采集频率或裁剪采集范围
- [ ] 8.5 在一次真实的周报导入期间观察监控面板，确认能看到 CPU/内存/磁盘 I/O 的峰值，
  作为「迁移后资源争抢风险」的实际验证依据（呼应设计文档风险章节）

## 9. Celery 结论记录（不实现，仅收尾）

- [ ] 9.1 确认 design.md 的"不引入 Celery"结论已同步进 issue #83 的验收清单，避免
  之后有人误以为这次顺带做了独立站抓取并发改造
- [ ] 9.2 在 issue #81 下留言/更新，说明本变更对其结论的再次核对与最终决定（不引入），
  避免重复评估
