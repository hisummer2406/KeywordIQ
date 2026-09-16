## Context

KeywordIQ v2 现行部署形态（`dockerize-selfhosted-infra` 变更后）：docker-compose 管理
`app`（4 uvicorn worker）+ `db`（自建 PostgreSQL 15.8，不对宿主机暴露端口，bind mount）+
`netdata`，host nginx 托管前端 SPA 并反代 `/api`；应用与数据库共享同一台 4 vCPU / 8 GiB
ECS。业务侧已有 `/health`、`/health/pool`、`monitoring.py` 指标采样、
`scripts/prune_retention.py`、`scripts/backup_selfhosted_pg.py`（全量 pg_dump→OSS）、
`import_batch` 状态表 + `import_queue.py` 的咨询锁轮询接管、按天 `TimedRotatingFileHandler`
日志（`request_id` 贯穿）。已有一份钉钉 SSO 集成（`app/auth/dingtalk_client.py`），证明
"企业内部应用 + tenant/app access token 缓存 + 并发换取用 `asyncio.Lock`" 这套模式在本仓库
跑通过，且**生产已有一个公网可达的 HTTPS 域名**（`SSO_REDIRECT_URI` 依赖它）。

旧设计稿 `docs/ops-agent/00-ops-agent-design.md`（v0.1，2026-09-09）基于阿里云 RDS + host
nginx/systemd 裸 uvicorn + 钉钉 + SSH CLI 交互，三项前提均已被后续变更或本次 Issue 推翻
（自建 PG 容器、飞书替代钉钉运维入口、互动卡片替代 CLI）。本设计不沿用旧稿的组件选型，
只沿用其中仍然成立的原则（能脚本判的不用模型判、复用已有资产、最小权限、白名单化操作）。

本变更是 GitHub Issue #90 的工程落地，Issue 已完成全部产品决策；本设计文档的任务是把
Issue 列出的 6 项"实施前须在 OpenSpec 落定"的工程决策拍板，并给出可验证的架构。

## Goals / Non-Goals

**Goals：**

- 一个与业务 `app` 完全独立进程/生命周期、同机部署的飞书运维机器人服务（`ops-bot`）。
- 巡检/告警/日报、备份编排、人工确认重启与发布、任务可靠性与审计，四块能力共享同一套
  飞书接入、鉴权、任务与审计存储底座。
- 所有决策落到具体可验证的数字、表结构、锁机制、API 调用序列——不是留白待定。

**Non-Goals（继承 Issue #90，不在此重复列出理由）：**

- 不管理/部署/停止 v1，不做 v1→v2 数据迁移，不自动合并 `v2`→`main`。
- 不建设多用户运维平台、多 IM 适配；不新增钉钉运维入口。
- 不开放任意 shell、不做生产数据库恢复删除、不做自动数据库回滚。
- 不用 LLM 做权限判断或直接驱动生产变更；旧稿的两级漏斗 AI 日志诊断本次不做。

## Decisions

### D0. 总体架构：单一变更、四个能力 spec，共享一套底座

`ops-bot-platform`（运行底座：独立服务、`ops` schema、飞书鉴权、回调协议）是
`ops-monitoring-alerting` / `ops-backup` / `ops-release-control` 三者共同的依赖——三者都要
"发飞书消息""认领后台任务""写审计记录""判断谁在说话"。拆成多个 OpenSpec 变更会迫使底座
被至少三份 design 文档各写一遍、且难以保证一致（历史上 `fact_growth`/`fact_dropped` 这类
"口径只有一处定义"的教训就是这么来的）。故本次一次性产出四个 spec、一份 design，实现阶段
（tasks.md）内部仍按底座→巡检告警→备份→发布的顺序分批提交，允许分批开 PR，但设计只有一份。

### D1. 服务形态、受限执行器、任务存储、调度机制（Issue 决策项 1）

