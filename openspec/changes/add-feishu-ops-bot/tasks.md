> **认领记录**：2026-09-16，Claude Code 会话（worktree `~/.claude-worktrees/add-feishu-ops-bot`，
> 分支 `change/add-feishu-ops-bot`）。proposal/design/specs 四件套已生成并通过
> `openspec validate`（首次提交 `5c5a1b9`）。第二次提交推进了第 1 组全部、第 3 组
> 3.1-3.3/3.7 的一部分——见下方逐项标注，**未勾选的任务仍待完成**，下一轮会话从
> 2.3（补 checks/executor/scheduler/service/schemas/api_internal）与 2.4/2.5
> （docker-compose 服务、nginx 反代）接着做。
>
> **实现期发现的一处偏离原设计**：`test_conventions.py::test_no_new_dead_settings`
> 要求 `config.py` 里声明的每个字段都有真实消费方，且扫描范围只覆盖 `app/`/`scripts/`/
> 顶层几个文件（现已加入 `ops_bot`）。据此调整了两点，design.md 未来若被引用需注意：
> （1）阈值类配置（`GH_TOKEN`/`OPS_ALERT_SILENCE_MINUTES` 等）**不再一次性加全**，
> 改为随各自消费方所在的任务组增量加入；（2）`OPS_DB_ROLE_PASSWORD` 改为不进
> `config.Settings`，直接在迁移里读 `os.environ`（同构 `PG_MAX_CONNECTIONS`/
> `POSTGRES_DB` 的既有先例）——它只有迁移这一个读取方，路由进 Settings 反而会被
> 判定为"声明了但过度暴露"。`ops_bot/settings.py` 也被去掉，`ops_bot/*` 直接
> `from config import settings`，与业务代码同一份配置来源，未新建独立文件。

## 0. 实现前置说明

**验收条件依赖真实环境的标注约定**：下面每个任务组末尾若有标 `[生产/飞书实机]` 的任务，
表示该任务需要真实飞书应用、真实阿里云账号或生产服务器才能完成验证，本仓库内无法自动化
验证；这类任务在实现阶段只能产出可执行的演练脚本/文档，由用户在部署后手动跑一遍并确认，
**不得在没有跑过的情况下宣称验收通过**。

## 1. 数据库底座：`ops` schema、角色、迁移

- [x] 1.1 新建 Alembic 迁移（`d4e5f6a7b8c9`）：`CREATE SCHEMA ops`、创建
      `ops_bot_role` 角色并授予 `ops` schema 全部权限、对 `analysis.import_batch`
      与 `analysis.site_data_stats` 仅授 `SELECT`；用 `op.execute` 写原生 DDL/DCL。
- [x] 1.2 定义 `ops_bot/model.py`：`OpsTask`/`OpsAuditLog`/`OpsDedupKey`/
      `OpsAlertState`/`OpsBackupRecord`/`OpsImportBaseline` 六个 SQLAlchemy 2.0
      `DeclarativeBase` 模型（独立 `OpsBase`，见 `ops_bot/database.py`），
      `schema="ops"`，字段按 design.md D1 表格。
- [x] 1.3 同一份迁移建出上述六张表；`uv run alembic upgrade head --sql` 与
      `downgrade d4e5f6a7b8c9:71e646b2d200 --sql` 均已核对生成的 DDL 落在 `ops` schema
      且回滚路径完整。
- [ ] 1.4 测试：**部分完成**——`test/test_ops_bot_crud_integration.py` 覆盖了
      `ops_bot_role` 对 `ops.ops_dedup_key` 的幂等读写（需 `TEST_DATABASE_URL`，
      本会话未接入真实测试库运行，只做了代码走查）。**尚未写**"`ops_bot_role` 对
      业务表写操作被拒绝"这条断言——留给 local-db 验证阶段一并补。

## 2. 配置与项目骨架

