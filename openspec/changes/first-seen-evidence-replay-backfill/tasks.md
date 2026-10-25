## 1. 规格与文档

- [x] 1.1 `keyword-first-seen` 三条 Requirement 的 delta（证据重放、完整性校验、撤销）
- [x] 1.2 README 运行环境改为实际部署

## 2. 生产执行记录（2026-10-22，用户在 Navicat 手工执行 SQL，非脚本）

- [x] 2.1 只读盘点：DE/GB/US 日报 2026-09-14~10-19 共 36 天，周报 2026-09-12~10-17 共 6 周，无缺期、无已清理分区；事实表最早期次均等于最早 COMPLETED 批次（GB 日报有一天重导，37 批次）
- [x] 2.2 回填前 `dim_keyword` 全为 UNKNOWN：DE 744,952 / GB 634,013 / US 4,161,934
- [x] 2.3 回填后：DE 最早可观测 465,386 + 导入创建 279,566；GB 411,053 + 222,960；US 2,718,207 + 1,443,727；无剩余 UNKNOWN；三站新 B 均为 2026-09-14
- [x] 2.4 抽样核验：19 个「导入创建」词，首次日期与事实表最早期次逐个相等；「最早可观测」未抽样
- [ ] 2.5 `VACUUM (ANALYZE) analysis.dim_keyword`（待低峰执行）；确认无误后删除 `analysis.bak_first_seen_143`
- [ ] 2.6 市场分析页浏览器验收（历史日期是否可见首次入榜数）——人工

## 3. 未完成（对应 #143 验收条件，SQL 绕过了脚本）

- [x] 3.1 `scripts/backfill_first_seen.py` 支持证据重放（`--replay`）、完整性校验、`--dry-run`、复用导入的比较键（`importer.first_seen_advance_sql`）、保存原状态与 `--undo`（回滚表列与 `bak_first_seen_143` 相同，可用于撤销 #143 手工回填）；文件头口径与 2C4G 已改
- [x] 3.2 真库重放一致性测试（`test/test_backfill_first_seen_integration.py`：逐期乱序导入 vs 清空后重放；最早期被清理、漏登记两种拒绝；重复零更新；撤销不误伤；回滚表保留最初原状态）。2026-10-25 在 keywordiq_test 跑过，并做了三处反向变异（重放不产出导入创建、校验恒通过、撤销不比对新值），对应用例均失败
- [x] 3.3 残留表述核对（2026-10-25）：`CLAUDE.md`、`AGENTS.md`、`.claude/`、`openspec/guides` 无；主 spec 的旧表述由归档本变更时替换；归档的 `first-seen-keyword-history` design 是历史记录不改
- [ ] 3.4 发现：三站日报 2026-10-06 当日首次入榜数突增（US 5,240，其余日约 1,000~2,000），原因未查