**服务形态**：新增 docker-compose 服务 `ops-bot`，与 `app` 共用同一个 Dockerfile 镜像、
不同 entrypoint（`python -m ops_bot.main`），监听 `127.0.0.1:8101`（只本地回环，不对公网
暴露）。host nginx 新增一个 location（如 `/ops/feishu/webhook`）反代到这个端口，复用现有
证书与域名——不需要新开公网入口。容器 `restart: unless-stopped`，与 `app`/`db` 同等对待。

代码位置：仓库根新增顶层包 `ops_bot/`（与 `app/` 同级，不挂在 `app/table/*` 下——它不是
一个数据域查询模块，是独立服务），内部仍遵守项目的分层约定（model/crud/service/api）：

```
ops_bot/
├── main.py            # FastAPI 极简 app：/ops/feishu/webhook（事件+卡片回调）、/ops/health
├── settings.py         # 复用 config.py 的 Settings 模式，新增 OPS_*/FEISHU_* 字段
├── database.py         # 独立引擎，连接串来自 OPS_DATABASE_URL（见 D1 最小权限段）
├── feishu/
│   ├── client.py       # tenant_access_token 缓存 + asyncio.Lock 防并发换取（同构 dingtalk_client.py）
│   ├── events.py        # URL 校验挑战、消息事件、卡片回调分发
│   ├── cards.py          # 卡片模板拼装（确认卡片、结果卡片、日报卡片）
│   └── auth.py           # 校验 open_id 与 chat_id 白名单
├── checks/                # 巡检检查器：quick/full/daily 三组，每组一个函数列表
├── executor/               # 受限执行器（见下）
├── scheduler.py            # 进程内多组轮询循环，见下
├── model.py / crud.py / service.py / schemas.py   # ops schema 的任务/审计/告警/备份记录
└── api_internal.py         # 供 events.py 调用的内部编排（受理请求→建任务→丢后台执行）
```

**受限执行器**：`ops_bot/executor/` 只暴露一个封闭的动作枚举（`OpsAction`：
`RESTART_APP` / `RELOAD_NGINX` / `BACKUP_NOW` / `DEPLOY`），每个动作对应一个写死 argv 列表
的 `subprocess.run(..., shell=False)` 调用（例如 `["docker", "compose", "restart", "app"]`、
`["./deploy.sh", "--yes", ...]`），**没有任何模板把聊天文本拼进命令行或环境变量**。执行器
函数签名只接受强类型参数（如 `DeployAction(target_sha: str, with_migration: bool)`），
不接受自由字符串。超时统一 300 秒，输出（stdout/stderr 截断到 8KB）落 `ops_task.output`。

**任务/审计存储**：复用现有自建 PostgreSQL 容器，新开 schema `ops`（不用 `analysis`、
不引入 SQLite——理由见下方"最小权限"段的取舍说明）。核心表（Alembic 迁移新建，SQLAlchemy
2.0 `DeclarativeBase`，`schema="ops"`）：

| 表 | 用途 | 关键字段 |
|---|---|---|
| `ops_task` | 每次巡检/告警/备份/重启/发布任务 | `id`(uuid) `kind` `status`(PENDING/PENDING_CONFIRM/RUNNING/SUCCEEDED/FAILED/EXPIRED) `payload`(jsonb) `output` `created_at` `finished_at` `confirm_token` `confirm_expires_at` `requested_by_open_id` |
| `ops_audit_log` | 审计追加记录 | `id` `task_id` `actor_open_id` `action` `target` `target_sha` `confirmed_at` `result` `failed_stage` `created_at`（凭证/密钥字段一律不落，落之前先脱敏） |
| `ops_dedup_key` | 飞书事件/回调幂等 | `event_id`(唯一) `task_id` `created_at` |
| `ops_alert_state` | 告警去重与静默 | `alert_key`(唯一) `level` `last_fired_at` `consecutive_count` `resolved_at` |
| `ops_backup_record` | 备份产物与失败阶段 | `id` `object_key` `size_bytes` `started_at` `finished_at` `status` `failed_stage`（DUMP/COPY_OUT/UPLOAD） |
| `ops_import_baseline` | 导入耗时基线（观测值，供人工核对阈值） | `period_type` `batch_id` `duration_seconds` `recorded_at` |