- [x] 2.1 `config.py` 的 `Settings` 新增 `OPS_DATABASE_URL`/`OPS_DATABASE_URL_ASYNC`/
      `FEISHU_APP_ID`/`FEISHU_APP_SECRET`/`FEISHU_VERIFICATION_TOKEN`/
      `FEISHU_ENCRYPT_KEY`/`FEISHU_OWNER_OPEN_ID`/`FEISHU_OPS_CHAT_ID`。
      **偏离原计划**：`GH_TOKEN`/`OPS_ALERT_SILENCE_MINUTES`/
      `OPS_IMPORT_DAY_TIMEOUT_MINUTES`/`OPS_IMPORT_WEEK_TIMEOUT_MINUTES`/
      `OPS_ERROR_RATE_THRESHOLD`/`OPS_BACKUP_RETENTION_DAYS`/`OPS_CONFIRM_TTL_MINUTES`/
      `OPS_MAINTENANCE_WINDOW_MINUTES` 未加入——`test_no_new_dead_settings` 不允许
      声明无消费方的字段，这些留给第 4/5/6 组各自任务加入。`OPS_DB_ROLE_PASSWORD`
      未进 `Settings`（见上方认领记录的偏离说明）。
- [x] 2.2 同步更新 `.env.example`，范围与 2.1 一致（含 `OPS_DB_ROLE_PASSWORD` 作为
      纯环境变量的文档行，不对应 Settings 字段）。
- [x] 2.3 `ops_bot/` 包骨架建好：`main.py`/`database.py`/`model.py`/`crud.py`/
      `scheduler.py`/`checks/`（`heartbeat.py`）/`executor/`/
      `feishu/{client,auth,cards}.py`。**与原计划的差异**：未拆出独立
      `feishu/events.py`（URL 校验/分发职责直接写在 `main.py` 里，事件类型变多、
      main.py 变臃肿时再拆）；未建 `service.py`/`schemas.py`/`api_internal.py`
      （当前逻辑量不足以证明这层拆分的必要性，`crud.py`/`scheduler.py`/`main.py`
      已经够用，等第 4-6 组接入更多业务逻辑后视情况再拆）；未新建
      `ops_bot/settings.py`——`ops_bot/*` 直接 `from config import settings`。
- [x] 2.4 **修订**（design.md D1 实现期修订，不再是 docker-compose 服务）：新增
      `etc/keywordiq-ops-bot.service`（host systemd unit）；`docker-compose.yml`
      的 `db` 服务新增 `"127.0.0.1:5432:5432"` 端口映射供 host 进程连接。
- [x] 2.5 `etc/keywordiq-nginx.conf` 新增 `upstream keywordiq_ops_bot`（127.0.0.1:8101）
      与 `location /ops/`，覆盖 `/ops/feishu/webhook` 与 `/ops/health`。

## 3. 飞书接入底座（`ops-bot-platform`）

- [x] 3.1 `feishu/client.py`：`tenant_access_token` 换取、进程内缓存 + `asyncio.Lock`
      防并发换取（同构 `app/auth/dingtalk_client.py` 的企业 token 段）；额外实现了
      `send_text`/`send_card`/`update_card`（原任务未列出，但 3.3/3.4 需要它）。
- [x] 3.2（部分）`feishu/auth.py`：Verification Token 校验（含 URL 校验挑战路径）、
      Encrypt Key 场景的 AES-256-CBC 解密、`open_id`/`chat_id` 白名单双重校验均已
      实现。**未拆出独立 `feishu/events.py`**——见 2.3 说明；卡片回调（interactive
      card action，与消息事件走不同回调机制）尚未接入，只接了消息事件一种，回调
      分发留给第 6 组（发布/重启确认要用到卡片回调）一起接入更合理。
- [x] 3.3（部分）`ops_bot/main.py`：`/ops/feishu/webhook` 端点完成 URL 校验挑战、
      来源校验、幂等判定（`OpsDedupKey`）、身份白名单、并对已授权事件回一条最小
      文本确认；`/ops/health` 已接入 3.6 的心跳判定（详见 3.6）。**与原任务描述的
      一处差异**：未接"丢 `asyncio.create_task` 后台执行"——当前唯一的动作是发一条
      同步文本回执，没有真正的长任务需要后台化，等第 6 组把重启/发布接进卡片回调
      （那才是真正耗时、需要后台化的动作）时再补。
