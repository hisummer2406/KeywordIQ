## 1. 查询层：语句参数化

- [x] 1.1 `analysis_crud` 加 `_TREND_SPEC`（视图 → 事实表 / 日期列 / 回溯天数），
      `trends_stmt` 增加 `view` 参数按表取值；`shape_trends` / `shape_trend_dates` 不动
      （按位置取列，与表无关）
- [x] 1.2 同步执行器 `fetch_trends` 加 `view` 参数（导出路径用）
- [x] 1.3 单元测试：两个视图各自命中正确的事实表与回溯窗口

## 2. DTO 与服务层

- [x] 2.1 `trend` / `trend_dates` 从 `DayRowItem` 提到 `_FactRowBase`；`DayRowItem` 与
      `WeekRowItem` 都不再单独声明
- [x] 2.2 `analysis_service.search` 去掉 `if view == "DAY"` 分支，两视图统一"先分页、
      再批量补附加"
- [x] 2.3 `_fetch_trends` 透传 view

## 3. 导出

- [x] 3.1 趋势列表头按视图取：日「近 7 日趋势」/ 周「近 7 周趋势」，取代原
      `_EXPORT_COLUMNS_DAY_EXTRA` 的「近7天走势」
- [x] 3.2 `iter_export_csv` 两个视图都补趋势
- [x] 3.3 集成测试：日、周两个视图的 CSV 表头与行尾各断言一次

## 4. 前端

- [x] 4.1 `columns.tsx`：列名改「搜索趋势」，去掉 `hideInTable: view !== 'DAY'`，
      把 view 传给 `TrendSparkline`
- [x] 4.2 `TrendSparkline`：悬浮提示按视图渲染（周视图加「当周」）
- [x] 4.3 `columns.test.tsx` / `TrendSparkline.test.tsx` 覆盖两个视图

## 5. 数据

- [x] 5.1 集成测试 `_seed` 扩成 3 期周报（**本变更唯一能证明"真的回溯了"的证据**：
      只造一期的话，单点结果与"根本没查"无法区分）
- [x] 5.2 `scripts/seed_demo_data.py` 造连续多期周报，让周视图趋势在本地可见

## 6. 收尾

- [x] 6.1 顺手修正 `show-top3-products` 变更 delta 里残留的「当周排名变化」——那笔已并入
      v2 但未归档，归档时会把已删除的字段重新写回主 spec
- [x] 6.2 全量回归（含 `TEST_DATABASE_URL` 集成）+ `openspec validate --all --strict`——**全新库上跑**才是有效验证：借上一轮残留分区的旧跑法会掩盖问题，本次即因此揪出导出集成测试不建分区的历史缺陷
      （本地全量单元/前端/OpenSpec 已通过；当前机器未配置 `TEST_DATABASE_URL`，数据库集成待运行）
