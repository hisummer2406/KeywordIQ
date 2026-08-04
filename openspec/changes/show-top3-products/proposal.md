## Why

事实表从建模之初就存了点击量前三的商品（`top1/top2/top3_product_id` 与各自的点击/转化份额），
导入链路也一直在写这三个槽位，但查询层只 JOIN 了 top1，界面只展示 top1。用户看到的是
「这个词点击量最高的那个商品」，而选品判断真正需要的是「这个词的点击集中在哪几个商品、
哪几个品牌手里」——只有一个商品时无法判断这个词是被单品垄断还是多家分食。

数据已经在库里，缺的只是把它接出来。这是本变更成本低的原因，也是它拖着不做没有道理的原因。

**影响范围：日报与周报两条路径**——两张事实表结构一致，槽位字段同名，查询层与界面对两者
共用同一套代码。

## What Changes

- 查询层 `_base_query` 增加 top2/top3 的商品维度关联，行数据从 top1 的 6 个商品字段扩展到
  三个槽位共 18 个（每槽位 ASIN / 标题 / 品牌 / 类目 / 点击份额 / 转化份额）。
- 结果表格以卡牌列呈现前三：新增「点击前三 ASIN」列（三张卡横排，每张含缩略图、ASIN、
  点击份额、转化份额）与「点击前三品牌」列（竖排三个品牌）；**移除**原有的 品牌、ASIN、
  商品标题、点击份额、转化份额 五个平铺列——它们展示的就是卡牌里的 Top1，留着即为同一个
  数在一行里出现两次。
- 商品缩略图由 ASIN 拼出亚马逊图床地址、由用户浏览器直连加载；不新增图片字段、不自建抓取
  与缓存。加载失败回退为占位方块。
- CSV 导出列从 top1 的 6 列扩展到三个槽位共 18 列（合计约 24 列），保持「导出的就是用户当下
  看到的那份数据」这条既有约定。
- **筛选与排序的口径不变**：品牌、类目、ASIN、商品标题、点击份额、转化份额六个条件与全部
  排序字段继续只作用于 top1。界面上前三可见而筛选只认第一名，这是一处刻意保留的口径差，
  必须在界面明示，否则用户会拿卡牌里第二名的品牌去筛却筛不出这一行。

不做（各自单独评估，不在本变更内）：筛选扩到前三任一槽位命中；类目列是否也合并为「前三类目」。

## Capabilities

### New Capabilities

无。本变更是既有能力的字段扩展与呈现调整，不引入新的能力域。

### Modified Capabilities

- `site-scoped-query`: 行字段从「top1 商品」扩展为「点击量前三商品」；新增导出列覆盖三个槽位；
  同时把「筛选与排序只作用于 top1」从隐含实现提升为明文要求。
- `analysis-screen-layout`: 结果表格的商品信息由平铺列改为卡牌列；新增「展示口径宽于筛选口径
  时必须在界面标注」的要求。

## Impact

**后端**

- `app/table/analysis/analysis_crud.py`：`_base_query` 增加两个 `aliased(DimProduct)` 的 LEFT JOIN
  （按 `product_id` 主键关联）。`_apply_filters` 与 `SORTABLE_FIELDS` 一字不改——筛选条件继续
  绑在 top1 的那个 join 上，新 alias 对筛选不可见。
- `app/table/search/search_schemas.py`：`_FactRowBase` 增加 12 个字段（top2/top3 各 6 个）。
- `app/table/analysis/analysis_service.py`：`_EXPORT_COLUMNS_BASE` 扩展列清单。导出走
  `_base_query`，字段自动跟随；行数与 `yield_per` 分块不变，故流式导出的内存界与
  `EXPORT_ROW_CAP` 均不受影响。

**前端**

- `frontend/src/services/analysis.ts`：`AnalysisRow` 增加 12 个字段。
- `frontend/src/pages/Analysis/columns.tsx`：列定义重构，`buildColumns` 的入参从 `view` 扩展为
  同时接收 `site`（拼图片地址需要）。
- 新增商品卡牌组件与 `productImageUrl(site, asin)` 纯函数（站点 → 图床域名 + marketplace 码的
  映射集中一处，不散进组件）。

**不受影响**

数据库结构（字段已存在，无迁移）、导入链路、分区与保留策略、鉴权、错误契约。

**已知风险**

图床地址里的 marketplace 码约定（US / GB / DE 各不同）无法靠 code review 确认，错了的表现是
图片全部回退成占位方块。必须在三个站点各取真实 ASIN 实测。
