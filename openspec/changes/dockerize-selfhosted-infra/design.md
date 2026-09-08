## Context

现状：阿里云 ECS（4 vCPU/8 GiB）跑 host nginx + systemd 托管的 uvicorn（4 workers，
`start.sh`），数据库是阿里云 RDS PostgreSQL 15（2 vCPU/4 GiB，`max_connections=400`）。
两者是两台独立机器，容量互不干扰——这正是现有连接池算式
`(DB_POOL_SIZE+DB_MAX_OVERFLOW+DB_ASYNC_POOL_SIZE+DB_ASYNC_MAX_OVERFLOW)×workers`
（当前 240，文档记录的目标值是 84）背后的隐含前提。

数据规模（决定自建 PG 要扛多大量）：约 6600 万行/月、16 GB/月增长；三个月保留窗口下
满窗约 2 亿行/50 GB。日报 30 万行/150MB 每天导入，周报 300 万行/1.5GB 每周导入，导入
用 `pg_try_advisory_lock` 串行化，走 `COPY` 流式装载，内存有界。

本变更要做两件事、评估一件事：
1. 部署形态从「host nginx + systemd + 裸 uvicorn」改为 Docker 编排
2. PostgreSQL 从阿里云 RDS 迁移为自建，与应用**同一台 ECS**
3. 重新评估是否引入 Celery + Redis（issue #81 的结论在 Docker 化前提下是否仍成立）

## Goals / Non-Goals

**Goals:**
- 应用与自建 PostgreSQL 均由 Docker Compose 管理生命周期，取代 `etc/keywordiq.service`
  与裸 `start.sh`
- 生产数据从阿里云 RDS 完整、可验证地迁移到自建 PG，且有明确的回滚窗口
- 自建 PG 有可运行的备份方案（RDS 自动备份消失后的等价替代）
- 连接池、`THREAD_POOL_LIMIT`、PostgreSQL 自身参数按"应用与数据库共享同一组
  4 vCPU/8 GiB"重新核算并写回配置与文档
- 对是否引入 Celery + Redis 给出明确结论（不留待后续再议）
- 补上 RDS 控制台监控图表消失后的可视化监控（CPU/内存/磁盘/Docker 容器/PostgreSQL
  指标），且监控本身的资源占用要计入决策 5 的核算，不能反过来挤占应用/数据库的余量

**Non-Goals:**
- 不做多机水平扩展、不引入 Kubernetes
- 不迁移阿里云 OSS（导入直传路径不变）
- 不做 Prometheus/Grafana 级别的长期指标留存与自定义告警规则引擎——当前需求是"看清
  这台机器现在扛不扛得住"，不是"建一套可定制的可观测性平台"，超出这个范围留给以后
  真正需要时再评估
- 不容器化 nginx（host nginx 已经工作正常，本变更不动它，只改它反代的后端从「裸
  uvicorn 进程」变成「Docker 容器里的 uvicorn」，监听地址不变仍是 `127.0.0.1:8000`）
- 不在本变更里实现 Celery（即便结论是"将来需要"，具体实现是另一个变更）
- 不改动日报/周报导入的业务逻辑（COPY、维度补齐、分区写入算法不变）

## Decisions

### 1. 容器拓扑：只容器化应用与数据库，nginx 留在 host

`docker-compose.yml` 编排两个服务：`app`（uvicorn，构建自仓库 `Dockerfile`）与 `db`
（官方 `postgres:15` 镜像）。`app` 仍然只监听 `127.0.0.1:8000`（`ports: ["127.0.0.1:8000:8000"]`），
host nginx 的反代配置（`etc/keywordiq-nginx.conf`）**不需要改动**。

**考虑过的替代方案**：连 nginx 一起容器化，换取"整套用 Docker 管理"的一致性。放弃的
理由是这条反代配置已经稳定工作，容器化 nginx 还要重新处理证书路径、host 网络模式或
端口映射，对本变更的目标（省 RDS 费用、Docker 管进程生命周期）没有增量收益，只增加
本次改动的风险面。