**最小权限的取舍**：旧稿基于 RDS，给 kwops 开一个独立只读账号很自然；本次自建 PG 容器
同样支持这一点，不因为"同一个容器"就退回到共享应用主账号。新增数据库角色
`ops_bot_role`（Alembic 迁移里用 `op.execute` 建，非 ORM 覆盖范围）：对 `ops` schema
拥有全部权限（`CREATE`/`SELECT`/`INSERT`/`UPDATE`/`DELETE`），对
`analysis.import_batch`、`analysis.site_data_stats` 只授 `SELECT`（巡检要读的最小面），
**不授予任何 `analysis` schema 下其他表的权限，不授予写权限**。`ops_bot` 的数据库连接串
（`OPS_DATABASE_URL`/`OPS_DATABASE_URL_ASYNC`，新增配置项）用这个角色的凭证，与应用主
账号（`DATABASE_URL`）完全分离——`ops_bot` 进程被攻破不等于拿到业务写权限。

**调度机制**：不引入 APScheduler 等新依赖，复用 `import_queue.py` 已经在用的模式——
`main.py` 的 `lifespan` 里起若干个 `asyncio.create_task` 常驻循环，每个循环
`while True: await run_group(); await asyncio.sleep(interval)`，分组见 D3。**判据用"距上次
成功执行的时间"而非进程内存计时器**：每次循环先查 `ops_task` 里该 `kind` 最近一次
`SUCCEEDED` 的 `finished_at`，超过该分组的间隔才真正执行——这样 `ops-bot` 容器重启不会
因为内存里的下次触发时间被清零而错过或重复触发同一轮（例如 09:00 日报，重启在 09:03
发生，循环唤醒后一看"今天还没发过"，照样补发；若重启发生在 08:58，不会因为循环刚起步
就在 09:00 之前额外触发一次）。

### D2. 飞书接入细节（Issue 决策项 2）

- **应用类型**：飞书开放平台"企业自建应用"（不发布到应用商店），机器人能力 + 消息卡片
  2.0（interactive card）。
- **稳定身份 ID**：用 `open_id`（同一飞书应用下用户唯一且稳定，不随企业通讯录变动而漂移，
  与钉钉 SSO 选 `unionId` 的理由同构——参见 `dingtalk-sso` 变更"账号只按
  external_id 匹配"那条纪律）。配置项 `FEISHU_OWNER_OPEN_ID`。**不用 `user_id`**（那是
  企业内部 ID，机器人/自建应用场景下不一定能稳定拿到，且离职重新入职会变）。
- **事件订阅**：飞书事件订阅需要公网 HTTPS 回调，走 `/ops/feishu/webhook`（见 D1）。
  首次配置时飞书会发 URL 校验挑战（`challenge` 字段），`events.py` 必须先处理这个特殊
  请求（不查签名、不查白名单，只回显 `challenge`），再处理正式事件。正式事件用
  `Verification Token` + `Encrypt Key`（如启用加密）校验来源合法性——**这道校验在鉴权
  （谁在说话）之前**，校验不过直接 4xx，不进入业务逻辑。
- **鉴权边界**：事件/卡片回调通过来源校验后，还要再过一道白名单：
  `sender.sender_id.open_id == FEISHU_OWNER_OPEN_ID` 且消息/卡片所在会话
  `chat_id == FEISHU_OPS_CHAT_ID`（配置项）。两者缺一都拒绝——只查 open_id 不查
  chat_id，会允许该用户在任意会话（包括未来可能被拉进的群）里操作机器人；只查 chat_id
  不查 open_id，等同于"群里只有一个人"这种被 Issue 明确否决的判据。
- **主动通知**：用 `im/v1/messages`（发到 `FEISHU_OPS_CHAT_ID`）+
  `interactive/v1/card`（更新已发送卡片的状态，用于"任务已受理→执行中→完成"的原地刷新，
  避免刷屏）。`tenant_access_token` 换取与缓存同构
  `app/auth/dingtalk_client.py` 的企业 token 那段：进程内缓存 + `asyncio.Lock`，避免并发
  请求各换各的、互相覆盖或触发限流。
