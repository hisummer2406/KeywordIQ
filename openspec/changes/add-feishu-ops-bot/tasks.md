> **认领记录**：2026-09-16，Claude Code 会话（worktree `~/.claude-worktrees/add-feishu-ops-bot`，
> 分支 `change/add-feishu-ops-bot`）。当前状态：proposal/design/specs 四件套已生成并通过
> `openspec validate`，**尚未开始实现**，任务列表待下一轮会话（或本会话续接）按顺序执行。

## 0. 实现前置说明

**验收条件依赖真实环境的标注约定**：下面每个任务组末尾若有标 `[生产/飞书实机]` 的任务，
表示该任务需要真实飞书应用、真实阿里云账号或生产服务器才能完成验证，本仓库内无法自动化
验证；这类任务在实现阶段只能产出可执行的演练脚本/文档，由用户在部署后手动跑一遍并确认，
**不得在没有跑过的情况下宣称验收通过**。

## 1. 数据库底座：`ops` schema、角色、迁移

- [ ] 1.1 新建 Alembic 迁移：`CREATE SCHEMA ops`、创建 `ops_bot_role` 角色并授予
      `ops` schema 全部权限、对 `analysis.import_batch` 与 `analysis.site_data_stats`
      仅授 `SELECT`；用 `op.execute` 写原生 DDL/DCL（非 ORM 可表达部分）。
- [ ] 1.2 定义 `ops_bot/model.py`：`OpsTask`/`OpsAuditLog`/`OpsDedupKey`/
      `OpsAlertState`/`OpsBackupRecord`/`OpsImportBaseline` 六个 SQLAlchemy 2.0
      `DeclarativeBase` 模型，`schema="ops"`，字段按 design.md D1 表格。
- [ ] 1.3 用同一份迁移（或紧随其后的一条）建出上述六张表；`alembic upgrade head --sql`
      核对生成的 DDL 落在 `ops` schema。
- [ ] 1.4 测试：迁移单元测试（不连真实库的部分）+ 需要 `TEST_DATABASE_URL` 的集成测试，
      断言 `ops_bot_role` 对业务表写操作被拒绝、对 `ops` schema 读写正常
      （`uv run pytest test/ -q`，集成部分需 `local-db`）。

## 2. 配置与项目骨架

- [ ] 2.1 `config.py` 的 `Settings` 新增 `OPS_DATABASE_URL`/`OPS_DATABASE_URL_ASYNC`/
      `FEISHU_APP_ID`/`FEISHU_APP_SECRET`/`FEISHU_VERIFICATION_TOKEN`/
      `FEISHU_ENCRYPT_KEY`/`FEISHU_OWNER_OPEN_ID`/`FEISHU_OPS_CHAT_ID`/
      `GH_TOKEN`/`OPS_ALERT_SILENCE_MINUTES`/`OPS_IMPORT_DAY_TIMEOUT_MINUTES`/
      `OPS_IMPORT_WEEK_TIMEOUT_MINUTES`/`OPS_ERROR_RATE_THRESHOLD`/
      `OPS_BACKUP_RETENTION_DAYS`/`OPS_CONFIRM_TTL_MINUTES`/
      `OPS_MAINTENANCE_WINDOW_MINUTES`（design.md D3/D5 给出的默认值）。
- [ ] 2.2 同步更新 `.env.example`，每项带单位、安全的本地默认值或留空说明。
- [ ] 2.3 新建 `ops_bot/` 包骨架（`main.py`/`settings.py`/`database.py`/
      `feishu/`/`checks/`/`executor/`/`scheduler.py`/`model.py`/`crud.py`/
      `service.py`/`schemas.py`/`api_internal.py`），`database.py` 用
      `OPS_DATABASE_URL`/`OPS_DATABASE_URL_ASYNC` 建独立引擎（不复用 `database.py` 里
      业务引擎）。
