"""飞书运维机器人（ops-bot）。

与业务 `app` 完全独立的进程/容器，见 openspec/changes/add-feishu-ops-bot/design.md。
不从这个包 import 业务 `app.*` 代码，也不被业务代码 import——两者只共享同一个自建
PostgreSQL 容器（不同数据库角色、不同 schema）与同一份 `config.Settings`。
"""