- **配置自检**：新增 `scripts/check_feishu_ops.py`（同构 `check_oss.py` /
  `check_dingtalk_sso.py`，退出码非 0 即有未通过项），依次验证：`app_id`/`app_secret` 能
  换到 `tenant_access_token`；`FEISHU_OPS_CHAT_ID` 能收到一条测试消息；
  `FEISHU_OWNER_OPEN_ID` 格式合法（`ou_` 前缀）；`OPS_DATABASE_URL` 能连通且落在
  `ops_bot_role` 权限范围内（尝试写一行 `ops_task` 再删除，尝试读
  `analysis.import_batch` 一行，尝试写 `analysis` 下任意业务表应失败——反向验证最小权限
  真的生效，不是"配了但没生效"）。

### D3. 巡检频率、阈值、导入基线、告警去重、备份时间与保留（Issue 决策项 3）

| 分组 | 间隔 | 检查项 | 判据 | 动作 |
|---|---|---|---|---|
| quick | 60s | 容器存活 | `docker inspect app/db` 的 `State.Health.Status` 或 `/health` 200 | 首次异常告警 warn；连续 3 次（3 分钟）升级 crit |
| quick | 60s | 机器人自身心跳落盘 | 更新 `ops_task` 一条 `kind=HEARTBEAT` 记录的 `finished_at`（供阿里云拨测的 `/ops/health` 端点读取此时间戳判断"最近一次循环仍在跳动"） | 无（心跳本身不产生告警，异常由外部拨测发现，见 D4） |
| full | 300s | 连接池水位 | `/health/pool` `checked_out` 逼近 `per_worker_ceiling`（阈值 90%） | warn |
| full | 300s | 导入卡死 | `import_batch` 存在 `PROCESSING`/`UPLOADING` 记录且 `updated_at` 超过对应 `period_type` 阈值仍未变化（见下） | crit，附 batch id |
| full | 300s | 日志错误率 | 近 5 分钟 ERROR/5xx 计数超阈值（`OPS_ERROR_RATE_THRESHOLD`，默认 20 次/5min） | warn，附 Top request_id |
| 15min | 900s | 磁盘 | 复用 `monitoring.py` 现有 70%/80% 口径（不新开一套阈值） | warn/crit |
| daily 03:00 | 按天判据（D1 的"距上次成功"机制） | 全量备份 | 见下 | 见 D-ops-backup |
| daily 03:30 | 同上 | alembic 漂移 | 容器内 `alembic current` == `alembic heads` | 不一致 crit（生产库结构偏离代码是重大问题） |
| daily 09:00 | 同上 | 巡检摘要 | 固定生成，不依赖异常 | 始终发送 |

**导入基线（"先观测后设阈"，不凭空断言卡死）**：初始默认值按 CLAUDE.md 记录的数据量
（日报约 30 万行、周报约 300 万行）保守估计——`OPS_IMPORT_DAY_TIMEOUT_MINUTES=60`、
`OPS_IMPORT_WEEK_TIMEOUT_MINUTES=240`。每次导入成功（`import_batch` 转 `COMPLETED`）时
`ops-bot` 巡检循环顺带记一行到 `ops_import_baseline`（`duration = updated_at - created_at`），
**不做自动阈值学习**（不过度设计——固定阈值 + 人工按基线表校准足够，观测数据只是给人看的
参考，不反过来驱动系统行为，除非未来发现两三次真实导入后阈值明显不合适才手动改
`.env`）。部署 runbook 明确要求：首次真实导入后核对 `ops_import_baseline`，据此调整两个
超时配置。

**告警去重**：`alert_key`（如 `disk-80`、`import-stuck-{batch_id}`）+ 静默期
`OPS_ALERT_SILENCE_MINUTES`（默认 30）——同 key 在静默期内不重复发送首次通知，但
`ops_alert_state.consecutive_count` 持续累加；连续 3 个周期仍处于 crit 时升级为
"强提醒"（消息里 `@` 所有者、标题加"持续"前缀）。恢复时（该 key 对应的检查转为正常）
立即发一条恢复通知，不受静默期限制，并把 `resolved_at` 写回、`consecutive_count` 归零。