### 2. app 与 db 之间用 Docker 内部网络，不用 Unix socket

`app` 通过 compose 的服务发现连 `db:5432`（标准 bridge 网络 + DNS 别名），
`DATABASE_URL`/`DATABASE_URL_ASYNC` 的 host 从 RDS 内网地址改成 `db`。

**考虑过的替代方案**：Unix socket（同机部署下理论上更快、且不监听 TCP 端口）。放弃的
理由是两个独立容器共享 socket 文件需要额外挂载一个共享 volume，复杂度换来的性能收益
在读多写少、单条查询延迟由 SQL 执行主导的场景下可忽略；docker 内部 bridge 网络本身
不对宿主机暴露端口（`db` 服务不写 `ports:`，只在 compose 内部网络可达），安全性与
Unix socket 等价。

### 3. PostgreSQL 数据落地：host 目录 bind mount，不用具名卷

`db` 的 `PGDATA` 挂载到宿主机固定路径（如 `/opt/keywordiq/pgdata`），而不是 Docker
具名卷。**理由**：bind mount 让备份脚本、磁盘用量监控、灾难恢复时的直接文件访问都不必
经过 `docker volume inspect` 找路径，与"迁移与备份要足够简单、不能只有懂 Docker 内部
机制的人能操作"这条运维诉求一致。代价是路径与容器生命周期解耦后，误删宿主机目录不会
有 Docker 层面的保护——记入下文风险。

### 4. Alembic 迁移方案：`alembic upgrade head` 建库，而非 `pg_dump` 建库

自建 PG 的 schema 由**全新空库跑 `alembic upgrade head`** 建出，不是从 RDS `pg_dump`
schema 再灌进去。**理由**：迁移文件是分区表与物化视图 DDL 的唯一权威来源（这两类对象
不在 SQLAlchemy autogenerate 范围内，历史上就要求手写迁移并用
`alembic upgrade head --sql` 核对）；从空库走一遍完整迁移链路，等于顺带验证了"迁移
历史本身足以重建出与 RDS 完全一致的 schema"——这本来就该成立，但从未在真正的空库上
验证过。若跑出来的 schema 与 RDS 实际 schema 有出入，说明迁移链路本身有缺口，必须先
修复迁移，而不是让自建库带着这个缺口上线。

数据部分用 `pg_dump --data-only --schema=analysis -Fc`（RDS 侧）→
`pg_restore --data-only -j <并行度>`（自建库侧，schema 已由 Alembic 建好），
大表按分区并行恢复以控制停机窗口。

### 5. 连接池与 PostgreSQL 参数：同机共享资源下的核算

RDS 独立一台机器时的策略是"两个引擎的池相加 × 4 workers"，当前实际值 240、文档记录
的目标值是 84。同机部署后，**PostgreSQL 进程本身也要在这 4 vCPU/8 GiB 里占一份**，
CLAUDE.md 里"降池子前先观测 `pg_stat_activity`"这条纪律现在从"建议"变成"前置条件"
——本次迁移就是那个观测窗口。

本变更落地时采用文档记录的目标值作为起点（同步 `DB_POOL_SIZE=2`/`DB_MAX_OVERFLOW=3`，
异步 `DB_ASYNC_POOL_SIZE=8`/`DB_ASYNC_MAX_OVERFLOW=8`，合计 21×4=84），并在 PG 侧
`max_connections` 设为略高于 84 的余量（如 120，覆盖 psql/alembic/脚本连接与迁移期间
双写）。PG 自身参数（同一份 8 GiB 里划给数据库的部分）：`shared_buffers≈2GB`、
`effective_cache_size≈4GB`、`work_mem` 保守取值（避免并发查询下工作内存叠加打满内存）
——具体数值在实现阶段用真实周报导入负载做一轮观测再定稿，本设计只定方向与起点。

`THREAD_POOL_LIMIT`（当前 32，与连接池无关，服务导出/探活/bcrypt/同步导入共用）
**不随本次改动调整**——它的约束来源与连接池无关，同机部署不改变这三类工作的性质。

