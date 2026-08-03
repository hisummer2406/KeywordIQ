## 1. 后端：类目筛选改多选

- [x] 1.1 `app/table/search/search_schemas.py`：`AnalysisSearchRequest` 移除
      `category_exact`，新增 `categories: list[str] | None = Field(None, max_length=100)`，
      描述写明"命中其一即可、空集合视同不筛"
- [x] 1.2 `app/table/analysis/analysis_crud.py`：`_apply_filters` 改为
      `if req.categories: stmt = stmt.where(DimProduct.category.in_(req.categories))`；
      `request_has_filters` 的字段清单同步换成 `categories`
- [x] 1.3 单元测试（不连库）：`request_has_filters` 对非空 `categories` 返回 True、
      对 `None` 与 `[]` 返回 False；`max_length=100` 超限被 Pydantic 拒绝。
      验证：`uv run pytest test/ -q`
- [x] 1.4 集成测试：在 `test/test_site_scoped_query_integration.py` 加一条多选类目用例
      （传两个类目 → 结果只含这两类，且不越出站点/视图/日期切片）。
      验证：`uv run pytest test/ -q`（本地起库时解开 `TEST_DATABASE_URL` gate）

## 2. 前端：纯函数层

- [x] 2.1 新建 `frontend/src/pages/Analysis/filters.ts`：`FilterDraft` 类型、`emptyDraft`、
      `detectSearchKind`、`RANGE_PRESETS`（排名 / 排名变化 / 点击份额 / 转化份额四组档位）、
      `toSearchBody(draft, scope)`（只带非空字段，空类目数组不传）
- [x] 2.2 `frontend/src/services/analysis.ts`：`SearchBody` 的 `category_exact?: string`
      换成 `categories?: string[]`
- [x] 2.3 新建 `frontend/src/pages/Analysis/filters.test.ts`：ASIN 判定（`B07Z82895W` →
      asin、`screen protector` → keyword、空串 → none）、档位映射到上下界、
      `toSearchBody` 裁掉空值与空类目数组、份额值不做单位换算。
      验证：`pnpm test`

## 3. 前端：展示组件

- [x] 3.1 新建 `ScopeBar.tsx`：站点 / 视图 / 日期 / 检索框（带判定回显后缀）/
      「立即查询」按钮 /「条件已修改」提示。受控组件，不发请求
- [x] 3.2 新建 `CategoryPanel.tsx`：全部类目开关 + 带计数的复选列表 + 按名过滤输入 +
      限高滚动。受控组件，类目清单由外部传入
- [x] 3.3 新建 `FilterPanel.tsx`：四行区间（输入框对 + 快捷档位按钮组，含「全部」档）+
      新词三态 + 品牌 / 商品标题文本框 + 收起展开。受控组件。
      **不含关键词与 ASIN 输入**——那两个维度归作用域区的检索框，一个维度只能有一个入口
- [x] 3.4 新建 `columns.tsx`：从现有 `index.tsx` 搬出 ProTable 列定义，
      移除各列的 `valueType` 搜索配置（筛选已移入过滤面板）

## 4. 前端：页面编排

- [x] 4.1 重写 `pages/Analysis/index.tsx`：`draft` / `applied` 两份状态，
      三段式布局（顶栏 Card + 过滤区 Card 左右两栏 + ProTable），
      ProTable 置 `search={false}`，保留刷新 / 列设置 / 导出 CSV
- [x] 4.2 接线「立即查询」：`applied ← draft` + `actionRef.reload()` 回第 1 页；
      `draft ≠ applied` 时显示提示
- [x] 4.3 接线作用域元数据：站点或视图改变即重拉 `/scope` 与 `/categories`、
      日期跟到最新一期、清空已勾选类目，且**不** reload 表格
- [x] 4.4 导出沿用最近一次生效的查询体（`applied`），确认导的仍是"用户当下看到的那份"

## 5. 前端：页面测试

- [x] 5.1 改写 `pages/Analysis/index.test.tsx`：改条件后未点查询不产生列表请求；
      点「立即查询」发一次且请求体带多选类目与区间；切站点重拉 scope 与 categories
      但不发列表请求；「条件已修改」提示的出现与消失。
      验证：`pnpm test`
- [x] 5.2 跑 `pnpm lint` + `pnpm tsc` + `pnpm test` 全绿

## 6. 收尾

- [x] 6.1 全量回归：`uv run pytest test/ -q`（本地起库跑含集成的版本，见 `local-db` skill）
      + 前端三件套
- [x] 6.2 起本地前后端，浏览器实测：多选类目、区间档位、手输区间取消档位高亮、
      ASIN 判定回显、切站点后提示与表格行为、导出结果与界面一致
- [x] 6.3 更新 `CLAUDE.md` 的 V2 查询层一节：类目筛选由 `category_exact` 改为 `categories`