- [ ] 2.4 新增 `Dockerfile` entrypoint 分支或独立 `ops_bot` 启动命令；
      `docker-compose.yml` 新增 `ops-bot` 服务（`127.0.0.1:8101` 回环端口，
      `restart: unless-stopped`，`depends_on: db`）。
- [ ] 2.5 `etc/keywordiq-nginx.conf` 新增 `/ops/feishu/webhook` 与 `/ops/health` 反代
      到 `127.0.0.1:8101`。

## 3. 飞书接入底座（`ops-bot-platform`）

- [ ] 3.1 `feishu/client.py`：`tenant_access_token` 换取、进程内缓存 + `asyncio.Lock`
      防并发换取（同构 `app/auth/dingtalk_client.py` 的企业 token 段）。
- [ ] 3.2 `feishu/events.py`：处理 URL 校验挑战（先于签名/鉴权）、消息事件、卡片回调
      分发；`feishu/auth.py`：Verification Token/Encrypt Key 校验 + `open_id`/`chat_id`
      白名单双重校验。
- [ ] 3.3 `ops_bot/main.py`：`/ops/feishu/webhook` 端点在飞书重试窗口内完成幂等判定
      （查/写 `OpsDedupKey`）与初始响应，业务逻辑丢 `asyncio.create_task` 后台执行；
      `/ops/health` 端点读取最近一次 quick 分组心跳时间，超过阈值返回 503。
- [ ] 3.4 `feishu/cards.py`：状态摘要卡片、确认卡片（重启/发布通用模板）、结果卡片
      模板函数（强类型入参，不接受自由字符串拼接）。
- [ ] 3.5 `executor/`：`OpsAction` 枚举 + 每个动作的固定 argv 执行函数，统一 300 秒
      超时、输出截断落 `OpsTask.output`。
- [ ] 3.6 `scheduler.py`：quick(60s)/full(300s)/15min(900s)/daily 多组常驻循环，
      每组判据用"距上次成功执行时间"（查 `OpsTask`），非内存计时器。
- [ ] 3.7 测试：webhook 幂等（同一 event_id 两次请求只处理一次）、鉴权白名单（伪造
      open_id/chat_id 被拒）、心跳端点异常返回 503 的单元测试。
- [ ] 3.8 `[生产/飞书实机]` 新建 `scripts/check_feishu_ops.py`（自检脚本，design.md
      D2 列出的四项校验），本地先用假凭证跑通"预期失败"路径，真实凭证的验证留给
      部署阶段。

## 4. 巡检检查器与告警（`ops-monitoring-alerting`）

- [ ] 4.1 `checks/`：quick 分组（容器存活、心跳落盘）、full 分组（连接池水位、导入卡死、
      日志错误率）、15min 分组（磁盘，复用 `monitoring.py` 现有阈值）。
- [ ] 4.2 告警去重与升级：`OpsAlertState` 读写、静默期判定、连续 3 周期升级"强提醒"、
      恢复通知不受静默期限制。
- [ ] 4.3 维护窗口：重启/发布执行开始时为 `service-down`/`db-conn` 两个 key 打开
      `maintenance_until`，巡检据此降级告警等级，窗口过后恢复正常判定。
- [ ] 4.4 导入完成/失败通知：读取 `import_batch` 状态变化（复用现有轮询/查询接口，
      不新增对业务表的写操作）。
- [ ] 4.5 导入基线记录：每次导入 `COMPLETED` 时写一行 `OpsImportBaseline`；日报/周报
      各自独立超时阈值判定卡死。
- [ ] 4.6 每日 09:00 巡检摘要：内容拼装 + 幂等判定（当天是否已发送过，查
      `OpsTask kind=DAILY_SUMMARY` 当日记录）。
- [ ] 4.7 状态与有界脱敏日志查询指令：复用 `add-request-diagnostics-logging` 已建的
      日志基础，按 request_id/时间窗口查询，条数与范围有界。
- [ ] 4.8 测试：告警去重/升级/恢复的单元测试；维护窗口降级逻辑测试；09:00 摘要幂等
      测试（模拟重启场景）；导入卡死判定测试（含"无基线不断言卡死"场景）。
