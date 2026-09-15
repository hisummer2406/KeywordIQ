# containerized-deployment Specification

## Purpose
TBD - created by archiving change dockerize-selfhosted-infra. Update Purpose after archive.
## Requirements
### Requirement: 容器化生命周期管理
应用与自建 PostgreSQL 的进程生命周期 SHALL 由 Docker Compose 管理，异常退出后自动
重启，不依赖新增的 systemd unit 或人工手动拉起。

#### Scenario: 容器崩溃后自动恢复
- **WHEN** `app` 或 `db` 容器进程异常退出
- **THEN** Docker 依据 `restart: unless-stopped` 策略在数秒内重新拉起该容器，不需要
  人工干预

#### Scenario: 宿主机重启后服务自动回来
- **WHEN** ECS 宿主机重启
- **THEN** Docker daemon 随系统自启，并按 restart 策略拉起 `app` 与 `db` 容器；系统上
  不再存在 `etc/keywordiq.service` 或依赖它才能恢复服务的路径

### Requirement: 自建 PostgreSQL 数据持久化与可访问性
自建 `db` 容器的数据 SHALL 持久化在宿主机可直接访问的路径（bind mount），容器删除
重建不丢数据，且备份/运维脚本无需经过 Docker 卷管理命令即可定位数据文件。

#### Scenario: 容器重建数据保留
- **WHEN** `db` 容器被删除并重新创建（如镜像版本更新）
- **THEN** 挂载的宿主机数据目录内容不变，重建后的容器读取到相同数据

### Requirement: 应用与数据库的资源核算适配同机部署
连接池（`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`/`DB_ASYNC_POOL_SIZE`/`DB_ASYNC_MAX_OVERFLOW`）
与自建 PostgreSQL 自身内存参数 SHALL 按"应用与数据库共享同一组 4 vCPU/8 GiB"重新
核算，不得沿用"数据库在独立机器"时的取值。

#### Scenario: 连接池总量在 PostgreSQL max_connections 安全余量内
- **WHEN** 应用以 4 个 uvicorn worker 启动
- **THEN** `(DB_POOL_SIZE+DB_MAX_OVERFLOW+DB_ASYNC_POOL_SIZE+DB_ASYNC_MAX_OVERFLOW)×4`
  必须小于自建 `db` 配置的 `max_connections`，并为非应用连接（psql/alembic/运维脚本）
  留出余量

### Requirement: 数据从阿里云 RDS 完整迁移且可验证
自建 PostgreSQL SHALL 包含从阿里云 RDS 完整迁移的数据，且切换前必须通过关键表行数或
校验值核对与源库一致。

#### Scenario: 迁移后关键表行数一致
- **WHEN** 完成 `pg_dump`/`pg_restore` 数据迁移
- **THEN** `fact_daily`、`fact_weekly`、`dim_keyword`、`dim_product`、`site_data_stats`
  等关键表在自建库中的行数与 RDS 源库一致

#### Scenario: schema 由 Alembic 迁移重建，不做人工搬迁
- **WHEN** 在全新的空自建 PostgreSQL 实例上执行 `alembic upgrade head`
- **THEN** 生成的 schema（含分区表与物化视图）与 RDS 当前实际 schema 一致，无需任何
  手工 DDL 补丁

### Requirement: 迁移窗口内可回滚，观察窗口内的回滚须先处理自建库产生的新写入与 RDS 只读状态
切换 `.env` 到自建 PostgreSQL 之后、自建库尚未产生任何新写入前，SHALL 能够仅通过修改
配置回滚到 RDS，不需要重新执行数据迁移；但迁移脚本（`_quiesce_source`）在迁移开始时
已把 RDS 持久切为拒绝新写事务，这个状态不会因为把 `.env` 改回指向 RDS 而自动解除，
回滚 SHALL 同时解除它，否则应用配置层面已经"改回 RDS"，实际却因数据库本身仍是只读
导致写入全部失败。一旦观察窗口内自建库已产生新的导入或用户写入，回滚 SHALL 先将这部分
写入同步或重放回 RDS，或由执行回滚的人明确记录接受这部分数据的丢失——仅修改配置不能
替代这一步，否则服务会从落后至少这部分写入的数据重新起步。

#### Scenario: 切换后尚无新写入时回滚
- **WHEN** 切换 `.env` 到自建库之后、自建库尚未产生任何新的导入或用户写入
- **THEN** 将 `DATABASE_URL`/`DATABASE_URL_ASYNC` 改回指向 RDS、解除 RDS 的
  `default_transaction_read_only`（`migrate_to_selfhosted_pg.py` 的 `_quiesce_source`
  docstring 给出了本地演练验证过的还原命令），并重启应用容器，即可恢复服务且不丢失
  任何数据

#### Scenario: 观察窗口内已有新写入时回滚
- **WHEN** 观察窗口内自建库已产生新的导入或用户写入，此时发现资源争抢或数据不一致问题
- **THEN** 回滚前必须先把这部分写入同步或重放回 RDS，或由执行回滚的人明确记录接受这部分
  数据丢失，并同样要解除 RDS 的 `default_transaction_read_only`；仅修改
  `DATABASE_URL`/`DATABASE_URL_ASYNC` 配置不满足本条回滚要求

### Requirement: 自建部署有可视化监控
自建 Docker 部署 SHALL 提供可视化监控面板，覆盖宿主机系统指标、各容器（`app`/`db`）
运行状态与自建 PostgreSQL 指标，作为阿里云 RDS 控制台监控图表消失后的等价替代。

#### Scenario: 面板可见关键指标
- **WHEN** 访问监控面板
- **THEN** 能看到宿主机 CPU/内存/磁盘/网络、`app`/`db` 各容器的资源占用、以及自建
  PostgreSQL 的连接数/查询等指标，且这些指标是自动采集的，不需要额外手工打点

#### Scenario: 监控面板不对公网暴露
- **WHEN** 从公网直接访问该 ECS 的公网 IP 与监控面板端口
- **THEN** 无法访问到监控面板；面板只能通过内部网络、SSH 隧道或加了鉴权的反代路径访问

### Requirement: 自建 PostgreSQL 有可运行的备份
自建 PostgreSQL SHALL 有自动化备份机制，备份产物存储在应用所在机器之外，作为 RDS 原有
自动备份能力消失后的等价替代。

#### Scenario: 定时备份产出并上传到异地存储
- **WHEN** 到达备份计划的执行时间点
- **THEN** 系统执行一次 `pg_dump` 并将备份文件上传至独立于本机的存储位置（如阿里云 OSS
  的专用前缀），不依赖本机磁盘作为唯一副本

#### Scenario: 备份可用于恢复
- **WHEN** 使用最近一次备份文件执行恢复
- **THEN** 能够重建出与备份时刻一致的 `analysis` schema 数据，不依赖其他未被备份覆盖
  的状态

