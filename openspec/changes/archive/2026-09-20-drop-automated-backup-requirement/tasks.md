## 1. 规格对齐

- [x] 1.1 `ops-backup` delta：移除「定时与手动备份」「备份新鲜度与保留期检查」「避免与既有调度重复」，新增「仅手动触发备份」「手动备份的保留期清理」。
- [x] 1.2 `ops-monitoring-alerting` delta：状态查询、定时巡检、每日摘要去掉备份。
- [x] 1.3 `containerized-deployment` delta：移除自动化备份要求，新增「不提供自动化备份」。
- [x] 1.4 `openspec validate` 通过。
