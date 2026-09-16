## Why

KeywordIQ v2 已完成容器化自托管部署（docker-compose：app + 自建 PostgreSQL + netdata，host nginx
反代），但巡检、告警、备份核实、应用重启与版本发布目前全部依赖人工登录服务器操作，没有周期性
无人值守巡检、没有告警出口、发布靠人盯屏执行 `deploy.sh`。GitHub Issue #90 要求建立一套可在飞书
完成的运维闭环，覆盖巡检告警、备份、以及**人工确认后**的重启与发布，运维人员只有项目所有者本人，
机器人与业务 FastAPI 进程同机但完全独立运行。本变更把 Issue #90 的确认范围落成可实施的 OpenSpec
设计与任务清单。

## What Changes

- 新增一个独立运行的飞书运维机器人服务（下称 `ops-bot`），与业务 `app` 容器同机、独立
  host 级 systemd 服务（实现期修订：docker-compose 化会让受限执行器要做的
  `docker compose restart`/`nginx reload`/`deploy.sh` 陷入路径映射难题，见 design.md D1），
  共享同一个自建 PostgreSQL 容器但使用新 schema `ops`（不与业务 `analysis` schema
  混用，不引入 SQLite）。
- 新增飞书企业自建应用的事件订阅 + 卡片交互接入：状态/日志/任务查询、导入完成失败通知、
  异常告警（首次/合并限频/恢复）、每日 09:00（Asia/Shanghai）巡检摘要。
- 新增自动/手动全量备份编排：复用 `scripts/backup_selfhosted_pg.py`，机器人内置调度接管
  定时触发（不新增 host cron，避免与"未知是否已有 cron 在跑"的生产现状产生双跑歧义），
  飞书可手动触发、查询最近备份、失败准确报告失败阶段。
- 新增人工确认后的应用重启与版本发布：飞书互动卡片展示影响与前置条件，本人确认后执行；
  发布只接受允许分支上、指定提交 SHA 且必需 CI 检查全部通过的版本；复用并完善 `deploy.sh`。
- 新增导入运行期间的重启/发布阻塞机制：读取 `import_batch` 状态并与
  `app/table/upload/import_queue.py` 的咨询锁协调，检查与执行之间的认领竞态由新增的
  "运维动作占位锁" 覆盖。
- 新增任务可靠性、权限与审计基础设施：任务/去重/审计记录落 `ops` schema（持久化、可在
  机器人重启后恢复查询）、飞书回调立即应答+后台执行+主动推送结果、限定可执行动作与参数
  （不做任意 shell）、只认配置好的飞书稳定用户 ID 的独立极简鉴权（不接入现有业务 RBAC）。
- 新增外部存活探测：使用阿里云云监控·站点监控（拨测）+ 通知对象选飞书机器人，探测公网
  健康检查地址与机器人自身心跳端点；这一项主要是部署期的阿里云控制台配置与 runbook，不
  引入新的应用代码组件。
- 明确不做（继承 Issue #90 非目标）：不管理/停止 v1、不做数据迁移、不自动合并
  v2→main、不做多 IM/多环境平台、不开放任意 shell 或自动数据库回滚、不用 LLM 驱动生产
  变更（AI 日志诊断留作后续评估项）。

本变更**不影响**日报/周报导入路径本身的业务逻辑——只读取 `import_batch` 状态用于通知与
重启/发布前的空闲判定，新增一把"运维动作占位"咨询锁供 `import_queue.py` 的认领逻辑感知，
不改变导入的分区、派生值计算或事实表写入行为。

## Capabilities

### New Capabilities

- `ops-bot-platform`：飞书运维机器人的运行底座——独立服务/容器、`ops` schema 任务与审计
  存储、飞书身份与会话鉴权、事件订阅与卡片回调的接收/幂等/后台执行协议。其余三个能力都
  构建在这上面。
- `ops-monitoring-alerting`：周期巡检（服务/资源/数据库/导入/备份）、告警去重与升级、
  导入完成失败通知、每日巡检摘要、状态与有界脱敏日志查询、外部存活探测的告警链路。
- `ops-backup`：自动/手动全量备份编排、备份产物核对与失败报告、隔离环境恢复验证材料。
- `ops-release-control`：人工确认后的应用重启与版本发布，含发布前置校验（CI/SHA/分支/
  迁移影响）、确认有效期与重放防护、导入协调锁、执行与验证、失败阶段报告。

### Modified Capabilities

（无——不修改现有 `analysis`/`upload`/`auth` 等既有 spec 的需求条款；`import_queue.py`
新增的咨询锁感知点属于实现细节，不改变已发布的导入行为契约。）

## Impact

- **新增代码**：`ops_bot/` 独立服务代码、Alembic 迁移新增 `ops` schema 下的任务/审计/
  去重/告警状态表、`etc/keywordiq-ops-bot.service`（host systemd unit）、
  `docker-compose.yml` 的 `db` 服务新增 `127.0.0.1:5432` 端口映射、nginx 配置新增
  飞书回调路径反代、`.env.example` 新增飞书应用与机器人相关配置项。
- **少量改动**：`app/table/upload/import_queue.py`（认领逻辑读取运维动作占位锁）、
  `deploy.sh`（供机器人以非交互方式调用的发布路径，迁移确认闸门保留）、
  `docs/ops-agent/00-ops-agent-design.md`（标注旧稿中 RDS/CLI-only/自愈/钉钉等假设已被
  本变更取代，指向本变更）。
- **部署材料**（不进代码仓库，部署期由用户提供）：飞书企业自建应用凭证、本人飞书稳定
  用户 ID、运维群 ID、阿里云云监控拨测与通知对象配置。
- **不影响**：v1 部署、业务钉钉 SSO、`analysis` schema 下的任何表结构或查询行为。
