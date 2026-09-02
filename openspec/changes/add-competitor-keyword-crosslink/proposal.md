## Why

竞对监控（issue #56）目前只能回答"这个卖家最近上了什么新品"，回答不了"这个新品有没有
抢占我的搜索词"——而后者才是 issue 标题写的"与关键词数据打通"，是这条产品线相对通用选品
工具的独有优势。数据其实已经在库里：`fact_daily`/`fact_weekly` 的 `top1/2/3_product_id`
是每个关键词点击份额前三的商品，与 `competitor_seller_asin`（P3a，issue #56，PR #64）的
竞对在售 ASIN 集合做一次 join 就能连起来，但目前既没有可用的索引（`top2/3_product_id`
没建）也没有筛选入口。

## What Changes

- 事实表补齐 `top2_product_id` / `top3_product_id` 索引（`fact_daily`/`fact_weekly` 各两条，
  与已有的 `top1_product_id`/品牌三列同构），否则下面两条筛选会退化成全表扫描
- 分析查询屏的 ASIN 精确匹配从只命中 `top1_product_id` 扩展为 `top1/2/3_product_id` 任一
  命中（**MODIFIED**，`site-scoped-query`）——把 ASIN 粘进搜索框就能看到它在前三名任一
  位置出现过的全部关键词，不再"排第二名就查不到"
- 分析查询屏新增「竞对新品」筛选维度：命中条件是该行 `top1/2/3_product_id` 对应的 ASIN
  属于当前用户在当前站点关注的卖家们的在售集合（**MODIFIED**，`site-scoped-query`）
- 新增能力 `competitor-keyword-crosslink`：定义"竞对新品命中一个关键词"这件事的语义边界
  （只服务器端时间意义上的"当前在售"，不做历史回溯；只到 ASIN 级，不做品牌级）

**不在本次范围内**：issue 里的方向 C（品牌级：卖家关联品牌 → `top1/2/3_brand_id`）——
`competitor_seller`/P3a 上报体目前都不存品牌名，"卖家→品牌"这层映射没有数据来源，做了
也是恒空筛选。移出本次，待有渠道拿到卖家品牌名后再评估，届时另开变更。

## Capabilities

### New Capabilities
- `competitor-keyword-crosslink`：竞对在售 ASIN 与 ABA 关键词事实表的关联语义
  （命中条件、时间口径、性能约束），供 `site-scoped-query` 的筛选实现遵循

### Modified Capabilities
- `site-scoped-query`：ASIN 精确匹配扩展到三个槽位；新增「竞对新品」筛选维度

## Impact

**影响日报与周报两条路径**（`fact_daily`/`fact_weekly` 结构完全对称，索引与筛选逻辑
在两表上各做一份）。

- 数据库：一条手写 Alembic 迁移，父表上 `CREATE INDEX IF NOT EXISTS`（元数据操作，秒级；
  已装满的库需要先按 `d3e4f5a6b7c8` 那条迁移注释里的 ONLY + CONCURRENTLY + ATTACH 流程
  在线补建，避免长时间持锁）
- 后端：`app/table/analysis/analysis_crud.py`（筛选语句）、`analysis_schemas.py`（请求
  字段）、`analysis_service.py`（作用域解析，取当前用户在当前站点关注的卖家 ASIN 集合）
- 前端：`frontend/src/pages/Analysis/`（`FilterPanel` 加一个筛选开关，`filters.ts` 加
  对应字段与 `toSearchBody` 映射）
- 依赖：读取 `competitor_seller_asin`（P3a，issue #56/PR #64）与 `user_seller_watch`
  （issue #56 P0）——**这两张表目前可能没有真实数据**（P3a 的浏览器插件客户端尚未开发，
  见下方风险说明），筛选功能上线后短期内结果可能为空，这不是缺陷，是数据源尚未就绪
