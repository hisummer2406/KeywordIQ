# ops-backup Specification

## Purpose
TBD - created by archiving change add-feishu-ops-bot. Update Purpose after archive.
## Requirements
### Requirement: 备份失败准确报告
备份流程的任一阶段（dump、导出、上传）失败 SHALL 被记录为失败并标注具体失败阶段，
MUST NOT 将部分完成的备份报告为成功。

#### Scenario: 上传阶段失败
- **WHEN** `pg_dump` 与容器内导出均成功，但上传 OSS 失败
- **THEN** 备份记录状态为失败，失败阶段标注为上传，且不生成一条看似成功的备份记录

### Requirement: 备份产物核对与通知
每次备份完成 SHALL 记录产物标识（OSS object key）、时间、大小；系统 MUST 能查询最近
备份结果并在完成或失败时发送通知。

#### Scenario: 查询最近备份
- **WHEN** 用户查询最近一次备份
- **THEN** 系统返回该次备份的时间、大小、产物标识与状态

### Requirement: 隔离环境恢复验证
系统 MUST 提供可在隔离环境执行的恢复验证步骤；生产数据库恢复 MUST NOT 作为飞书聊天
指令开放。

#### Scenario: 用户尝试通过飞书发起恢复
- **WHEN** 用户发送任何形式的"恢复数据库"指令
- **THEN** 系统不提供该操作，仅指向文档化的隔离环境恢复步骤

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

