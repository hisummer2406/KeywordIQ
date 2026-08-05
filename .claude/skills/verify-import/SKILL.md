---
name: verify-import
description: 用真实报表 CSV 跑一遍完整的三段式导入并断言落库结果。当改动了 importer / import_service / 分区 / 派生值计算，或需要确认导入管线端到端仍然正确时使用。
---

# 端到端导入验证

单元测试用几十行的小夹具，验的是逻辑分支；这个流程用**真实体量的报表文件**（US 日报
约 40 万行）跑完整管线，验的是 `COPY` 装载、分区路由、派生值关联、统计回写这一串
在真实数据形状下还成不成立。改过导入路径就该跑一次——它抓得住单元测试结构上抓不到的
东西：列数漂移、编码问题、上一期关联落空、分区没建。

先按 [local-db](../local-db/SKILL.md) 把测试库准备好。

## 数据来源

优先用 `docs/table/` 下的真实报表（**未入库，本地才有**，命名形如
`US_热门搜索词_简单_Day_2026_07_18.csv`）。文件名是归属三元组的权威来源——
站点、周期、报告日期都从它解析，界面只做回显。

本地没有这些文件时退回合成数据：

```bash
uv run python scripts/generate_synthetic_data.py --help
```

## 跑一次导入

`OSS_ENABLED=false` 时走本地文件退路，`LocalFileSource` 直接读磁盘，不需要 OSS：

```python
# uv run python - <<'EOF'
from datetime import date
from app.table.upload.import_source import LocalFileSource
from app.table.upload.import_service import run_import

batch = run_import(
    site="US", period="DAY", report_date=date(2026, 7, 18),
    source=LocalFileSource("docs/table/US_热门搜索词_简单_Day_2026_07_18.csv"),
    original_filename="US_热门搜索词_简单_Day_2026_07_18.csv",
)
print(batch.status, batch.total_rows, batch.written_rows, batch.error_message)
# EOF
```

`run_import` **不抛异常**（它是后台任务），失败信息落在 `batch.status` 与
`batch.error_message` 里。所以「脚本没报错」不等于「导入成功」——必须读 `status`。

## 断言清单

跑完逐项核对，任何一项不符都说明管线有问题：

1. **批次状态** `import_batch.status == 'COMPLETED'`，且 `written_rows` 与文件数据行数
   吻合（`skipped_rows` 应有明确原因：列数不符或搜索词为空）。
2. **事实数据落在正确分区。** 查 `fact_daily` 在该 (site, report_date) 上的行数等于
   `written_rows`。分区路由错了的表现是行数为 0 而导入却"成功"。
3. **派生值算定。** 排名、上期排名、排名变化、是否新词都是**写入时算定**的，事实表
   只追加、不回填。抽查若干行：首次导入某站点时 `is_new` 应为真、上期排名应为空；
   导入连续两期后，第二期应能关联到上一期。
4. **统计回写。** `site_data_stats` 中该 (site, period, report_date) 的 `row_count`
   等于事实表实际行数——分页总数和日期选择器都读它，不一致会让界面显示错误的总数。
5. **类目物化视图刷新。** `category_stats` 中该站点/周期有数据，否则类目下拉是空的。

## 重复导入

同一期重复导入是**唯一**允许删除事实数据的场景：先删该切片再写。重复导入后
第 2、4 两项的数字应当不变（而非翻倍）——翻倍说明去重逻辑没生效。

## 灌进去的数据活不过下一次 pytest

`test_import_service_integration.py` 与 `test_oss_ingestion_integration.py` 的夹具会
`TRUNCATE` 全部业务表（fact/dim/stats/batch）——它们测的是 `run_import`，而后者内部
提交，回滚兜不住，只能靠 TRUNCATE 收尾。

所以这里灌进去的真实报表，**下一次 `git commit`（提交门禁会跑全量 pytest）就没了**，
没有任何提示。别把它当成"手上有一份真实数据可以反复用"：

- 验证要一次跑完，断言结果记下来，不要指望回头再查库
- 真要留着反复用，换个库名（如 `keywordiq_verify`），别放 `keywordiq_test`

## 别在这里做的事

- 不要为了跑通而手工 `INSERT` 事实表或手工建分区。分区由
  `ensure_fact_partitions` 在写入前保证，绕过它就等于没验到这一步。
- 不要用生产库。这个流程会写入并可能删除事实切片。