- [ ] 4.9 `[生产/飞书实机]` 阿里云云监控·站点监控配置：业务 `/health` 与
      `/ops/health` 两个拨测目标，通知对象选飞书机器人；实测触发一次告警，核对飞书群
      实际收到的消息格式与内容。
- [ ] 4.10 `[生产/飞书实机]` 整机失联演练：隔离/受控关闭网络（或模拟停机），验证外部
      拨测独立于本机发出告警；同时验证仅 `ops-bot` 进程被杀（机器本身正常）时
      `/ops/health` 能被拨测判定为异常。

## 5. 备份（`ops-backup`）

- [ ] 5.1 `ops_bot` 内封装调用 `scripts/backup_selfhosted_pg.py`（复用现有脚本，不重写
      备份逻辑），记录 `OpsBackupRecord`（含失败阶段：DUMP/COPY_OUT/UPLOAD）。
- [ ] 5.2 定时触发（daily 分组，"距上次成功"判据）+ 飞书手动触发指令，两者共用同一份
      执行与记录逻辑。
- [ ] 5.3 备份超期检查（24h warn / 48h crit）接入 full 分组告警。
- [ ] 5.4 保留期清理：备份成功后清理超过 `OPS_BACKUP_RETENTION_DAYS` 的旧 OSS 对象，
      清理动作写入同一条审计记录。
- [ ] 5.5 查询最近备份指令（时间/大小/产物标识/状态）。
- [ ] 5.6 测试：备份各阶段失败分别正确记录失败阶段（mock 各阶段异常）；保留期清理只
      删超期对象的单元测试；查询指令返回格式测试。
- [ ] 5.7 `[生产/飞书实机]` 部署前核实生产是否已有独立 crontab 在跑
      `backup_selfhosted_pg.py`（`crontab -l`），若有则先禁用旧任务再启用本系统内置
      调度，避免双跑；在 runbook 中写明这一步骤。
- [ ] 5.8 `[生产/飞书实机]` 用真实 OSS 桶跑一次手动备份，核对产物；按脚本文档已验证过
      的隔离环境恢复步骤，实测一次恢复演练并留存证据。

## 6. 重启与发布确认（`ops-release-control`）

- [ ] 6.1 重启动作：预定义 `RESTART_APP`/`RELOAD_NGINX`，确认卡片生成前先查
      `import_batch` 快速拦截（design.md D5 第一层）。
- [ ] 6.2 发布 CI 校验：调用 GitHub Checks API 核实目标 SHA 的 `backend`/`frontend`/
      `extension` 三个 job 结论全部 `success`；等待中/失败/找不到均判定为不放行。
- [ ] 6.3 SHA 校验：只接受完整 40 位 SHA，`git merge-base --is-ancestor` 校验落在允许
      分支历史上。
- [ ] 6.4 发布确认卡片：当前/目标 SHA、变更摘要、CI 结果、迁移标注（对比目标 SHA 的
      alembic heads 与生产 `alembic current`）、预期影响。
- [ ] 6.5 确认状态机：`PENDING_CONFIRM`→乐观锁 `UPDATE ... RETURNING` 转
      `CONFIRMED`→执行；过期判定与 `EXPIRED` 状态回写；`confirm_token` 校验。
- [ ] 6.6 双向咨询锁：新增 `_OPS_ACTION_LOCK_KEY = 0x4B4F`，`executor` 执行前
      `pg_try_advisory_lock`；同步修改 `app/table/upload/import_queue.py` 的认领查询，
      认领前对同一 key 做 `pg_try_advisory_lock` 探测并立即释放，抢不到则本轮跳过。
- [ ] 6.7 执行前重新核实：CI 状态、SHA 归属、导入空闲状态、`_OPS_ACTION_LOCK_KEY` 均
      在执行的这一刻重新查询，不复用确认时的快照。