### 6. 备份：host cron + `pg_dump` + 上传 OSS，不新增备份容器

一条 host 上的 cron（不放进 compose，避免为了一个定时任务再引入一个常驻容器）执行
`docker exec db pg_dump ...`，产物上传到阿里云 OSS 的独立前缀（复用已有 OSS 凭证，
与导入用的 `imports/` 前缀区分，如 `backups/postgres/`）。

**考虑过的替代方案**：compose 里加一个专门跑 `pg_dump` 定时任务的容器（如
`prodrigestivill/postgres-backup-local` 一类镜像）。放弃的理由是 host cron 已经是
项目里熟悉的机制（`scripts/prune_retention.py` 走的也是这条路），不需要为同一类需求
引入第二套定时任务基础设施。

### 7. Celery + Redis：结论是**本变更不引入**

issue #81 已经对"是否为独立站抓取并发引入 Celery/Redis"做过完整评估，结论是不建议，
依据三条：(a) 并发天花板是 `refresh_due_store` 一次只抓一个站点这个写法造成的，不是
缺 Celery 造成的，`asyncio.gather`+信号量足够且零新依赖；(b) 当前无容器化，新增一个
要保活的服务运维成本不小；(c) 单机 4C8G，没有跨机分布式的前提。

本变更把 (b) 的前提改变了——Docker 化之后，跑一个 Redis 容器的运维成本确实显著低于
"在裸 host 上新增一个要手动装、要配 systemd、崩了没人管的服务"。但重新核对后，
**(a) 与 (c) 依然完全成立、且与是否容器化无关**：

- (a) 是代码写法问题，不是基础设施问题——加了 Celery，`refresh_due_store` 内部"一次
  只抓一个站点"这行代码本身不会自动变并发；真正要做的还是那个 `asyncio.gather`+
  信号量改造，与用不用 Celery 无关。
- (c) 本次恰恰是把 PostgreSQL 也塞进同一台 4C8G 机器，可用资源余量比改造前更紧，而不是
  更松——这与"需要跨机分布式所以要上 Celery"的前提方向相反。
- 额外代价：Redis 引入新的持久化决策（任务队列丢失是否可接受）、新的监控维度（队列
  积压）、新的镜像与依赖；而这些都不解决 issue #81 已经指出的真正问题（观测缺失、
  单次抓取不并发）。

**结论：本变更不引入 Celery/Redis。** 独立站抓取并发问题按 issue #81 的建议路径推进
（`asyncio.gather`+信号量改造 + 积压可观测性），那是一个独立的、更小代价的变更，不
依赖本次 Docker 化。若将来真的需要跨机分布式（规模超出单机 4C8G），再重新评估 Celery，
届时 Docker 化已经完成，引入门槛只会比现在更低。

### 8. 废弃 `etc/keywordiq.service`，依赖 Docker 原生重启策略

`app`/`db` 两个服务都设 `restart: unless-stopped`；Docker daemon 本身随系统 systemd
（`docker.service`，安装 Docker 时自带）开机自启。不再需要一个额外的 systemd unit 去
拉起 `start.sh`——那层职责现在由 Docker 自己承担，减少一层需要维护的编排。
`etc/keywordiq.service` 整个移除（连同其安装说明），`deploy.sh` 的"重启后端"步骤改为
`docker compose up -d --build`。

### 9. 可视化监控：单容器 Netdata，不上 Prometheus/Grafana

监控用**单容器 Netdata**：挂载 Docker socket（只读）与宿主机 `/proc`/`/sys` 后自动发现
`app`/`db` 容器与宿主机系统指标，另装 PostgreSQL 采集插件（需要在自建 `db` 里建一个
仅有只读监控权限的角色，见任务清单）连自建库，开箱给出可视化面板，配置量接近零。

