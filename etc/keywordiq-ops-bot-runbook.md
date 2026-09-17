# 飞书运维机器人（ops-bot）部署与操作手册

面向"要把这套机器人真正装起来、用起来"的操作手册。设计决策与取舍理由见
`openspec/changes/add-feishu-ops-bot/design.md`（本文档不重复那些理由，只讲怎么做）；
旧版设计稿 `docs/ops-agent/00-ops-agent-design.md` 的钉钉/RDS/CLI-only/自愈等假设已被
本变更取代，仅作历史参考。

## 1. 安装配置步骤

前置：`v2` 分支已部署（`docker-compose.yml` 管理的 `app`/`db` 正常运行），
`uv sync` 已跑过。

**在运行下面的步骤 1 之前，先把 `.env` 里 `OPS_DATABASE_URL`/`OPS_DATABASE_URL_ASYNC`/
`OPS_DB_ROLE_PASSWORD` 三项（见第 3 节）填成你自己的真实密码，三处密码需一致。**
迁移会用 `OPS_DB_ROLE_PASSWORD` 当场 `(re)set` `ops_bot_role` 的数据库密码（见迁移
文件 `d4e5f6a7b8c9` 的说明）；先跑迁移、之后才改这三项，只会改掉 `.env` 里的值，
角色在数据库里的真实密码仍停在迁移执行那一刻读到的旧值（未配置时的兜底值
`change-me-in-production`），启动服务后 ops-bot 用新密码连接会认证失败，且日志里
看不出这是密码没对齐的问题。走完第 6 步起服务前若已经改过这三项，重新跑一次
`docker compose run --rm app alembic upgrade head` 即可让角色密码追上 `.env`。

```bash
# 1. 迁移：新建 ops schema、ops_bot_role 角色、六张状态表（在 app 容器内跑，
#    与业务迁移同一条流水线）
docker compose run --rm app alembic upgrade head

# 2. db 服务暴露 127.0.0.1:5432——host 级 ops-bot 不在 compose 内部网络里，
#    需要这个端口才能连上（只本机可达，docker-compose.yml 已配好，
#    重新 up 一次生效）
docker compose up -d db

# 3. 装 systemd 单元
sudo cp etc/keywordiq-ops-bot.service /etc/systemd/system/
sudo systemctl daemon-reload

# 4. 建运行账号（示例；实际用户名/组按你的服务器策略调整）与工作目录权限
sudo useradd --system --home /opt/keywordiq --no-create-home opsbot
sudo usermod -aG docker opsbot   # RESTART_APP/DEPLOY 要跑 docker compose

# 5. 装 nginx 配置（etc/keywordiq-nginx.conf 已含 /ops/ 反代段，按现有部署方式生效）
sudo nginx -t && sudo systemctl reload nginx

# 6. 填好 .env 剩余的「飞书运维机器人」变量（第 2/3 节，数据库三项已在步骤 1 前填好），然后起服务
sudo systemctl enable --now keywordiq-ops-bot
sudo systemctl status keywordiq-ops-bot
```

**`deploy.sh` 内部会用到的 `sudo` 命令**（`RELOAD_NGINX`/`DEPLOY` 执行时以
`opsbot` 账号触发）需要配置 NOPASSWD sudoers，覆盖 `deploy.sh` 里 `as_root()`
实际调用的那几类命令（`nginx -t`、`systemctl reload nginx`、`cp` 到
`/etc/logrotate.d/`、`logrotate -d`、`rsync` 到 `$WEB_ROOT`、`mkdir -p $WEB_ROOT`）。
示例（按你服务器的实际路径调整）：

```
# /etc/sudoers.d/opsbot
opsbot ALL=(root) NOPASSWD: /usr/sbin/nginx -t, /bin/systemctl reload nginx, \
    /bin/cp * /etc/logrotate.d/keywordiq, /usr/sbin/logrotate -d /etc/logrotate.d/keywordiq, \
    /usr/bin/rsync -a --delete * /var/www/keywordiq/, /bin/mkdir -p /var/www/keywordiq
```

## 2. 飞书应用配置