- [x] 3.4 `feishu/cards.py`：状态摘要卡片、确认卡片（`build_confirm_card`，双按钮
      携带同一个 `task_id`/`confirm_token`，`decision` 字段区分确认/取消）、结果
      卡片（`build_result_card`）均已实现，强类型入参。卡片 JSON 结构依据飞书官方
      文档判断，**未经真实客户端渲染验证**——留给 3.8/演练阶段。
- [x] 3.5 `executor/`：`OpsAction` 枚举 + `RESTART_APP`/`RELOAD_NGINX`/`BACKUP_NOW`/
      `DEPLOY` 四个固定 argv 执行函数；`DEPLOY` 超时放宽到 1800 秒（镜像/前端构建是
      分钟级操作，通用的 300 秒对它不够，其余三个动作仍是 300 秒）。输出截断到 8KB。
- [x] 3.6 `scheduler.py`：通用的常驻循环机制（`ScheduledJob`/`start`/`stop`），判据
      用"距上次成功执行时间"（查 `ops_task`）。当前只注册了 quick 分组的
      `HEARTBEAT` 任务（`checks/heartbeat.py`，60s 间隔）；full/15min/daily 分组
      的具体检查函数随第 4 组接入。`/ops/health` 已读心跳新鲜度，超过 90 秒
      （60s 间隔 + 30s 余量）返回 503。
- [x] 3.7（部分）测试：`test/test_ops_bot_feishu_unit.py`（token 缓存/并发、
      verification token 校验、AES 加解密自洽性、`is_authorized` 白名单）、
      `test/test_ops_bot_webhook_unit.py`（URL 校验挑战、403 路径、心跳新鲜度
      三态含 503，全部打桩不触库）、`test/test_ops_bot_executor_unit.py`（四个
      动作的 argv、失败/超时/多步骤中断）、`test/test_ops_bot_cards_unit.py`、
      `test/test_ops_bot_scheduler_unit.py`（判据/成功失败记录/取消，全部打桩）、
      `test/test_ops_bot_crud_integration.py`（幂等去重，需 `TEST_DATABASE_URL`，
      本会话确认能在真实 `keywordiq_test` 库上正确 skip——库未迁移到 ops schema
      时优雅跳过而非报错，真正的读写断言仍待 local-db 迁移后验证）。**未覆盖**：
      已授权事件完整路径的端到端测试（依赖真实 DB，webhook 单元测试特意不触库）。
- [x] 3.8 `[生产/飞书实机]` 新建 `scripts/check_feishu_ops.py`（自检脚本，design.md
      D2 列出的四项校验，含对"最小权限"的反向验证）。本地用假凭证实测过"预期失败"
      路径（配置错误时逐项打印 ❌ 且不崩溃退出），**修正了一处实现漏洞**：数据库
      连接失败时反向验证（"写 analysis.import_batch 应被拒绝"）曾把"连不上库"误判
      成"权限边界生效"打印 ✅——现已改为先单独确认连通性，连不上直接失败并跳过
      后续子检查。真实凭证的验证留给部署阶段。

## 4. 巡检检查器与告警（`ops-monitoring-alerting`）

- [x] 4.1 `checks/`：`heartbeat.py`（quick，已在第 3 组建）、`service.py`（app 健康
      +连接池水位，full）、`import_status.py`（导入卡死检测，full）、`logs.py`
      （近 5 分钟错误/5xx 计数，full）、`resources.py`（磁盘，15min，阈值
      70%/80%）、`alembic_drift.py`（daily）。**未做**"容器存活"这项原计划里的
      检查——host 级服务改用更直接的"打 `/health` 端点"而非 `docker inspect`，
      两者目的相同（服务是否活着），`/health` 同时探活了双数据库引擎，信息量
      更大，不需要额外再查一次容器状态。
