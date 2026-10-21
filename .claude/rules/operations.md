---
paths:
  - "ops_bot/**/*.py"
  - "etc/**/*"
  - ".github/workflows/deploy-v2.yml"
  - ".github/v2-ecs-cd.md"
  - "deploy.sh"
  - "start.sh"
  - "docker-compose.yml"
  - "Dockerfile"
  - "database.py"
  - "config.py"
  - "scripts/*selfhosted*.py"
  - "scripts/check_oss.py"
  - "scripts/check_feishu_ops.py"
  - "docs/ops/**/*"
  - "test/test*ops_bot*.py"
---

# 生产与容量操作约束

- 动生产前读 [交接](../../docs/ops/v2-ecs-handoff.md) 和 [运维导航](../../openspec/guides/operations.md)；交接状态仍需按所声称层面验证。
- V2 只绑 127.0.0.1:8001，Compose 项目 keywordiq-v2，所有 ports 只回环。
- 发布只走 Deploy V2 to ECS：refs/heads/v2、完整 SHA 在 origin/v2 历史、该 SHA 后端/前端/插件 CI 全部 completed/success。
  不放宽检查。授权与失败处理见根 CLAUDE.md「生产操作边界」，这里不重复。
- 触发前核对：最新目标、无未推送提交、三项 CI、是否含新迁移、无进行中导入；有迁移先说明无自动备份并等确认。
- 生产 .env 的 db 主机属于容器网络；不要裸用宿主机连接执行迁移，按发布工作流封装执行。
- 池预算不变式：（同步池+异步池，各自 pool_size+max_overflow）×worker 数，加 ops-bot 池，必须小于 PG_MAX_CONNECTIONS 并留余量；当前来源看 config.py/database.py/ops_bot/database.py/Compose。
  THREAD_POOL_LIMIT 与 DB 池独立，不能跟同步池一起缩到 5；PG 与应用共享 CPU/内存，调整前实测。
- ops_bot 不互 import 业务 app；ops schema/角色独立，新表登记 OPS_TABLES；导入互斥锁两边同 key。
- 机器人只执行预定义动作，重启/发布需确认卡、完整 SHA 与 CI，导入进行中拒绝；不把卡片配置等同真机验收。
- OSS STS/CORS/ETag、飞书回调、健康拨测需分别实测；不要从旧文档复制当前环境值或把历史状态当现状。
