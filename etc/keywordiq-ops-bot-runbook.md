# 飞书运维机器人（ops-bot）部署与操作手册

面向"要把这套机器人真正装起来、用起来"的操作手册。设计决策与取舍理由见
`openspec/changes/add-feishu-ops-bot/design.md`（本文档不重复那些理由，只讲怎么做）；
旧版设计稿 `docs/ops-agent/00-ops-agent-design.md` 的钉钉/RDS/CLI-only/自愈等假设已被
本变更取代，仅作历史参考。

## 1. 安装配置步骤

前置：V2 应用已部署在 `/var/www/keywordiq`（`docker-compose.yml` 管理的
`app`/`db` 正常运行）。机器人运行在宿主机，因此还需要宿主机安装 `uv`，并在仓库根执行
`uv sync --no-dev` 创建 `.venv`。

**在运行下面的步骤 1 之前，先把 `.env` 里 `OPS_DATABASE_URL`/`OPS_DATABASE_URL_ASYNC`/
`OPS_DB_ROLE_PASSWORD` 三项（见第 3 节）填成你自己的真实密码，三处密码需一致。**
迁移会用 `OPS_DB_ROLE_PASSWORD` 当场 `(re)set` `ops_bot_role` 的数据库密码（见迁移
文件 `d4e5f6a7b8c9` 的说明）；先跑迁移、之后才改这三项，只会改掉 `.env` 里的值，
角色在数据库里的真实密码仍停在迁移执行那一刻读到的旧值（未配置时的兜底值
`change-me-in-production`），启动服务后 ops-bot 用新密码连接会认证失败，且日志里
看不出这是密码没对齐的问题（journal 里是 `password authentication failed for user
"ops_bot_role"`）。**库已经在 head 时再跑 `alembic upgrade head` 是空操作，不会重设
密码**，对齐要直接改角色（密码经 psql 变量传入，含单引号也安全）：

```bash
PW=$(grep -E '^OPS_DB_ROLE_PASSWORD=' .env | cut -d= -f2- | tr -d "\"'")
echo "ALTER ROLE ops_bot_role WITH PASSWORD :'pw'" \
  | docker compose exec -T db psql -U postgres -d postgres -v pw="$PW"
sudo systemctl restart keywordiq-ops-bot
```

```bash
# 1. 迁移：新建 ops schema、ops_bot_role 角色、六张状态表（在 app 容器内跑，
#    与业务迁移同一条流水线）
docker compose run --rm app alembic upgrade head

# 2. db 服务暴露 127.0.0.1:5432——host 级 ops-bot 不在 compose 内部网络里，
#    需要这个端口才能连上（只本机可达，docker-compose.yml 已配好，
#    重新 up 一次生效）
docker compose up -d db

# 3. 为 host 级机器人准备依赖；V2 已有 www 账号、docker 组和受限 sudo 规则，无需新建 opsbot 用户
uv sync --no-dev

# 4. 安装 systemd 单元
sudo cp etc/keywordiq-ops-bot.service /etc/systemd/system/
sudo systemctl daemon-reload

# 5. 安装 V2 Nginx 配置（含 /ops/ 反代到 127.0.0.1:8101）
#    目标文件名以机器上实际生效的为准：先 `sudo nginx -T | grep -n "server_name key\."`
#    找到它。V2 ECS 上是 /etc/nginx/conf.d/key.liangtong.online_80.conf，直接
#    cp 到仓库里假设的另一个文件名会得到两份重复的 upstream/server_name。
#    覆盖前先 diff：仓库版本写死了证书路径。
sudo cp etc/keywordiq-v2-nginx.conf /etc/nginx/conf.d/key.liangtong.online_80.conf
sudo nginx -t && sudo systemctl reload nginx

# 6. 填好 .env 剩余的「飞书运维机器人」变量（第 2/3 节，数据库三项已在步骤 1 前填好），然后起服务
sudo systemctl enable --now keywordiq-ops-bot
sudo systemctl status keywordiq-ops-bot
```

**服务起不来、`status=127`**：`uv` 不在 systemd 的 `PATH` 里。unit 已同时列了
`/var/www/.local/bin` 与 `/home/www/.local/bin`；`uv` 装在别处就用
`sudo systemctl edit keywordiq-ops-bot` 加 drop-in 覆盖 `Environment=PATH=...`。
**宿主机手动跑脚本用 `.venv/bin/python scripts/xxx.py`，不要 `uv run`**：不带
`--no-sync` 的 `uv run` 会重新检查环境，`.venv` 的小版本与 `.python-version` 不符时会
退回到系统 `/usr/bin/python3`（Alibaba Cloud Linux 3 上是 3.6.8）并报错。

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
5. 把机器人拉进你的运维群，记录群的 `chat_id`。**只有一个人运维时不必建群**：
   `FEISHU_OPS_CHAT_ID` 填你与机器人的**单聊** `chat_id`（`oc_` 前缀）即可，白名单
   仍是 `open_id` 与 `chat_id` 同时匹配。