**维护窗口**：`DEPLOY`/`RESTART_APP` 执行期间在 `ops_alert_state` 里对
`service-down`、`db-conn` 两个 key 打开一个 `maintenance_until`（执行开始时间 +
`OPS_MAINTENANCE_WINDOW_MINUTES`，默认 5 分钟）；巡检发现服务不可用但仍在维护窗口内时，
按 warn 而非 crit 记录且消息注明"发布/重启导致的预期短暂不可用"，窗口过后仍不可用则正常
升级告警——不能让维护窗口变成"发布失败也不告警"的黑洞。

**备份时间与保留**：沿用旧稿"低峰期"判断，定每日 03:00（Asia/Shanghai）触发一次
`scripts/backup_selfhosted_pg.py`；OSS 侧保留 `OPS_BACKUP_RETENTION_DAYS`（默认 30 天）——
`ops-bot` 每次备份成功后顺带清理超过保留期的旧对象（按 `backups/postgres/` 前缀列出，
按时间戳文件名判断，不依赖 OSS 生命周期规则，因为要在同一条审计记录里体现"清理了哪些"）。
超期阈值：距上次 `ops_backup_record.status=SUCCEEDED` 的备份 > 24h 触发 warn，> 48h 触发
crit（与巡检 full 分组同周期检查）。

**任务/审计保留**：`ops_task`/`ops_audit_log`/`ops_dedup_key` 保留 90 天（数据量远小于
业务事实表，不需要按月分区，daily 分组里加一项按 `created_at` 直接 `DELETE`）。

### D4. 外部存活探测（Issue 决策项 4，已与用户确认）

采用**阿里云云监控·站点监控（拨测）**，通知对象类型选**飞书机器人**（云监控 2026 年文档
确认原生支持钉钉/飞书/Slack/企业微信/Teams 作为通知对象，来源：
https://www.alibabacloud.com/help/zh/cms/cloudmonitor-2-0/notification-object）。这条链路
**不需要 ops-bot 自身的代码**，是阿里云控制台配置 + 部署材料，原因是它必须独立于本机——
如果告警链路依赖 ops-bot 自己转发，机器进程本身失联时这条链路同样失联，等于没做。

两个独立的拨测目标（缺一都会漏判一种失联）：

1. **业务健康**：探测 `https://<域名>/health`（现有端点），异常→通知对象=飞书机器人
   （直接配业务侧的告警，不经过 ops-bot）。
2. **机器人心跳**：探测 `https://<域名>/ops/health`（本次新增，nginx 反代到 ops-bot 的
   `127.0.0.1:8101`），该端点读取 D3 quick 分组写入的心跳记录，若超过 90 秒未更新返回
   503（而不是"进程还活着就返回 200"——要探测的是"巡检循环仍在跳动"，不是"HTTP server
   还能响应"，两者在事件循环卡死但连接未断的情况下会给出不同答案）。

这条链路解决"整机断网/停机"与"ops-bot 进程本身挂了但机器没死"两种失联，且完全不依赖
`ops-bot` 的通知能力——飞书机器人 webhook 由阿里云直接调用。

### D5. 发布必需 CI、确认有效期、导入协调锁、迁移边界（Issue 决策项 5）

**发布必需 CI 清单**：`.github/workflows/ci.yml` 当前有三个 job：`backend`、`frontend`、
`extension`。**规定目标 SHA 上这三个 job 的 GitHub Check Run 结论必须全部为
`success`**，不按"这次改动是否波及某个目录"做部分豁免——SHA 是不可变的，CI 结果是这个
提交整体的性质；引入"哪个 job 与本次相关"的判断本身就是一层容易出错的启发式，而漏判的
后果是放行一个实际上未通过 CI 的发布。用 GitHub Checks API
`GET /repos/{owner}/{repo}/commits/{sha}/check-runs`，任一 job 状态为
`in_progress`/`queued`/`action_required` → "等待中，不放行"；`failure`/`cancelled`/
`timed_out` → "失败，不放行"；找不到某个 job 的 check run → "无法核实，不放行"（不是
"没跑等于通过"）。GitHub API 调用需要 `GH_TOKEN`（新增部署材料，最小权限：仅
`checks:read`/`contents:read`）。