1. 飞书开放平台创建**企业自建应用**，开通**机器人**能力与**消息卡片**。
2. 记录 `App ID`/`App Secret`。
3. 「事件订阅」页：回调地址填 `https://<你的域名>/ops/feishu/webhook`，记录
   `Verification Token`；如需加密，同时启用并记录 `Encrypt Key`。订阅
   `im.message.receive_v1`（接收消息）事件。
4. 「消息卡片」页：把卡片回调地址也配成同一个 URL（飞书经典卡片回传交互，与事件
   订阅共用一个入口）——**这一条是本变更依据官方文档实现、未经真实客户端验证的
   部分**，配置后务必按第 7 节演练一次确认真的收得到回调。
5. 把机器人拉进你的运维群，记录群的 `chat_id`。
6. 拿到你本人的飞书 `open_id`（`ou_` 前缀）——可以让机器人临时打印收到的事件来看
   （或用飞书开放平台的接口调试工具查）。

## 3. 部署材料清单（写进 `.env`，不进仓库）

| 变量 | 来源 |
|---|---|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 第 2 节步骤 1-2 |
| `FEISHU_VERIFICATION_TOKEN` / `FEISHU_ENCRYPT_KEY` | 第 2 节步骤 3 |
| `FEISHU_OWNER_OPEN_ID` | 第 2 节步骤 6 |
| `FEISHU_OPS_CHAT_ID` | 第 2 节步骤 5 |
| `OPS_DATABASE_URL` / `OPS_DATABASE_URL_ASYNC` / `OPS_DB_ROLE_PASSWORD` | 自定，三处密码需一致，见 `.env.example` |
| `GH_TOKEN`（`checks:read`+`contents:read`）/ `GH_REPO` | GitHub 生成一个最小权限的 token |
| v2 部署路径与访问方式 | 确认与 `etc/keywordiq-ops-bot.service` 的 `WorkingDirectory` 一致 |
| 现有 crontab（`crontab -l`） | 见第 6 节，核实是否已有独立的备份定时任务 |
| 阿里云云监控账号可访问 + 站点监控配置权限 | 见第 5 节 |

其余阈值类配置（`OPS_ALERT_SILENCE_MINUTES` 等）有安全的默认值，先用默认值跑起来，
按第 8 节校准。

## 4. 操作说明：飞书群里能做什么

在运维群里 @机器人（或直接发消息，视你的会话设置）：

| 指令 | 作用 |
|---|---|
| `状态` | 服务/磁盘/备份新鲜度一屏摘要 |
| `任务` | 最近 10 条运维任务记录 |
| `日志 <request_id>` | 按请求编号查日志（有界，只查近期） |
| `备份记录` | 最近一次成功备份的时间/产物/大小 |
| `备份` | 立即触发一次手动备份（不需确认，异步执行，完成后单独回报结果） |
| `重启` | 生成重启确认卡片，需点击「确认执行」才真正重启 |
| `发布 <完整 40 位 commit SHA>` | 生成发布确认卡片，展示 CI 结果/迁移标注/变更摘要，需点击确认 |

确认卡片默认 10 分钟有效期（`OPS_CONFIRM_TTL_MINUTES`），过期需重新发起；执行期间
（重启/发布）会自动打开维护窗口，巡检不会把这段预期的短暂不可用当成异常告警。

导入进行中时，`重启`/`发布` 都会被直接拒绝（不生成确认卡片）；确认后到真正执行
之间如果有新导入抢先开始，执行会在最后一刻中止并说明原因，不会与导入交叉写。

## 5. 外部存活探测：阿里云云监控配置

按 `design.md` D4 的决策，用云监控·站点监控，不额外起探测进程：

1. 阿里云控制台 → 云监控 → 站点监控 → 新建监控任务。
2. 创建两个拨测任务：
   - 业务健康：`https://<域名>/health`，预期返回 HTTP 200。
   - 机器人心跳：`https://<域名>/ops/health`，预期返回 HTTP 200（**注意**：巡检
     循环卡死时这个端点会主动返回 503，拨测应把非 200 都判定为异常）。
3. 报警联系人 → 新建「机器人」类型的通知对象，选**飞书**，填入飞书机器人的
   webhook 地址（自定义机器人 webhook，不是本变更的事件订阅回调地址——两者是
   两个不同的东西，前者是飞书群自定义机器人功能，后者是应用机器人的回调）。