- [x] 4.2 告警去重与升级：`ops_bot/alerts.py` 的 `evaluate()` 状态机，`OpsAlertState`
      读写、静默期判定、连续 3 周期升级"强提醒"、恢复通知不受静默期限制，均已实现
      并有状态转移单元测试覆盖每条分支。
- [x] 4.3（部分）维护窗口机制：`alerts.open_maintenance_window()` 已实现且巡检据此
      降级告警等级（单元测试覆盖）。**尚未被任何调用方触发**——"重启/发布执行开始时
      打开窗口"要等第 6 组把重启/发布执行路径接进来才有调用点，机制本身先就位。
      另外：**用的告警键是 `service-down`，没有单独的 `db-conn` 键**——`/health`
      本身双引擎都探，`service-down` 已经覆盖"数据库连不上导致服务不健康"这个
      场景，两个键会是同一件事的两种说法。
- [x] 4.4 导入完成/失败通知：`ops_bot/jobs.py` 的 `import_notify` 任务，用
      `crud.last_succeeded_at(session, "IMPORT_NOTIFY")` 作为"上次检查到哪"的
      水位线（复用 3.6 已有的"距上次成功"机制，不新增专门的状态列）。
- [x] 4.5 导入基线记录：`import_status.record_baseline()`，在 `import_notify` 任务里
      对 `COMPLETED` 批次调用，日报/周报各自独立超时阈值在 `import_status.
      find_stuck_batches()` 里按 `period_type` 分别取值判定卡死。
- [x] 4.6 每日 09:00 巡检摘要：`ops_bot/daily_summary.py` + `scheduler.daily_at(9, 0)`
      的日历语义判据（见 3.6 的 `should_run` 扩展），幂等性由 `should_run` 本身保证
      （"今天是否已成功过"直接查 `ops_task`，不需要另外判断"当天是否已发送"）。
- [x] 4.7 状态/任务/日志查询指令：`ops_bot/commands.py`，飞书消息文本
      "状态"/"任务"/"日志 <request_id>" 三个指令，接入 `main.py` 的 webhook 消息
      处理（替换掉此前占位的"已收到，功能开发中"）。日志查询复用本组新建的
      `checks/logs.py`（不是 `add-request-diagnostics-logging` 的现成查询接口——
      那次变更只补了日志内容与耗时字段，没有建对外查询 API，"复用"准确说是复用
      它定的日志格式去解析文件，不是复用一个已有接口）。
- [x] 4.8 测试：`test_ops_bot_alerts_unit.py`（去重/升级/恢复/维护窗口降级，7 个场景）、
      `test_ops_bot_import_status_unit.py`（含日报/周报阈值分别生效、无基准点不误判）、
      `test_ops_bot_logs_unit.py`（含 INFO 级 5xx 计入、ERROR 级不重复计数）、
      `test_ops_bot_service_check_unit.py`、`test_ops_bot_alembic_drift_unit.py`、
      `test_ops_bot_daily_summary_unit.py`、`test_ops_bot_commands_unit.py`
      （commands 测试过程中抓到一个真实 bug：`"日志 "` 尾随空格被 `.strip()` 削掉后
      `startswith("日志 ")` 失配，指令被误判成"不认识"——已改成"指令头+剩余参数"
      两段式解析并修复）。**未覆盖**"09:00 摘要幂等——模拟重启场景"这条端到端
      集成测试（`daily_at` 的日历语义已有单元测试，但"服务真重启一次仍不重发"这个
      跨进程场景需要真实 DB + 真实调度循环，留给 local-db 阶段）。
- [ ] 4.9 `[生产/飞书实机]` 阿里云云监控·站点监控配置：业务 `/health` 与
      `/ops/health` 两个拨测目标，通知对象选飞书机器人；实测触发一次告警，核对飞书群
      实际收到的消息格式与内容。
