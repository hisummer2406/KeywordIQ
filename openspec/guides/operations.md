# 生产边界与容量

本指南是根目录 [CLAUDE.md](../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。
文中代码与命令路径均相对仓库根目录；主文件规定跨域红线，本指南保留该域的细则、理由和历史证据。
历史测量、上线状态与人工验证记录只证明记录时状态，不替代本次验证。

## Production Environment

**服务出问题排查看 `etc/observability-runbook.md`**（怎么用 request_id 关联 nginx/
应用日志、怎么读慢查询诊断、issue #4 类导入期卡死复发时重启前要采集什么），本文件不
重复那些操作步骤。

| Category | Spec |
|----------|------|
| App Server | Alibaba Cloud ECS · Alibaba Cloud Linux 3.2104 LTS 64-bit · 4 vCPU / 8 GiB RAM |
| Database | 自建 PostgreSQL 15.x（Docker 容器，与 App Server 同一台 ECS，见 `docker-compose.yml`）· `max_connections` 由 `.env` 的 `PG_MAX_CONNECTIONS` 配置，起点 120 |

应用与数据库不再是两台独立机器——`dockerize-selfhosted-infra` 变更之前是阿里云 RDS
PostgreSQL 15.0（2C4G，独立机器），出于省去 RDS 托管溢价的考虑改为自建、同机部署。
详见 `openspec/changes/archive/2026-09-15-dockerize-selfhosted-infra/design.md`。

**V2 上线拓扑（2026-09-19）**——细节与实时状态以 `docs/ops/v2-ecs-handoff.md` 为准，这里只
留写代码时会撞上的几条：

- **V1 与 V2 同机、完全隔离。** V1 仍是裸部署（域名 `amazon.liangtong.online`、后端 8000），
  **不得修改或停止**；V2 域名 `key.liangtong.online`，应用容器只绑 `127.0.0.1:8001`
  （容器内 8000，宿主机 8000 是 V1 的）。部署脚本、健康检查、logrotate、nginx 配置里凡是
  端口/域名/目录，V2 都有自己的默认值（`deploy.sh`、`.github/v2-ecs-cd.md`），照抄 V1 的
  会打到别人身上。
- **V2 的容器与数据都有独立命名**：Compose 项目 `keywordiq-v2`，数据库 `keywordiq_v2`，
  数据目录 `/var/lib/keywordiq-v2-pgdata`（bind mount），部署目录 `/var/www/keywordiq`，
  运行用户 `www`，前端产物 `/var/www/keywordiq-web`。生效的 nginx 配置是
  `/etc/nginx/conf.d/key.liangtong.online_80.conf`，仓库里的 `etc/keywordiq-v2-nginx.conf`
  是它的模板（写死了证书路径，覆盖前先 diff）。
- **数据库没有自动备份**（2026-09-20 决定）。机器人不再每日备份，也没有「备份逾期」告警；
  手动的飞书 `备份` 指令与 `scripts/backup_selfhosted_pg.py` 保留。生产库恢复只能按脚本
  docstring 手动执行，不经飞书。写方案时别默认"出事能从备份恢复"。
- **合并进 `v2` ≠ 上线。** 发布是手动触发的「Deploy V2 to ECS」，且必须等该 merge commit 的
  三项 CI 都出结果——CI 尚未产生 check-runs 就触发会被门禁拒绝（ECS 不会被改动）。

**飞书运维机器人（`ops_bot/`）是与业务 `app` 完全独立的 host 级 systemd 服务**
（`keywordiq-ops-bot`，以 `www` 运行，监听 `127.0.0.1:8101`，nginx 把 `/ops/` 反代过去）。
操作手册是 `etc/keywordiq-ops-bot-runbook.md`，设计取舍在
`openspec/changes/archive/2026-09-17-add-feishu-ops-bot/design.md`，这里只记约束：

- **它崩溃、重启不影响业务 `app`，反之亦然**；`app/` 不 import 它，它也不依赖 `app.*`。
  唯一的耦合是**咨询锁 key 两边各自定义同一个值**：`import_service._OPS_ACTION_LOCK_KEY`
  与 `ops_bot/executor` 里的 `0x4B4F`（重启/发布与导入互斥靠它）。改一边不改另一边，
  互斥静默失效、也不报错。
- **数据库侧最小权限**：连接用独立角色 `ops_bot_role`，状态表在独立的 `ops` schema
  （`OpsBase`），与业务 `Base`/`analysis` 物理隔离。**新增 ops 表要登记进
  `migration_scope.OPS_TABLES`**（不是 `V2_TABLES`），否则 autogenerate 静默忽略。
  为了让 host 级进程连得上，compose 的 `db` 才发布了 `127.0.0.1:5432`——只绑回环，别改成
  `0.0.0.0`。它自己有一组连接池（同步 2+2、异步 4+4，最多 12 条，见 `ops_bot/database.py`）。
- **只做预定义动作，且危险动作必须人工确认。** 文本指令：`状态` / `任务` / `日志 <request_id>`
  / `备份记录` / `备份`；`重启` 与 `发布 <完整 40 位 SHA>` 走确认卡片，未确认不执行。
  发布只接受 `origin/v2` 历史上、三项必需 CI 全部成功的完整 SHA，导入进行中一律拒绝。
  **生产库恢复不作为飞书指令开放。** 卡片按钮回调是未经真实客户端验证的部分。
- **单人运维，不建群**：`FEISHU_OPS_CHAT_ID` 是与机器人的单聊 `chat_id`，须用**应用身份**
  取，否则之后发消息全部 `230002`。事件订阅是「发送到服务器」，回调地址
  `https://key.liangtong.online/ops/feishu/webhook`。用 `scripts/check_feishu_ops.py` 自检。
- **外部存活探测是阿里云云监控拨测 `/health` 与 `/ops/health`**——交接文档里这一项
  **尚未配置**，在那之前机器人自己挂了没人知道。

**Capacity implications for code changes:**
- `PG_MAX_CONNECTIONS`（起点 120）是全局上限的硬顶；全局上限是**两个引擎各自的池相加**
  再乘 workers：`(DB_POOL_SIZE + DB_MAX_OVERFLOW + DB_ASYNC_POOL_SIZE + DB_ASYNC_MAX_OVERFLOW) × workers`
  （`start.sh`/Dockerfile 固定 4 workers，当前取值算下来是 84）。改任一组前先核对这个算式
- ops-bot 是 `PG_MAX_CONNECTIONS` 的**第三个占用方**（前两个是应用的同步与异步池）：最多再占 12 条（同步 2+2、异步
  4+4），合计 84 + 12 = 96，离 120 的余量只剩 24（还要给 psql/alembic/运维脚本）。
  上调任何一组池之前把它算进去
- **应用与数据库现在共享同一组 4 vCPU / 8 GiB**（此前数据库在独立的 RDS 机器上，不与
  应用争抢）：nginx、4 个 uvicorn worker、导入任务、自建 PostgreSQL 进程本身、Netdata
  监控容器、ops-bot，**以及同机的 V1 服务**全部挤在这台机器上；导入已由咨询锁串行化，
  勿再引入并行分片而不实测
- 8 GiB RAM 下导入靠 `COPY` 流式装载保持内存有界；不要引入整文件读取或全量 DataFrame。
  自建 PostgreSQL 的 `shared_buffers`/`effective_cache_size`/`work_mem`（`.env` 的
  `PG_*` 系列）同样要在这 8 GiB 里核算，不能只顾应用侧内存