**不可变版本定位**：只接受完整 40 位 commit SHA（不接受分支名、不接受短 SHA——短 SHA
在提交数增长后有碰撞歧义，且"允许分支"会持续推进，同一个短前缀可能在未来某天对应到
不同提交）。发布前用 `git merge-base --is-ancestor <sha> origin/v2` 校验该 SHA 确实在
允许分支的历史里（拒绝伪造一个不在任何分支历史上的 SHA）。

**确认有效期与重放防护**：确认卡片生成时在 `ops_task` 写一行
`status=PENDING_CONFIRM`、`confirm_token`（uuid4）、`confirm_expires_at`（创建时间 +
`OPS_CONFIRM_TTL_MINUTES`，默认 10 分钟），卡片按钮的 `value` 携带
`{task_id, confirm_token}`。点击回调时：

1. 查 `ops_task` 该 `task_id`，`confirm_token` 不匹配 → 拒绝（防止已失效卡片的按钮被
   重新点出旧 token）。
2. `status != PENDING_CONFIRM` → 拒绝并回"该任务已处理或已过期"（覆盖重复点击、以及
   已经因过期被巡检循环标记为 `EXPIRED` 的情况）。
3. `now() > confirm_expires_at` → 原地把 `status` 置 `EXPIRED`，拒绝并提示"已过期，请
   重新发起"。
4. 三项都通过后，**不直接执行**——先把 `status` 置 `CONFIRMED`（乐观锁：
   `UPDATE ... WHERE id=:id AND status='PENDING_CONFIRM' RETURNING id`，`rowcount=0` 说明
   有并发请求抢先处理过，直接返回"已处理"，避免同一张卡片被双击时执行两次），再进入
   D5 下一段"执行前重新核实"。

**导入协调锁机制**（检查与执行之间的竞态，不是"查一下"这种 TOCTOU）：新增一把独立咨询锁
`_OPS_ACTION_LOCK_KEY = 0x4B4F`（与现有导入锁 `_IMPORT_LOCK_KEY = 0x4B49`
[`app/table/upload/import_service.py`] 不同的 key，避免撞车）。协调分两层：

1. **确认卡片生成前**（轻量前置检查，不加锁）：查询 `import_batch` 是否存在
   `PROCESSING`/`UPLOADING` 记录，存在则直接拒绝生成确认卡片，回"导入进行中，暂不可
   发起重启/发布"。这一步只是快速拦截明显场景，不承诺并发安全。
2. **人工确认后、真正执行前**（唯一有强保证的一步）：`executor` 尝试
   `pg_try_advisory_lock(_OPS_ACTION_LOCK_KEY)`；同时修改
   `app/table/upload/import_queue.py` 的认领查询（认领 `QUEUED`/`UPLOADING` 批次前）
   加一次 `pg_try_advisory_lock(_OPS_ACTION_LOCK_KEY)` 探测——**抢到就立刻释放**（只是
   探测这把锁当前是否被 `ops-bot` 持有，不是要长期占它），抢不到（说明 `ops-bot`
   正握着）则本轮跳过认领，等下一轮轮询。`ops-bot` 执行动作期间持有这把锁直到动作结束
   （成功/失败都释放）。这样"确认期间来了个新导入"的场景里，谁先真正抢到锁谁执行，
   另一方在下一轮重试——不会出现"重启把正在跑的导入腰斩"或"导入在重启的一瞬间插入"这种
   交叉写。`ops-bot` 执行前若抢不到这把锁（导入已经抢先拿到），直接判定为"导入进行中，
   中止执行"并把任务标记失败、说明原因，**不做二次重试或强行抢占**——人工确认后的动作
   若因竞态未执行，通知使用者需要重新发起，而不是静默重试到不可预期的时间点。