- [ ] 4.10 `[生产/飞书实机]` 整机失联演练：隔离/受控关闭网络（或模拟停机），验证外部
      拨测独立于本机发出告警；同时验证仅 `ops-bot` 进程被杀（机器本身正常）时
      `/ops/health` 能被拨测判定为异常。

## 5. 备份（`ops-backup`）

- [x] 5.1 `ops_bot/backup.py` 封装调用 `scripts/backup_selfhosted_pg.py`（`executor.
      backup_now()`，子进程调用不重写备份逻辑），解析该脚本已有的进度打印
      （`_parse_result`）反推 `OpsBackupRecord` 需要的产物标识/大小/失败阶段
      （DUMP/COPY_OUT/UPLOAD）——**没有改动共享脚本本身**，代价是脚本文案变了这里
      的正则要跟着改，已用脚本当前真实文案样例写测试兜底这条风险。
- [x] 5.2 定时触发（daily 分组，`scheduler.daily_at(3, 0)`）+ 飞书手动触发指令
      （`ops_bot/main.py` 里"备份"/"backup" 两个词直接触发，**不经确认卡片**——
      design.md D5 只要求重启/发布走确认卡片，备份未被点名，且备份本身可重复、
      不影响正在提供服务的应用，风险量级与前两者不同，判定为不需要）。触发后
      立即回"已受理"，实际执行放 `asyncio` 后台任务（`_run_manual_backup`），完成
      后单独发一条结果消息，与定时触发共用同一个 `backup.run_backup()`。
- [x] 5.3 备份超期检查（24h warn / 48h crit）：`ops_bot/jobs.py` 的
      `backup_overdue_check`，full 分组，接入 `alerts.evaluate`。
- [x] 5.4 保留期清理：`backup.cleanup_old_backups()`，`run_backup()` 成功后自动调用，
      清理摘要写进本次 `ops_task.output`（可追溯"清理了哪些"）。
- [x] 5.5 查询最近备份指令：`ops_bot/commands.py` 的"备份记录"，返回时间/产物标识/
      大小（`crud.latest_succeeded_backup`）。
- [x] 5.6 测试：`test_ops_bot_backup_unit.py`（`_parse_result` 用脚本四种真实文案
      样例覆盖成功/DUMP失败/COPY_OUT失败/UPLOAD失败；`run_backup` 失败时必须先落
      记录再抛异常）、`test_ops_bot_commands_unit.py`（"备份记录"指令两种结果）、
      `test_ops_bot_webhook_unit.py::TestRunManualBackup`（手动触发成功/失败两条
      路径分别正确落 `ops_task`）。**未覆盖**"保留期清理只删超期对象"——
      `cleanup_old_backups` 直接调用真实 `oss2` API，没有打桩测试，只能靠 5.8 的
      生产实测验证。
- [ ] 5.7 `[生产/飞书实机]` 部署前核实生产是否已有独立 crontab 在跑
      `backup_selfhosted_pg.py`（`crontab -l`），若有则先禁用旧任务再启用本系统内置
      调度，避免双跑；在 runbook 中写明这一步骤。
- [ ] 5.8 `[生产/飞书实机]` 用真实 OSS 桶跑一次手动备份，核对产物；按脚本文档已验证过
      的隔离环境恢复步骤，实测一次恢复演练并留存证据。

## 6. 重启与发布确认（`ops-release-control`）

- [x] 6.1（部分）重启动作：`RESTART_APP` 已接入完整确认流程
      （`release/flow.py::build_restart_plan`），确认卡片生成前先查
      `import_batch` 快速拦截（`_import_active_or_unknown`，design.md D5 第一层）。
      **`RELOAD_NGINX` 只在 `executor.py` 里实现并测试了执行原语，没有接独立的
      聊天触发入口**——Issue 与 design.md D5 通篇只讨论"重启""发布"两个动作需要
      确认流程，没有点名 `RELOAD_NGINX` 要单独可从飞书触发；它目前只会作为
      `deploy.sh` 内部流程的一部分间接生效，判定为本次范围内合理的取舍而非遗漏。
