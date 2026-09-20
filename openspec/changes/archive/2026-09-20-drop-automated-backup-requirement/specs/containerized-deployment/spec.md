## REMOVED Requirements

### Requirement: 自建 PostgreSQL 有可运行的备份
**Reason**: 2026-09-20 项目负责人决定不做数据库备份。该要求原本是"RDS 自动备份消失后的等价替代"，此等价性因此不再成立，由下面新增的要求明示其后果。
**Migration**: 无代码迁移。ops-bot 的每日定时备份已于 PR #113 移除；手动备份指令与 `scripts/backup_selfhosted_pg.py` 保留，可按需取一份一次性副本。

## ADDED Requirements

### Requirement: 自建 PostgreSQL 不提供自动化备份
自建 PostgreSQL 部署 SHALL NOT 依赖自动化备份作为数据保护手段。数据目录位于宿主机
bind mount，其可用性由宿主机与磁盘本身承担；部署与运维材料 MUST NOT 暗示存在自动备份或可承诺的恢复点目标。

#### Scenario: 宿主机或数据盘丢失
- **WHEN** 宿主机或数据盘发生不可恢复的损坏
- **THEN** 自上一次手动备份（若有）之后的数据无法恢复；该后果是已被接受的取舍，不视为部署缺陷

#### Scenario: 需要一份一次性副本
- **WHEN** 运维人员在重大变更前希望留一份副本
- **THEN** 可通过 ops-bot 的手动备份指令取得，副本存放在应用所在机器之外的独立存储