**迁移与回滚边界**：发布计划生成确认卡片时，比较目标 SHA 对应代码里的 alembic heads 与
当前生产库 `alembic current` 是否一致，不一致则卡片标注"本次发布包含数据库迁移"；执行时
`deploy.sh` 按需带 `--migrate`（默认 `--skip-migrate`），**迁移确认闸门保留**——
`deploy.sh` 内 `read -r -p` 的交互确认在这条路径下会阻塞住 `subprocess`，因此
`ops-bot` 调用带迁移的发布时必须传 `--yes`（否则会死等一个不存在的 TTY 输入），这意味着
"迁移是否要跑"这个决策点已经上移到飞书确认卡片本身——卡片没有展示迁移信息就等于用户没有
被告知，**这条路径不能既跳过卡片说明又跑迁移**。回滚（发布目标 SHA 早于当前）额外检查：
若目标 SHA 代码里的 alembic heads 落后于当前生产库 `alembic current`（即回滚会让代码期望
的 schema 版本早于库里实际版本），直接拒绝执行并在卡片/结果里说明"目标版本落后于当前数据库
结构，不支持自动降级数据库，需人工评估后手动处理"——不尝试自动 `alembic downgrade`。

### D6. 部署材料清单（Issue 决策项 6）

不进代码仓库，作为 `runbook` 的一部分列出、部署时由用户提供并直接写入服务器 `.env`：

| 材料 | 用途 |
|---|---|
| 飞书自建应用 `App ID` / `App Secret` | 换取 `tenant_access_token` |
| 飞书应用 `Verification Token` / `Encrypt Key`（如启用加密） | 校验事件订阅回调来源 |
| 本人飞书 `open_id`（`FEISHU_OWNER_OPEN_ID`） | 鉴权白名单 |
| 运维群 `chat_id`（`FEISHU_OPS_CHAT_ID`） | 通知与交互入口 |
| `GH_TOKEN`（`checks:read`/`contents:read`） | 发布前查询 CI 状态 |
| v2 部署路径与访问方式 | 确认 `deploy.sh`/`docker-compose.yml` 的实际路径与本次设计假设一致 |
| 现有 crontab（`crontab -l` 输出） | 核实是否已有旧的备份/清理定时任务；若有，切换前需人工禁用，避免与 `ops-bot` 内置调度双跑（见风险段） |
| 阿里云云监控账号可访问 + 站点监控/通知对象配置权限 | 配置 D4 的两个拨测目标 |

## Risks / Trade-offs

- **[风险] `ops-bot` 容器崩溃重启错过或重复调度**
  → 已在 D1 用"距上次成功执行时间"而非内存计时器规避，重启后自愈补跑或跳过已完成的轮次。

- **[风险] 飞书事件订阅回调超时重试导致重复执行**
  → 事件到达先落 `ops_dedup_key`（唯一约束 `event_id`），已存在则直接返回 200 且不重新
  入队；回调处理必须在飞书的重试窗口（约 3 秒）内完成"落库 + 200 响应"，真正的巡检/
  执行逻辑放 `asyncio` 后台任务，不在请求处理协程里做。

- **[风险] 现有 crontab 可能已经在跑 `backup_selfhosted_pg.py`，与 `ops-bot` 内置调度双跑**
  → 本设计明确假设"不确定生产是否已有 cron"（仓库里找不到已部署的 crontab 配置，属于
  宿主机层面、不受版本控制）。切换为 `ops-bot` 内置调度前，D6 部署材料清单要求核实
  `crontab -l`；若已有，runbook 要求先 `crontab -e` 移除旧条目再上线 `ops-bot`，这一步
  是部署操作，不是本次代码能自动处理的（应用代码不能远程改宿主机 crontab）。

- **[风险] 双向咨询锁需要同时改 `ops-bot` 与 `import_queue.py` 两处代码，改漏一处会让
  这把锁形同虚设**
  → `import_queue.py` 的改动是本变更范围内的既有文件改动（proposal.md 已列出），tasks.md
  必须包含针对这个交互的集成测试（模拟"导入认领中途 ops-bot 抢锁""ops-bot 执行中途导入
  抢锁"两种时序，断言互斥生效）。

