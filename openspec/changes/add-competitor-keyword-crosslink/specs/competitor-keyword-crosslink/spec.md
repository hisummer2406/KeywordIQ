## ADDED Requirements

### Requirement: 竞对新品命中一个关键词的判定只到 ASIN 级、只看当前在售集合

「某个关键词被竞对新品命中」SHALL 定义为：该关键词在当前查询的事实切片中，`top1`/`top2`/
`top3` 商品槽位（点击份额前三）里至少有一个的 ASIN，出现在当前用户当前站点关注的卖家的
**当前在售 ASIN 集合**（`competitor_seller_asin`）中。

判定 MUST NOT 做历史回溯——不判断"这个 ASIN 是不是曾经在该竞对名下出现过又下架"，只看
调用查询这一刻的在售集合。这与 `competitor-asin-ingest` 能力"不做下架判定"的既有边界
一致：在售集合本身随每次上报增量更新，判定口径没有必要比数据源本身更强。

判定 MUST NOT 扩展到品牌级（卖家关联品牌 → 该品牌是否出现在 `top1/2/3_brand_id`）——
`competitor_seller`/`competitor_asin_report` 均不存储卖家的品牌名，"卖家 → 品牌"这层
映射没有数据来源；在没有数据的前提下实现品牌级判定，产出的只会是一个恒空的筛选，比不做
更容易让用户误以为"我关注的这几个品牌都没有关联词"。

#### Scenario: 命中任意一个槽位即算命中

- **WHEN** 某关键词的 `top2_product_id` 对应的 ASIN 属于用户关注的某个卖家的在售集合，
  `top1`/`top3` 不属于
- **THEN** 该关键词判定为命中

#### Scenario: 已下架但仍在关注集合外的商品不参与判定

- **WHEN** 某 ASIN 此前属于某竞对的在售集合，但最新一次上报里已经不再出现（本能力不做
  下架判定，该 ASIN 的历史行仍留在 `competitor_seller_asin` 中）
- **THEN** 判定仍按 `competitor_seller_asin` 当前实际持有的行判定，不额外排除

#### Scenario: 未关注任何卖家时不产生命中

- **WHEN** 用户在当前站点没有关注任何卖家
- **THEN** 该站点下没有任何关键词被判定为竞对新品命中

### Requirement: 竞对新品的私有性与站点边界跟随既有关注关系约定

判定使用的"当前用户关注的卖家"集合 SHALL 按 `user_seller_watch` 的 `user_id` 与查询
当前站点共同限定，MUST NOT 跨用户、MUST NOT 跨站点。

#### Scenario: 不同用户看到不同的命中结果

- **WHEN** 用户甲关注了卖家 A，用户乙关注了卖家 B，A、B 的在售集合完全不重叠
- **THEN** 用户甲与用户乙对同一批关键词施加竞对新品筛选，得到不同的命中结果

#### Scenario: 跨站点关注不影响本站点判定

- **WHEN** 用户在 `US` 站点关注了卖家 A（`site=US`），并在 `GB` 站点关注了卖家 B（`site=GB`）
- **THEN** 查询 `US` 站点时只用卖家 A 的在售集合判定，不受卖家 B 影响