**考虑过的替代方案**：Prometheus + Grafana + node_exporter + postgres_exporter。放弃
的理由是这套要多跑 3-4 个容器、手工写抓取规则与面板，额外内存占用（约 500MB-1GB 量级）
在决策 5 已经把 8 GiB 切给应用+PG 的情况下不是小数目，且这套东西本身也要有人长期维护
——与本次"少加一层运维负担"的初衷相悖。Netdata 换掉的是"长期历史留存"与"可定制告警"
这两项当前用不上的能力，换来的是显著更低的资源占用与维护成本。

**安全边界**：Netdata 面板默认无鉴权，**不对公网暴露**——只监听内部 Docker 网络或
`127.0.0.1`，需要远程查看时经 SSH 隧道或 nginx 反代加一层 Basic Auth，不新开一个无鉴权
的公网入口。

**资源占用计入核算**：Netdata 自身典型占用约 100-200MB 内存、CPU 占用轻（周期性采集，
非持续高负载）。决策 5 的连接池/PG 参数核算与本变更"实测一次完整周报导入期间的资源
峰值"这一步，都要把 Netdata 自己的占用算作固定开销的一部分，不能只算应用与 PG。

## Risks / Trade-offs

- **[风险] 同机资源争抢**：周报导入（COPY 300 万行/1.5GB，CPU/I·O 密集）与 200 并发
  读请求、以及 PostgreSQL 自身进程现在挤在同一组 4 核上，比"数据库在另一台机器"时更
  容易互相拖慢。
  **缓解**：连接池按目标值起步（决策 5）；上线后用 `docker stats` + `pg_stat_activity`
  实测一次完整周报导入期间的资源峰值，超出预期再调；导入本身已由咨询锁串行化，未变。

- **[风险] 单点故障合并**：现在 ECS 故障与 RDS 故障是两个独立故障域，合并部署后 ECS
  一次故障应用与数据库一起不可用。
  **缓解**：这是本变更明确接受的取舍（省成本是本次的首要目标），用可靠的备份
  （决策 6）把"故障后能恢复"顶上，不追求"故障不发生"。

- **[风险] 迁移窗口数据不一致**：`pg_dump`/`pg_restore` 之间若应用仍在写 RDS，会丢失
  窗口期的写入；仅检查 `import_batch` 无在跑批次不够——用户操作、词库、竞对轮询等其余
  写路径完全不受这个检查约束，检查通过与 dump 开始之间也仍有空隙可以插入新写入。
  **缓解**：选低峰时段做迁移；`scripts/migrate_to_selfhosted_pg.py` 在确认没有导入任务
  在跑（`import_batch` 无 `PROCESSING`/`QUEUED`/`UPLOADING`）之后，会把 RDS 切为拒绝新写
  事务并断开其余会话（`_quiesce_source`），堵住检查与 dump 之间的窗口；这一步切成只读
  之前若源库上已有别的写入在跑，那部分数据依然进不了 dump，低峰时段仍是前提而非可以
  跳过的步骤。迁移后按关键表（`fact_daily`/`fact_weekly`/`dim_keyword`/`dim_product`/
  `site_data_stats`）逐一核对行数与最大主键，核对通过才切 `DATABASE_URL`；RDS 保留至少
  7 天只读可查，作为回滚与核对基准，确认无误后再释放。**这个只读状态是单向的**：
  观察窗口内自建库一旦有新写入，RDS 就已经落后，见下文 Migration Plan 步骤 6 的回滚
  限制——不是"随时能切回去"的安全网。

- **[风险] bind mount 路径被误操作**：PG 数据目录是宿主机普通目录，没有 Docker 卷层面
  的误删保护。
  **缓解**：路径固定、写进部署文档；结合决策 6 的每日备份兜底最坏情况。

- **[风险] PostgreSQL 版本/参数漂移**：阿里云 RDS 的补丁、版本升级不再自动发生，之后
  全部靠自己维护。
  **缓解**：Docker 镜像标签锁定具体小版本（如 `postgres:15.8`，不用 `postgres:15` 这类
  会随时间指向不同小版本的浮动标签），升级走人工评估 + 更新标签的流程，写进运维文档。