- **[风险] `GH_TOKEN` 若权限范围给大了（比如误给 `repo` 全权限），`ops-bot` 被攻破的
  代价上升**
  → D6 材料清单明确写最小权限范围（`checks:read`/`contents:read`），`check_feishu_ops.py`
  自检脚本增加一项"用该 token 尝试一次写操作应失败"的反向验证（同构 D2 对数据库角色的
  反向验证思路）。

- **[风险] 阿里云云监控拨测到通知对象=飞书机器人这条链路，本设计未曾在真实环境验证过
  格式细节（Webhook payload 是否需要额外签名、消息卡片渲染效果）**
  → 这是本设计基于官方文档做出的判断，不是已验证的事实。tasks.md 必须包含一条"部署时
  用真实阿里云账号配置一次并触发一次测试告警，核对飞书群实际收到的消息格式"，且在验收
  清单里明确标注为"依赖生产/云账号，无法在实现阶段自动验证"。

- **[风险] `deploy.sh` 现有的 alembic 交互确认（`read -r -p`）在 `ops-bot` 以
  `subprocess` 非交互方式调用时若忘记传 `--yes`，会挂起直到 300 秒执行器超时**
  → D5 已经把"迁移与否"上移到飞书确认卡片本身决定，执行器按卡片决定的 `with_migration`
  显式传 `--migrate --yes` 或 `--skip-migrate`，不存在"忘记传"的路径（类型是布尔字段，
  不是字符串拼接）。超时兜底仍保留，作为其他未知失败模式的防线。

## Migration Plan

1. **Alembic 迁移（新增，不改动 `analysis` schema）**：创建 `ops` schema、`ops_bot_role`
   角色及其权限授予（`op.execute` 原生 SQL，非 ORM 可表达）、`ops_task` /
   `ops_audit_log` / `ops_dedup_key` / `ops_alert_state` / `ops_backup_record` /
   `ops_import_baseline` 六张表（`DeclarativeBase`，`schema="ops"`）。这条迁移仍然走
   `docker compose run --rm app alembic upgrade head`（与业务迁移同一条流水线，`ops_bot`
   本身不跑迁移，避免多一个迁移入口）。
2. **代码**：新增 `ops_bot/` 包、`docker-compose.yml` 新增 `ops-bot` 服务、nginx 配置新增
   `/ops/feishu/webhook` 与 `/ops/health` 反代、`.env.example` 新增全部 `OPS_*`/
   `FEISHU_*` 配置项（含安全的本地默认值/留空）、`scripts/check_feishu_ops.py`。
3. **部署顺序**：`db` 迁移先行 → 部署 `ops-bot`（此时飞书应用尚未配置，服务能起但收不到
   事件）→ 用户在飞书开放平台完成 D6 材料的配置与事件订阅登记 →
   `uv run python scripts/check_feishu_ops.py` 全绿 → 阿里云云监控拨测配置 → 演练验证
   （见 tasks.md 的演练场景）。
4. **回滚**：`ops-bot` 是新增的独立服务，回滚即 `docker compose stop ops-bot` +
   `alembic downgrade` 到迁移前版本（`ops` schema 整体 `DROP`，不影响 `analysis`）——
   业务系统不依赖 `ops-bot` 存在与否，回滚零风险。

## Open Questions

- 飞书卡片的具体消息模板（字段排布、按钮文案）留给 tasks.md 实现阶段按飞书卡片 2.0
  JSON 结构逐个撰写，不在此穷举——这是实现细节而非架构决策。
- `check-runs` API 对同一 SHA 若存在历史上多次触发（如重跑 CI）产生的多条同名 job 记录，
  取值策略（取最新一条还是要求全部为 success）留待 tasks.md 实现时读 GitHub API 实际
  返回结构后确定，此处只确定"三个 job 必须全部通过"这条硬性要求。
