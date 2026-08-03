# 分析查询屏改造为三段式布局

**影响路径**：查询侧的**日报与周报两条路径**（日视图与周视图共用同一张查询屏）。
**不涉及**导入路径、归档路径与鉴权。

## Why

现行分析查询屏把三类性质不同的东西挤在一起：作用域三元组（站点/视图/日期）塞在
ProTable 的 `headerTitle` 里，筛选条件散落在 ProTable 自带的搜索表单与各列的
`valueType` 中。后果有三：

1. **类目筛选只能选一个**。`category_exact` 是单值精确匹配，而"在若干个类目里找词"
   是选品的常规动作，用户只能一个个类目分别查、自己在脑子里合并结果。
2. **条件改一处就发一次请求**。ProTable 的搜索表单是即时提交的，用户设区间下限时
   就已经打出一次查询，设上限再打一次。在千万行切片上每次都是真查询。
3. **筛选项被藏在列设置里**。「排名区间」「份额区间」这些后端早已支持的筛选
   （`ranking_min/max`、`click_share_min/max` 等）在界面上根本没有入口——后端能力
   齐备，界面没暴露。

对标卖家精灵的选品屏，它把这三件事分成了三个明确的区域：顶部定"查哪份数据"，
中部定"筛哪些行"，底部是结果。区域边界与语义边界一致，用户不需要在工具栏、
搜索表单、列设置三处来回找条件。

## What Changes

### 查询屏（前端）

- 页面重排为三段：
  - **顶部作用域栏**：站点 / 视图（日·周）/ 日期 / 单一搜索框（ASIN 或关键词）/「立即查询」
  - **中部过滤区**：左侧类目多选面板（带计数、可滚动、可按名过滤），右侧结果过滤面板
    （区间输入 + 快捷档位）
  - **底部结果表格**：ProTable 关闭自带搜索表单，保留刷新 / 列设置 / 导出 CSV
- **查询触发改为显式**：所有筛选条件与作用域的改动先进入草稿态，点「立即查询」才生效。
  表格自身的排序、翻页、每页条数不受此约束，仍即时生效。
- **单一搜索框**按输入格式判定检索目标：形如 `B0` + 8 位字母数字走 ASIN，否则走关键词；
  界面回显判定结果。
- 暴露后端已支持但界面未提供的筛选：排名区间、排名变化区间、点击份额区间、
  转化份额区间、品牌、商品标题、是否新词。区间项各配一组快捷档位。
  关键词与 ASIN 归顶部检索框，过滤区不重复提供入口。
- 单文件的查询屏拆分为作用域栏、类目面板、过滤面板、列定义与纯函数条件转换五部分。

### 查询接口（后端）

- **BREAKING**：`AnalysisSearchRequest.category_exact`（单值精确匹配）由
  `categories`（类目值列表，命中其一即可）替代。旧字段移除，不保留双写——它唯一的
  调用方是同仓前端，在同一次变更内一并更新。

## Capabilities

### New Capabilities

- `analysis-screen-layout`: 分析查询屏的交互契约——区域划分与各区职责、查询的显式触发
  语义（什么改动即时生效、什么改动要等按钮）、单一搜索框的目标判定、快捷档位与手输
  区间的等价关系、未生效改动的可见性。

### Modified Capabilities

- `site-scoped-query`: 类目筛选由"精确匹配单个类目"改为"匹配所选类目集合中的任意一个"，
  且空集合视同不筛。作用域边界（单站点、单视图、单日期）不变。

## Impact

**后端**

- `app/table/search/search_schemas.py`：`AnalysisSearchRequest` 移除 `category_exact`，
  新增 `categories: list[str] | None`
- `app/table/analysis/analysis_crud.py`：`_apply_filters` 改用 `IN` 条件，
  `request_has_filters` 同步换字段
- `POST /api/analysis/search` 与 `POST /api/analysis/export` 的请求体（两者共用同一 DTO）

**前端**

- `frontend/src/pages/Analysis/index.tsx`：由单文件重排并拆分
- `frontend/src/services/analysis.ts`：`SearchBody` 的类目字段
- `frontend/src/pages/Analysis/index.test.tsx`：既有用例随交互模型调整

**不影响**

- 数据模型、分区、导入管线、保留清理
- 鉴权与权限点（仍是 `analysis:view` / `analysis:export`）
- 错误契约与 ProTable 响应契约
