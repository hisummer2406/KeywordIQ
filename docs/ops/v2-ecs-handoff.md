# V2 ECS 部署交接（2026-09-19）

本文件记录可继续执行的事实状态，不含任何 AccessKey、密码、App Secret、SSH 私钥或其他密钥。任何部署、回调配置或运维变更开始前都先阅读本文件。

## 已上线的 V2 基础环境

- ECS：阿里云 Alibaba Cloud Linux 3，4 vCPU / 8 GiB；部署目录 `/var/www/keywordiq`，运行用户 `www`。
- V1 与 V2 同机但完全隔离：V1 仍为裸部署，域名 `amazon.liangtong.online`，后端端口 8000；不得修改或停止。
- V2 域名 `key.liangtong.online`；host Nginx 托管 `/var/www/keywordiq-web`，将 `/api/` 与 `/health` 反代到 Docker app 的 `127.0.0.1:8001`。
- V2 Compose 项目名 `keywordiq-v2`，包含 app、PostgreSQL 15.8 与 Netdata；数据库为独立的 `keywordiq_v2`，数据目录 `/var/lib/keywordiq-v2-pgdata`。
- TLS 已在 Nginx 生效。不要记录或提交证书私钥。

## 已验证的能力

- `https://key.liangtong.online/` 与 `/health` 已可用。
- GitHub Actions 的 V2 CD 仅能手动触发：合并到 `main` 后先等待该 merge commit 的 main push CI 发布门禁通过（受影响项成功，无关项经范围核验后跳过），再在 Actions 运行 `Deploy Main to ECS`。按 [ECS 发布手册](../../.github/v2-ecs-cd.md) 核对候选及当前运行版本。
- OSS Bucket 为 `keywordiq-release`（杭州），服务端走内网 Endpoint；STS、前端临时上传权限、服务端读回、CORS 和 7 天未完成分片清理均已通过 `scripts/check_oss.py` 验证。密钥仅保存在 ECS `.env`。

## 当前飞书运维机器人状态

- 飞书使用企业自建应用的机器人能力，不是群自定义机器人，也不是工作台卡片。
- 已合并的 PR #108（commit `75a90c4`）为 V2 增加 `/ops/` Nginx 反代到 `127.0.0.1:8101`，并把 `keywordiq-ops-bot.service` 对齐到 `/var/www/keywordiq` 和 `www` 用户。
- 该 commit 的 V2 CI 已成功：<https://github.com/hisummer2406/KeywordIQ/actions/runs/35440430922>。
- 首次部署尝试在 CI 尚未完成时被部署门禁拒绝，ECS 没有改动；随后在 CI 成功后重新部署并已成功完成：<https://github.com/hisummer2406/KeywordIQ/actions/runs/35441609054>。
- 2026-09-20 已在 ECS 完成：ops-bot 的 host 级安装（迁移、`uv` 环境、systemd unit、Nginx `/ops/` 反代，生效文件是 `/etc/nginx/conf.d/key.liangtong.online_80.conf`），`/ops/health` 返回 `ok`；飞书事件订阅已保存（启用了加密）；`scripts/check_feishu_ops.py` 四项全部通过。
- **单人运维，不建群**：`FEISHU_OPS_CHAT_ID` 是与机器人的单聊 `chat_id`。取法与踩坑见运行手册第 2 节步骤 7（务必用应用身份取，否则之后发消息全部 `230002`）。
- 事件订阅方式是“事件发送到服务器”；回调地址 `https://key.liangtong.online/ops/feishu/webhook`。
- 飞书权限可在“权限管理 → 批量导入/导出权限”导入；精确 JSON 以当前接入对话或 `ops_bot/feishu/client.py` 的实际调用为准。不要把 App Secret 写进仓库。

## 未完成项与接手顺序

1. 在与机器人的单聊里依次演练 `状态`，最后演练带确认卡片的 `重启`；发布操作必须使用完整 40 位 SHA。卡片按钮回调是未经真实客户端验证的部分，`重启` 演练时留意。
2. 不做数据库备份（2026-09-20 决定）：已移除「备份逾期」告警与每日定时备份；手动 `备份` 指令保留。
3. 按运行手册第 5 节配置阿里云云监控的 `/health` 与 `/ops/health` 拨测。

## 交接纪律

- `.env`、证书、AccessKey、GitHub token、飞书 App Secret、Verification Token、Encrypt Key、SSH 私钥一律不得提交、粘贴或写入本文件。
- 修改 V2 部署或机器人配置前，遵守 `CLAUDE.md` 的 worktree、OpenSpec、PR 与 CI 流程；PR 基线必须是 `main`。