- **[风险] 监控本身挤占本就紧张的资源，或反过来无鉴权暴露**：Netdata 虽轻量，但仍是
  第三个常驻进程；若挂载 Docker socket 的方式配置不当，也可能扩大攻击面。
  **缓解**：决策 9 已把 Netdata 的资源占用计入核算而非额外开销；Docker socket 只读挂载；
  面板不监听公网地址，需要访问走 SSH 隧道或加鉴权的反代。

## Migration Plan

1. 在 `db` 容器（全新、空库）上跑 `alembic upgrade head`，核对生成的 schema（含分区、
   物化视图）与 RDS 实际 schema 一致（`\d+` 或 `pg_dump --schema-only` 逐项比对）
2. 确认迁移窗口内没有导入任务在跑（`import_batch` 表无 `PROCESSING`/`QUEUED`/
   `UPLOADING`），随后把 RDS 切为拒绝新写事务并断开其余会话——`migrate_to_selfhosted_pg.py`
   自动做这两步（`_check_no_active_import` + `_quiesce_source`），不需要手工执行
3. `pg_dump --data-only --schema=analysis -Fc` 导出 RDS 数据；`pg_restore --data-only
   -j <并行度>` 灌入自建库
4. 核对关键表行数、最大主键/最新 `report_date`、`site_data_stats` 汇总值与 RDS 一致
5. 切换 `.env` 的 `DATABASE_URL`/`DATABASE_URL_ASYNC` 指向 `db`，`./deploy.sh` 拉起应用
   容器，跑一次健康检查（`/health`）与一次只读查询（`/api/analysis/scope`）。**首次由旧
   路线切过来时，遗留的 `keywordiq.service` 仍绑着 127.0.0.1:8000**，与容器要绑的端口
   相同——仓库里删掉 `etc/keywordiq.service` 只影响下次全新安装，不会停掉宿主机上已
   安装、正在跑的 unit，不先停会让容器因端口占用起不来。`deploy.sh` 已在
   `docker compose up -d` 前自动 `systemctl stop`/`disable` 它（Codex 审核 PR #84），
   这一步因此不需要手工执行
6. RDS 保留只读、不删除，观察窗口（建议 7 天，覆盖至少一次日报 + 一次周报导入）；
   **回滚不是把 `.env` 换回 RDS 这么简单**——切换只在自建库还没有任何新写入的极短
   窗口内才是无损的；观察窗口内一旦有导入或用户写入落到自建库，RDS 已经落后，改回
   `.env` 会丢弃这部分写入，服务从至少落后一次日报、一次周报的数据重新起步。真出现
   资源争抢或数据问题要回滚，必须先把观察窗口内自建库上的写入同步或重放回 RDS
   （或明确接受这部分数据的损失），配置改回去不能替代这一步——本设计不预先规划反向
   同步工具，真到了要回滚的那一刻按当时的写入量现场评估
7. 观察窗口结束、确认无问题后，释放 RDS 实例；同一批清理宿主机上残留的
   `keywordiq.service` unit 文件本身（第 5 步已停用/禁用，这里是彻底移除，
   `systemctl disable` 不删文件）、更新 `deploy.sh`/CLAUDE.md 反映新部署形态

## Open Questions

- PostgreSQL 具体参数（`shared_buffers`/`work_mem`/`max_connections` 最终值）：起点见
  决策 5，需要真实周报导入负载下的观测数据才能定稿，本设计不预先拍板具体数字
- 是否需要一个类似 `scripts/check_oss.py`/`scripts/check_dingtalk_sso.py` 的验证脚本
  （跑一遍迁移+核对流程并断言），把"迁移正确性"从人工核对变成可重复执行——倾向需要，
  留给 tasks 阶段细化
- `pg_dump`/`pg_restore` 的并行度取值：取决于自建 PG 在导入期间能分给这个一次性操作
  多少 I/O 余量，实现阶段用小规模数据先试跑再定
- 备份的保留策略（保留几份、多久轮转清理 OSS 上的旧备份）：本设计只定了"备份要存在"，
  具体保留周期留给实现阶段按 OSS 存储成本与恢复需求权衡