6. 拿到你本人的飞书 `open_id`（`ou_` 前缀）。
7. 单聊的 `chat_id`：开放平台「API 调试台」→ 发送消息，`receive_id_type=open_id`、
   `receive_id` 填上一步的值，**Token 必须选应用身份（`tenant_access_token`）**，返回体
   `data.sender.sender_type` 应为 `app`，其 `data.chat_id` 即是。选成用户身份会得到
   「你给自己发消息」的会话，机器人不在里面，之后发送一律 `230002`。
   不要用抓包取：启用了 `FEISHU_ENCRYPT_KEY` 后请求体是密文。
   授权被拒时 journal 里有一行 `飞书事件来自非授权用户/会话，拒绝处理：open_id=…
   chat_id=…`，发一条消息即可直接读到这两个值（也是 `FEISHU_OWNER_OPEN_ID` 与
   `FEISHU_OPS_CHAT_ID` 还没配对时排查的第一站）。

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
| `状态` | 服务/磁盘/当前部署提交/未解决告警一屏摘要 |
| `任务` | 最近 10 条运维任务记录 |
| `日志 <request_id>` | 按请求编号查日志（有界，只查近期） |
| `备份记录` | 最近一次备份尝试的时间/产物/大小（含失败） |
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

- **不做定时备份，也没有「备份逾期」告警**（2026-09-20 起）：机器人不再每日自动备份，
  `状态` / 每日摘要里也没有备份行。手动的 `备份` / `备份记录` 指令仍可用；要恢复定时
  备份，在 `ops_bot/jobs.py` 重新注册 `BACKUP` 任务（`backup.run_backup`）即可。
- 恢复演练步骤见 `scripts/backup_selfhosted_pg.py` 模块 docstring 顶部——已用真实
  OSS 桶验证过，可直接照抄；**生产数据库恢复不通过飞书指令开放**，只能按这份文档
  手动执行。

### 定时保留清理与 OSS 原始归档

- 机器人每日 03:30 运行 `RETENTION_PRUNE`，由 `.env` 的 `OPS_RETENTION_MODE` 控制：
  `off`（默认，不运行）/ `dry_run`（只预览）/ `execute`（预览后真实移出，**不可恢复**）。
  改配置后需重启 `keywordiq-ops-bot.service`；结果在 `ops_task` 的 `output`。
- 超期月份只有全部日报、周报期次的 OSS 原始文件校验通过才会移出，没有跳过手段。
- 收到 `retention-gate-blocked`：该月数据已保留。按告警的期次和原因处理：`no_batch`、
  `empty_object_key`、`no_rows` 查 `import_batch`；`object_missing`、`object_empty` 查
  OSS 对象，能补则重传重导；`oss_unavailable` 查 OSS 配置与网络。处理后次日自动重试。
- 收到 `retention-job-failed`：当日不会自动重跑，看 ops-bot 日志；手工诊断只用
  `docker compose run --rm app python scripts/prune_retention.py --dry-run`。
- **`keywordiq-release` 的原始对象不得配置过期规则**：它们是分区移出后唯一的恢复来源，
  也是后续 AI 训练的数据积累。转低频或归档存储不影响校验，但归档对象重导前要先解冻。
- 恢复已移出的月份：按批次的 object key 从 OSS 重新导入。

## 7. 故障处理

| 现象 | 排查方向 |
|---|---|
| 机器人不回复任何消息 | `systemctl status keywordiq-ops-bot`；`journalctl -u keywordiq-ops-bot -n 100` |
| 扫码/事件订阅配置后飞书后台显示"验证失败" | 确认 `/ops/feishu/webhook` 能被公网访问（`curl https://<域名>/ops/health`），nginx 反代是否生效 |
| 卡片按钮点击没反应 | 这是未经真实客户端验证的部分（见第 2 节步骤 4）；先确认卡片回调地址配置正确，再查 `journalctl` 里 `_handle_card_action` 相关日志 |
| 自检/告警报 `230002 Bot/User can NOT be out of the chat` | `FEISHU_OPS_CHAT_ID` 指向的会话里没有机器人——多半是用用户身份调试取到的 `chat_id`，按第 2 节步骤 7 重取 |
| 告警报 `230001 invalid receive_id` | `FEISHU_OPS_CHAT_ID` 为空或格式不对（须 `oc_` 前缀，不是 `ou_`） |
| 发消息机器人没回复、journal 里无任何报错 | 白名单没配对：看 `拒绝处理：open_id=… chat_id=…` 那行，与 `.env` 逐字符比对 |
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

## 9. 发布分支切换期间

迁移 #208 冻结发布时停止受理飞书发布请求，核对已确认的请求和正在执行的动作均已结束。
旧分支重命名后验证主仓已不再提供 `refs/heads/v2`。机器人计划和执行阶段都仍显式
fetch `v2`，远端分支缺失时拒绝继续。切换后的生产发布走 **Deploy Main to ECS**。

恢复飞书发布另行评审。恢复前覆盖 `ops_bot/release/git_ref.py` 和
`ops_bot/executor/__init__.py` 的分支校验、同一 SHA 和同一次 main push CI 的证据、
确认卡片与导入互斥，并明确与 Actions 发布入口的关系。