- [ ] 6.8 非交互调用 `deploy.sh`：按迁移标注显式传 `--migrate --yes` 或
      `--skip-migrate --yes`（布尔字段驱动，不做字符串拼接）；捕获阶段化的失败信息
      （前置检查/构建部署/迁移/健康验证）。
- [ ] 6.9 互斥：重启/发布任务级互斥（同一时刻只允许一个执行中的运维动作），拒绝时
      明确提示当前占用的任务。
- [ ] 6.10 回滚边界：目标 SHA 的 alembic heads 落后于当前生产版本时拒绝执行并说明
      原因。
- [ ] 6.11 测试：确认状态机各分支（未确认/已确认/过期/重复点击/目标变化）单元测试；
      CI 校验各失败模式单元测试（mock GitHub API）；SHA 校验单元测试；互斥单元测试；
      迁移标注/回滚边界单元测试。
- [ ] 6.12 集成测试（`local-db`）：模拟"确认后、执行前导入抢先获得咨询锁"与"导入
      认领中途运维动作抢锁"两种交叉时序，断言互斥生效、任一方失败时报告清晰原因（对应
      design.md 风险段与 `ops-release-control` spec 的"确认期间导入抢先开始"场景）。
- [ ] 6.13 `[生产/飞书实机]` 受控完成一次真实确认发布：从生成确认卡片、人工点击确认、
      执行、到发布后验证，全流程走一遍；额外构造一次部署失败/健康检查失败场景，确认
      不误报成功且报告正确的失败阶段。

## 7. 权限、审计收尾

- [ ] 7.1 审计记录写入：每个受限动作执行前后写 `OpsAuditLog`（操作者/动作/目标/
      SHA/确认时间/结果/失败阶段），敏感字段脱敏后再落库。
- [ ] 7.2 `ops_task`/`ops_audit_log`/`ops_dedup_key` 90 天保留清理（daily 分组）。
- [ ] 7.3 通知失败与执行失败分开记录：飞书消息发送失败不得覆盖或掩盖任务本身的执行
      结果；通知重试逻辑 MUST NOT 重新触发已执行过的动作。
- [ ] 7.4 业务应用/数据库异常时的宿主机级诊断：即便无法连接业务数据库，`ops-bot`
      仍能报告健康检查失败并提供容器状态/磁盘等不依赖业务库的诊断信息。
- [ ] 7.5 测试：审计记录脱敏单元测试；通知失败与执行结果分离的单元测试。

## 8. 文档与部署材料

- [ ] 8.1 编写 runbook：安装配置步骤、飞书应用权限/事件订阅/回调设置、操作说明、
      外部探测配置步骤、备份恢复步骤、故障处理、发布分支由 `v2` 切换到 `main` 的简短
      指引（放 `docs/ops-agent/` 或 `etc/`，与现有 `observability-runbook.md` 并列）。
- [ ] 8.2 更新 `docs/ops-agent/00-ops-agent-design.md`：标注旧稿的 RDS/CLI-only/自愈/
      钉钉等假设已被本变更取代，指向本变更文档。
- [ ] 8.3 D6 部署材料清单文档化（飞书凭证/open_id/chat_id/GH_TOKEN/v2 部署路径/既有
      crontab 核实/阿里云云监控权限），密钥占位说明"直接配置到部署环境，不写入仓库"。

## 9. 收尾验证

- [ ] 9.1 按改动范围跑 `uv run ruff check .` + `uv run pytest test/ -q`；涉及
      `ops` schema 迁移与 `import_queue.py` 改动，按 `local-db` 要求跑一次完整回归
      （含集成测试），不得把跳过集成测试描述为通过。
- [ ] 9.2 汇总本次未能在实现阶段自动验证、依赖生产/飞书实机的验收条件清单（对应 Issue
      #90 验收条件中的相应项），作为 PR 说明的一部分明确列为待办，不在 PR 里宣称已完成。
- [ ] 9.3 `git status --short` 核对索引列，按路径提交（遵守仓库"提交前看索引列"约定）。