- [x] 6.2 发布 CI 校验：`release/ci.py`，调用 GitHub Checks API 核实目标 SHA 的
      `backend`/`frontend`/`extension` 三个 job 结论全部 `success`；等待中/失败/
      找不到/查询本身失败均判定为不放行（`GH_TOKEN` 未配置同样不放行）。同名 job
      因重跑产生多条记录时取 `started_at` 最新的一条。
- [x] 6.3 SHA 校验：`release/git_ref.py` 的 `is_valid_sha_format`（只接受完整 40 位
      小写十六进制）+ `is_ancestor_of_branch`（`git fetch` 后
      `git merge-base --is-ancestor`）。
- [x] 6.4 发布确认卡片：当前 SHA（`git_ref.current_sha`）、目标 SHA、变更摘要
      （`git_ref.change_summary`，超过 10 行截断并注明总条数）、CI 结果、迁移标注
      （`git_ref.migrations_changed`）均已展示；"预期影响"没有单独成行，隐含在
      "数据库迁移：是/否"与变更摘要里，没有另外生成一段自由文本描述。
- [x] 6.5 确认状态机：`crud.create_pending_confirm_task` /
      `crud.try_confirm_task`（乐观锁 `UPDATE ... WHERE status=PENDING_CONFIRM`）/
      `crud.mark_task_expired`，`release/flow.py::handle_confirm_callback` 编排
      "未找到/token 不匹配→已失效"、"状态不是 PENDING_CONFIRM→已处理或已过期"、
      "已过期→标记 EXPIRED"、"取消→标记 FAILED"、"并发确认竞态→乐观锁兜底" 五条
      分支，全部有单元测试。
- [x] 6.6 双向咨询锁：`ops_bot/release/lock.py` 的 `OPS_ACTION_LOCK_KEY = 0x4B4F`
      与 `app/table/upload/import_service.py` 新增的 `_OPS_ACTION_LOCK_KEY = 0x4B4F`
      （同值，两边独立定义）；`import_service.drain_import_queue` 在查到排队批次、
      抢导入锁之前先探测这把锁（`_ops_action_in_progress`，抢到即放，只做"此刻是否
      被占用"的判断），占用中则本轮跳过认领。
- [x] 6.7 执行前重新核实：`_execute_confirmed` 重新查导入空闲状态（失败判定为"不
      空闲"）、`DEPLOY` 额外重新查 CI 与 SHA 归属，均不复用 `build_deploy_plan` /
      确认时的快照；执行前还要真正抢到 `lock.try_acquire()` 才会继续。
- [x] 6.8 非交互调用 `deploy.sh`：`executor.deploy()` 按 `DeployPlan.with_migration`
      显式传 `--migrate` 缺省或 `--skip-migrate`，固定带 `--yes`（布尔字段驱动，
      不做字符串拼接）；`DEPLOY` 超时放宽到 1800 秒（构建镜像+前端是分钟级操作，
      通用 300 秒对它不够，其余三个动作仍是 300 秒）；`_run_steps` 按步骤捕获
      阶段化失败信息（`GIT_FETCH`/`GIT_CHECKOUT`/`DEPLOY_SH`，任一步失败立即停止
      不继续跑后续步骤）。
- [x] 6.9 互斥：`lock.try_acquire()` 就是互斥本身（同一时刻只有一个执行能持有
      `OPS_ACTION_LOCK_KEY`），抢不到时明确提示"另一个重启/发布任务正在执行中"
      ——**单元测试过程中抓到一个真实 bug**：最初的失败提示写成了"导入进行中"，
      但实际上导入侧对这把锁只做"探测后立即释放"、从不长期持有，抢不到锁唯一的
      成因就是另一个重启/发布正在跑，已改正措辞（详见 `flow.py` 里的注释与
      `test_lock_not_acquired_aborts` 的说明）。