4. 把这个通知对象绑定到两个拨测任务的报警规则上。
5. 演练：临时把 `keywordiq-ops-bot` 服务停掉（`systemctl stop
   keywordiq-ops-bot`），确认几分钟内飞书群收到"机器人心跳"告警；恢复后确认收到
   恢复通知。整机断网的演练需要能控制宿主机网络（如临时改安全组），按你的运维
   窗口安排单独进行。

## 6. 备份与恢复

- 内置调度每日 03:00（Asia/Shanghai）自动触发一次全量备份，成功后自动清理超过
  `OPS_BACKUP_RETENTION_DAYS` 天的旧产物。
- **部署前必须核实 `crontab -l` 是否已有独立的 `backup_selfhosted_pg.py` 定时
  任务**——如果有，先 `crontab -e` 移除，避免与内置调度双跑（重复备份不是错误，
  但白白多占 OSS 空间与 pg_dump 负载）。
- 恢复演练步骤见 `scripts/backup_selfhosted_pg.py` 模块 docstring 顶部——已用真实
  OSS 桶验证过，可直接照抄；**生产数据库恢复不通过飞书指令开放**，只能按这份文档
  手动执行。

## 7. 故障处理

| 现象 | 排查方向 |
|---|---|
| 机器人不回复任何消息 | `systemctl status keywordiq-ops-bot`；`journalctl -u keywordiq-ops-bot -n 100` |
| 扫码/事件订阅配置后飞书后台显示"验证失败" | 确认 `/ops/feishu/webhook` 能被公网访问（`curl https://<域名>/ops/health`），nginx 反代是否生效 |
| 卡片按钮点击没反应 | 这是未经真实客户端验证的部分（见第 2 节步骤 4）；先确认卡片回调地址配置正确，再查 `journalctl` 里 `_handle_card_action` 相关日志 |
| `/ops/health` 返回 `db_unreachable` | 自建 PG 容器是否在跑（`docker compose ps db`）；`OPS_DATABASE_URL_ASYNC` 是否正确指向 `127.0.0.1:5432` |
| 重启/发布一直提示"导入进行中" | `docker compose exec db psql -U postgres -d $POSTGRES_DB -c "SELECT id,status FROM analysis.import_batch WHERE status IN ('PROCESSING','UPLOADING')"`，确认是否真的有导入在跑 |
| 发布一直提示"CI 未全部通过" | 用 `任务` 指令或直接查 GitHub 该 SHA 的 Checks 页面，确认 `backend`/`frontend`/`extension` 三个 job 状态 |
| 每日巡检摘要没发 | 确认服务在 09:00 前后确实在跑（`should_run` 判据要求"09:00 之后"，服务若在 08:58 崩溃重启，摘要仍会在下次轮询补发） |

`#4` 类导入期卡死复发时的证据采集流程见 `etc/observability-runbook.md`，与本机器人
无关的部分不在这里重复。

## 8. 部署后必做：校准导入耗时阈值

`OPS_IMPORT_DAY_TIMEOUT_MINUTES`/`OPS_IMPORT_WEEK_TIMEOUT_MINUTES` 的默认值是保守
估计。首次真实导入（日报/周报各至少一次）完成后：

```bash
docker compose exec db psql -U postgres -d "$POSTGRES_DB" \
  -c "SELECT period_type, duration_seconds, recorded_at FROM ops.ops_import_baseline ORDER BY recorded_at DESC LIMIT 10"
```

按观测到的真实耗时（乘 3-4 倍留余量）调整 `.env` 里的两个阈值，重启
`keywordiq-ops-bot` 生效。

## 9. 发布分支由 `v2` 切换到 `main`

试运行结束、`v2` 合并进 `main` 后（人工操作，本机器人不参与），只需要：

1. 把 `release/git_ref.py` 里 `is_ancestor_of_branch`/`build_deploy_plan` 默认的
   `branch: str = "v2"` 改成 `"main"`（或在调用处显式传参，视你届时的调用约定）。
2. 确认 `.github/workflows/ci.yml` 的三个必需 job 名称未变。
3. 用第 4 节的 `发布 <sha>` 指令走一次完整确认流程验证。

本机器人不自动判断试运行是否结束，也不自动执行这次切换——按 Issue #90 的非目标，
分支切换是人工决定的一次性操作。
