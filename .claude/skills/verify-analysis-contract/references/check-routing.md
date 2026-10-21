# 检查路由：按改动选择，先读断言

以下路径相对仓库根目录，测试文件可能继续演进；文件存在和历史跑绿不等于本次实际覆盖。

| 影响 | 现有后端入口 | 前端/额外入口 |
|---|---|---|
| 站点/日周、筛选/数量、窗口与分页 | test/test_site_scoped_query_integration.py；test/test_window_scoped_rows_integration.py；test/test_analysis_filters_unit.py；test/test_row_segments_unit.py | frontend/src/pages/Analysis/filters.test.ts；frontend/src/pages/Analysis/exportAndPaging.test.tsx |
| 关键词模式与字面符号 | test/test_keyword_match_unit.py；test/test_keyword_match_integration.py | frontend/src/pages/Analysis/filters.test.ts |
| 导出流、范围/限额、日周趋势 | test/test_analysis_export_unit.py；test/test_analysis_export_integration.py | frontend/src/pages/Analysis/exportAndPaging.test.tsx |
| 首次证据、乱序/基线、首次数量 | test/test_first_seen_integration.py；test/test_backfill_first_seen_integration.py | frontend/src/pages/Analysis/columns.test.tsx |
| 隐藏类目与可见计数 | test/test_hidden_categories_unit.py；test/test_hidden_categories_integration.py | 按diff核对筛选、下拉与导出是否共用基线 |
| 私有竞对筛选/权限 | test/test_analysis_competitor_permission_unit.py | frontend/src/pages/Analysis/savedMode.test.ts；frontend/src/pages/Analysis/filters.test.ts |
| 词库与公共排名历史 | test/test_keyword_library_integration.py；test/test_keyword_trend_integration.py；test/test_keyword_trend_api_unit.py | 有真实UI改动时补对应页面浏览器检查 |
| 保存条件、恢复、展示参照 | 按接口影响选择上述组 | frontend/src/pages/Analysis/stateCache.test.ts；frontend/src/pages/Analysis/savedMode.test.ts；frontend/src/pages/Analysis/columns.test.tsx |

后端从根目录 `uv run pytest <选定文件> -q -ra`；前端在frontend目录 `pnpm test <相对frontend的选定文件>`。
这些尖括号表示执行前替换的选定范围，不原样交给shell。测试目标仍须遵守仓库提交前完整门槛。

## 典型路由与能力边界

- “改了关键词匹配，担心导出不一致”：组合匹配、筛选计数、导出与前端条件转换；不是只跑纯分词测试。
- “周报首次数量为零”：先核期次/基线与可判断状态；组合首次证据、日周作用域及展示，不能把不可判断当零。
- “条件恢复后403”：核账号权限及保存条件规范化，组合私有筛选权限与前端恢复；不删除服务端鉴权来让测试通过。
- “数据都是空的”：先确认有输入与目标环境；没有TEST_DATABASE_URL而全跳过时，仅交付未执行状态。