- [x] 6.10 回滚边界：`git_ref.rollback_needs_review`（目标 SHA 是当前 HEAD 的祖先
      且迁移目录内容不同）命中时 `build_deploy_plan` 直接拒绝生成确认卡片，不
      尝试自动降级数据库。**不是对 alembic heads 图的精确比较**——精确判断要在
      目标 SHA 的树上重建 down_revision 图，这里用"迁移目录内容是否相同"当代理，
      偏保守（宁可多问一次人工，不能漏判），已在函数文档与两处单元测试里写清楚。
- [x] 6.11 测试：`test_ops_bot_release_flow_unit.py`（22 个用例，覆盖确认状态机
      五条分支、`build_restart_plan`/`build_deploy_plan` 的每一层拒绝判据、执行前
      重新核实、互斥、RESTART_APP/DEPLOY 两种执行路径）、`test_ops_bot_ci_check_unit.py`
      （7 个用例，含同名 job 重跑取最新、未配置 token 拒绝、未找到 job 不算通过）、
      `test_ops_bot_git_ref_unit.py`（20 个用例，含 SHA 格式/祖先关系/迁移变化/
      回滚边界/当前 SHA/变更摘要截断）。
- [x] 6.12（部分）集成测试：`test_ops_bot_lock_coordination_integration.py` 验证
      "ops-bot 持有锁时导入侧探测到进行中"与"退出上下文后锁真正释放，不留孤儿锁"，
      需 `TEST_DATABASE_URL` 且 `ops_bot_role` 已迁移到位——本会话确认能在真实
      `keywordiq_test` 库上正确 skip（库未迁移），真正的读写断言仍待 local-db
      迁移后验证。**未覆盖**"确认后、执行前导入抢先获得咨询锁"与"导入认领中途
      运维动作抢锁"这两种交叉时序的完整场景（design.md 风险段点名的那两条）——
      现有集成测试只验证了锁本身跨角色可见，没有驱动两个真实并发协程互相竞争，
      留给 local-db 阶段用真实 asyncio 并发补齐。
- [ ] 6.13 `[生产/飞书实机]` 受控完成一次真实确认发布：从生成确认卡片、人工点击确认、
      执行、到发布后验证，全流程走一遍；额外构造一次部署失败/健康检查失败场景，确认
      不误报成功且报告正确的失败阶段。

## 7. 权限、审计收尾

- [x] 7.1 审计记录写入：`crud.write_audit_log` + `release/flow.py::_execute_confirmed`
      （重启/发布，含 `confirmed_at`）、`backup.run_backup`（备份，`confirmed_at`
      恒为 `None`——备份不走确认流程，不是漏填）均已接入，记录操作者/动作/目标/
      SHA/结果/失败阶段。**"脱敏"基本不适用**：`OpsAuditLog` 表结构本身没有自由
      文本输出字段，凭证/密钥这类敏感内容原本就没有地方可落；风险面在
      `ops_task.output`（自由文本，已有截断，未做敏感词过滤）——这是既有边界，
      未在本组新增处理。
- [x] 7.2 `crud.prune_old_task_records`（90 天，`_TASK_RETENTION_DAYS`），接入
      `jobs.py` 的 `PRUNE_TASK_RECORDS` daily 任务（`daily_at(4, 0)`）。
- [x] 7.3（超出原计划范围）通知失败与执行失败分开记录：**单元测试过程中发现两处
      真实缺口并修复**——(1) `daily_summary.send()` 曾在计算完摘要后不设防地调用
      `send_card`，卡片发送失败会让"算摘要"这件本已成功的事被记成任务失败；
      (2) `jobs.py` 的 `import_notify` 曾在 for 循环里裸调用 `send_text`，单条
      通知发送失败会中途抛出异常，导致**已经算好但还没提交的导入基线记录随事务
      回滚一起丢失**——这比"少发一条通知"严重得多。两处均已改为捕获发送异常、
      记录到任务输出文本、不影响任务整体成功判定。`main.py` 的指令分发链路另外
      加了一层兜底 try/except，防止数据库不可达之类的未预期异常让事件在标记"已
      处理"后却从未得到任何回复。"通知重试不重新触发已执行动作"这条本就成立：
      重启/发布走的是确认卡片这套独立状态机，与消息回复的发送/重试机制完全无
      交集，没有专门测试这个交叉场景（因为它们本来就没有交叉点）。
