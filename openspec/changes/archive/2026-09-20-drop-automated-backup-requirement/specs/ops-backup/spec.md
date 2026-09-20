## REMOVED Requirements

### Requirement: 定时与手动备份
**Reason**: 2026-09-20 项目负责人决定不做数据库备份。"每日自动触发一次全量备份"不再成立，仅保留手动触发，由下面新增的「仅手动触发备份」承接。
**Migration**: 无。ops-bot 的每日 `BACKUP` 调度任务已于 PR #113 移除。

### Requirement: 备份新鲜度与保留期检查
**Reason**: 不做定时备份，"距上次成功备份多久"没有可判据的期望值；从未备份过会让该告警每轮巡检都触发，是纯噪声。保留期清理的部分由下面新增的要求承接。
**Migration**: 无。已落库的 `backup-overdue` 告警记录由 ops-bot 启动时按 `RETIRED_ALERT_KEYS` 静默关闭（PR #114）。

### Requirement: 避免与既有调度重复
**Reason**: 该要求的前提是"内置调度是备份触发的唯一权威入口"。系统不再内置备份调度，也就不存在与独立 cron 双跑的问题。
**Migration**: 无。

## ADDED Requirements

### Requirement: 仅手动触发备份
系统 SHALL 支持通过飞书指令手动触发一次全量备份（复用
`scripts/backup_selfhosted_pg.py`）；系统 MUST NOT 内置定时/自动触发备份。

#### Scenario: 手动触发备份
- **WHEN** 授权用户发送"立即备份"指令
- **THEN** 系统创建一个备份任务并立即开始执行，不需要经过重启/发布那样的人工确认卡片

#### Scenario: 不自动触发备份
- **WHEN** 无人发送备份指令，无论经过多长时间
- **THEN** 系统不产生任何备份任务，也不因为"长时间没有备份"而告警

### Requirement: 手动备份的保留期清理
超过配置保留期的旧备份产物 MUST 在一次手动备份成功完成后被清理，清理动作 MUST 记录在案。

#### Scenario: 超过保留期的备份被清理
- **WHEN** 一次手动备份成功完成
- **THEN** 系统顺带清理超过保留期的旧备份产物，并记录本次清理了哪些对象
