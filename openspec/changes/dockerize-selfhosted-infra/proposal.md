## Why

生产环境目前是阿里云 ECS（4 vCPU/8 GiB，host nginx + systemd 托管 uvicorn）+ 阿里云 RDS
PostgreSQL 15（2 vCPU/4 GiB）。RDS 相对自建 PostgreSQL 有明显的托管溢价，且绑定阿里云
托管服务。已决定摆脱这项托管费用：PostgreSQL 改为自建、与应用同机部署，部署形态改由
Docker 编排管理（取代裸 `start.sh` + systemd）。详见 issue #83。

这次改造同时改变了 issue #81（评估独立站抓取轮询的并发天花板）里"不建议引入 Celery"
的一条前提——该结论部分依据"当前无容器化，新增一个要保活的服务运维成本不小"，Docker
化之后这条依据被削弱，因此本变更需要重新核对是否引入 Celery，而不是延续旧结论或另起
新炉灶重新论证。

## What Changes

- 生产部署从「host nginx + systemd 托管的裸 uvicorn」改为 Docker 编排（应用容器 +
  自建 PostgreSQL 容器，按需含 nginx 容器）
- PostgreSQL 从阿里云 RDS 迁移为自建实例，与应用**同一台 ECS**（不新增数据库专用机器）
- 连接池（`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`/`DB_ASYNC_POOL_SIZE`/`DB_ASYNC_MAX_OVERFLOW`）
  与 `THREAD_POOL_LIMIT` 按"应用与数据库共享同一组 4 vCPU/8 GiB"重新核算
- 补齐自建 PostgreSQL 的备份方案（RDS 的自动备份消失后必须有等价替代）
- 重新评估是否引入 Celery + Redis：核对 issue #81 的结论在 Docker 化前提下是否仍然
  成立，给出明确的引入/不引入决定（**不预设结论**）
- **BREAKING**：现有 `etc/keywordiq.service`（systemd unit）与 `deploy.sh`/`start.sh`
  的部署路径被本变更取代，需要明确废弃或降级为非容器环境的 fallback

## Capabilities

### New Capabilities
- `containerized-deployment`：应用与自建 PostgreSQL 的容器化部署与运维形态——容器编排
  拆分、镜像构建、自建 PG 的初始化与备份、数据从阿里云 RDS 的迁移路径、同机资源共享下
  的连接池与线程池核算。是否引入 Celery/Redis 的评估结论与（若引入）其容器化编排方式
  也在此能力范围内。

### Modified Capabilities
（无。本变更不改动任何既有 API 契约或查询/导入的业务逻辑，只改变其运行时部署与资源
环境；`background-job-poller` 现有行为不变——若最终结论是引入 Celery，其编排范围记在
`containerized-deployment` 而非改写 `background-job-poller` 的既有需求。）

## Impact

- **不影响日报、周报导入的业务逻辑**（COPY 流式装载、维度补齐、分区写入等均不变），
  但两条路径共享的运行时资源环境改变：导入期间的 CPU/内存/磁盘 I/O 与数据库进程不再
  分属两台机器，而是在同一台 4C8G ECS 上争抢，连接池与 `THREAD_POOL_LIMIT` 的既有
  取值（按"数据库在另一台机器"假设核算）需要重新核算
- 受影响文件：`deploy.sh`、`start.sh`、`etc/keywordiq.service`、`.env.example`
  （`DATABASE_URL`/`DATABASE_URL_ASYNC`/`DB_*` 系列）、CLAUDE.md 的
  「Production Environment」「Key Configuration」两节
- 依赖变化：新增 Docker/Docker Compose 作为部署依赖；若引入 Celery，新增 `celery`、
  `redis` 客户端库与 Redis 服务
- 不影响：阿里云 OSS 直传导入路径（`OSS_*` 配置与 STS 凭证流程不变）、前端、
  RBAC/鉴权、V2 数据模型与查询层