- [x] 7.4 业务应用/数据库异常时的宿主机级诊断：`service.is_app_healthy()`（HTTP
      探测）与 `resources.disk_usage_percent()`（`psutil` 直读）均不依赖数据库；
      `/ops/health` 新增 `db_unreachable` 分支，`ops`/`analysis` 两个 schema
      同处一个自建 PG 实例，实例整体不可达时返回成形的 503 而非裸 500。
- [x] 7.5 测试：`test_ops_bot_jobs_unit.py`（`import_notify` 的通知失败隔离，2 个
      用例）、`test_ops_bot_webhook_unit.py` 新增
      `test_database_unreachable_returns_503_not_500`、
      `TestDispatchExceptionFallback`（指令链路未预期异常兜底）。**未覆盖**：
      `crud.write_audit_log`/`crud.prune_old_task_records` 的独立单元测试——两者
      是薄 SQL 组装，已通过调用方（`flow.py`/`backup.py`/`jobs.py`）的测试间接
      验证"以正确参数被调用"，真正的 SQL 正确性（保留边界是否精确、审计行字段
      是否如实落库）留给 local-db 阶段验证。

## 8. 文档与部署材料

- [x] 8.1（修订落点）编写 runbook：`etc/keywordiq-ops-bot-runbook.md`，覆盖安装配置
      步骤、飞书应用权限/事件订阅/回调设置、操作说明（全部文本指令）、外部探测
      配置步骤、备份恢复步骤、故障处理表、部署后校准导入阈值、发布分支由 `v2`
      切换到 `main` 的简短指引。**没有放进 `docs/ops-agent/`**——实现期发现
      `docs/` 整个目录被 `.gitignore` 排除（"规格的唯一来源是 openspec/，不是
      这里——docs/ 只是本地放参考资料的地方"），放在那里写了等于没写，PR 里根本
      看不见。改放 `etc/`，与已有的 `etc/observability-runbook.md`
      （CLAUDE.md 点名的运维文档入口）同一条路径纪律。
- [x] 8.2（发现原计划不可行，已改路线）**不更新
      `docs/ops-agent/00-ops-agent-design.md`**——它是本机本地文件，从未进过
      版本库，编辑它不会被提交，本变更看不见、未来任何会话也看不见，等于白做。
      "旧稿假设已被取代"这件事记在 `design.md` 的 Context 一节与本条目里，
      对面向未来的读者已经足够；proposal.md 也已同步改口径去掉这条不成立的
      "少量改动"清单项。
- [x] 8.3 D6 部署材料清单文档化：`etc/keywordiq-ops-bot-runbook.md` 第 3 节
      （飞书凭证/open_id/chat_id/GH_TOKEN/v2 部署路径/既有 crontab 核实/阿里云
      云监控权限），密钥占位说明"写进 `.env`，不进代码仓库"。

## 9. 收尾验证

- [ ] 9.1 按改动范围跑 `uv run ruff check .` + `uv run pytest test/ -q`；涉及
      `ops` schema 迁移与 `import_queue.py` 改动，按 `local-db` 要求跑一次完整回归
      （含集成测试），不得把跳过集成测试描述为通过。
- [ ] 9.2 汇总本次未能在实现阶段自动验证、依赖生产/飞书实机的验收条件清单（对应 Issue
      #90 验收条件中的相应项），作为 PR 说明的一部分明确列为待办，不在 PR 里宣称已完成。
- [ ] 9.3 `git status --short` 核对索引列，按路径提交（遵守仓库"提交前看索引列"约定）。
