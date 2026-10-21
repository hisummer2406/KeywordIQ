---
name: verify-import
description: 验证 CSV 导入的真实体量、日周分区、批次统计、首次证据与重复乱序一致性；用于修改导入管线后的端到端检查。
---

# 端到端导入验证

输入：变更范围、目标本地验证库、站点/日周周期、报表文件及期次。输出：输入规模、实际批次、
分区/统计/首次证据断言、重复与乱序比较、耗时和未覆盖项。具体口径引用现有规格，不在这里再写公式。

## 准备

- 先执行 [local-db](../local-db/SKILL.md) 的目标身份与占用检查；导入会提交并可能删除同一期切片，禁止生产目标。
- 小样本集成回归可用共享测试库；需要保留的真实体量报表用已协调的一次性验证库，不能随后被全量夹具清空。
- 先读 [导入规则](../../rules/ingestion.md)、[分析规则](../../rules/analysis.md) 及本次相关 [规格导航](../../../openspec/guides/ingestion.md)。
- 优先使用有权使用的本地CSV，不提交原始报表。没有时按现有 importer 测试夹具的数据格式生成合成CSV；
  报告是合成还是实际报表、行数/字节数、文件摘要与目标期次。小样本不能冒充真实体量。
- `scripts/generate_synthetic_data.py` 是直接写库的种子工具，**不生成输入CSV、不经过完整COPY入口**；不能用其结果替代本验证。
- 用 `importer.parse_report_filename`/`infer_period` 及站点注册验证文件名归属；无法解析时按当前入口契约处理，
  不让传给 run_import 的手填日期与文件名矛盾。日/周报受影响的路径分别准备，不把日报结果外推到周报。

## 运行与核对

在已核实本地库、隔离配置与输入文件后，从仓库根目录调用现有 `run_import`，使用 LocalFileSource；
调用签名参考 [编排层集成测试](../../../test/test_import_service_integration.py)。不要手工插入事实或创建分区绕过入口。
本地文件验证本身不要求访问OSS，不擅自改共享 .env 或生产开关。

- 读取返回批次并按ID回查持久化状态。成功必须 COMPLETED；FAILED读错误，QUEUED/PROCESSING不是成功。
  处理 ImportRejected 等显式异常；不能以“函数没抛异常”推断成功，也不能只依赖可能未写成的失败状态。
- 日表用 report_date，周表用 report_date_week。按站点/周期/期次核事实行数、实际分区归属与 written_rows；
  total_rows、拒绝/跳过/重复等计数按实际入口语义核对，不假定输入行数必等于写入数。
- 检查该切片 site_data_stats 的原始行数和类目/品牌统计普通表；不要称作物化视图刷新。
  可见列表总数可能因黑名单/筛选减少，不能拿原始统计强行与可见总数相等。
- 检查上一期关联、排名变化、掉榜/增长派生。缺期需有理由；阈值变更不能只验证新一期而漏掉回填影响。
- 首次证据检查 dim_keyword 与查询派生的 first_listed；不再断言事实 is_new。
  新站点第一期是基线、不可判断，不算全体首次；UNKNOWN/最早可观测不能冒充确认首次。
- 重复同一期：事实/统计和首次证据不翻倍、不漂移；乱序早期/后期与日周并列：最终证据和每期结果与顺序导入一致。
  这些比较需要相同初始状态的一次性目标；不靠清空共享库制造初始状态，也不要求批次历史ID/条数不变。

先跑与改动匹配的已有用例，再做体量验证；现成入口：

```bash
uv run pytest test/test_import_service_integration.py test/test_importer_integration.py test/test_first_seen_integration.py -q -ra
```

首次回填涉及 `test/test_backfill_first_seen_integration.py`；增长涉及 `test/test_growth_calc_integration.py`。
测试夹具会写入/清理数据，不能在要保留报表的库里随后跑这组测试。配置、TEST_DATABASE_URL与实际导入目标须明确分开记录。

## 证据与停止条件

记录每条断言的范围、预期/实际和状态，附命令退出码；日/周各列完成情况，写明是否真正跑到COPY/分区/统计收尾。
遇到目标不明、库被占用、锁拒绝、输入缺失或断言失败，保存已获得证据并停止相关部分，不自动重导或改数据“修绿”。
只验证本地文件入口不代表STS/CORS、浏览器直传或生产通过；这些需要另行授权的外部链路验证。
