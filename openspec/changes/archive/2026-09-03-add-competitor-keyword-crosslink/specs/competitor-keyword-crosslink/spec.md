## ADDED Requirements

### Requirement: 竞对新发现命中一个关键词的判定只到 ASIN 级、只看近期新发现

「某个关键词被竞对新发现命中」SHALL 定义为：该关键词在当前查询的事实切片中，`top1`/`top2`/
`top3` 商品槽位（点击份额前三）里至少有一个的 ASIN，是当前用户当前站点关注的卖家**近期
新发现**的 ASIN——判据与卖家清单「近 7 日新发现」（`watch_crud.NEW_DISCOVERY_WINDOW_DAYS`）
同构并复用同一个窗口常量：`first_seen_at > baseline_at`（基线批次不算新发现）且
`first_seen_at` 落在窗口内。

判定 MUST NOT 把"该卖家曾经上报过这个 ASIN"本身当作命中条件（PR #66 复审 P1 定案）。
`competitor_seller_asin` 是被动、增量采集的历史观察集合，不做下架删除；不限定时间的话，
这个开关会命中卖家有史以来上报过的全部商品，也与卖家清单页"近 7 日新发现"的口径不一致
——同一份数据源，两处不该在回答不同的问题。

界面标签 SHALL 是「竞对新发现」而非「竞对新品」（PR #66 三次复审 P2）。这不是措辞偏好：
被动采集证明不了商品何时上架，只能证明我们何时见到它，因此「上新」及其近义词（含"新品"）
在这条数据线上一律不用——与 `competitor-storefront-watch` 对 Amazon 侧上新流的措辞要求
同一条纪律。请求字段沿用 `competitor_new`（改标识符要动 schema、前端、spec 三处，
收益不抵成本），但它读作 new **discovery**。

判定 MUST NOT 扩展到品牌级（卖家关联品牌 → 该品牌是否出现在 `top1/2/3_brand_id`）——
`competitor_seller`/`competitor_asin_report` 均不存储卖家的品牌名，"卖家 → 品牌"这层
映射没有数据来源；在没有数据的前提下实现品牌级判定，产出的只会是一个恒空的筛选，比不做
更容易让用户误以为"我关注的这几个品牌都没有关联词"。

#### Scenario: 命中任意一个槽位即算命中

- **WHEN** 某关键词的 `top2_product_id` 对应的 ASIN 是用户关注的某个卖家近期新发现的，
  `top1`/`top3` 不是
- **THEN** 该关键词判定为命中

#### Scenario: 基线批次不算新发现

- **WHEN** 某卖家首次被关注、首次上报，该批 ASIN 的 `first_seen_at` 与
  `competitor_seller.baseline_at` 落在同一事务、严格相等
- **THEN** 这批 ASIN 不参与竞对新发现判定——它们回答的是"我们第一次见到这家店在卖什么"，
  不是"新发现"

#### Scenario: 超出窗口的历史新发现不算

- **WHEN** 某 ASIN 确实晚于该卖家的 `baseline_at` 才被上报（是真正的新发现），但发现时刻
  已经超出 `NEW_DISCOVERY_WINDOW_DAYS`
- **THEN** 该 ASIN 不参与竞对新发现判定——"新发现"是有时效的信号，不是"只要晚于基线就永远算"

#### Scenario: 未建立基线的卖家不产生命中

- **WHEN** 某卖家还没有任何上报记录，`baseline_at` 为 `NULL`
- **THEN** 该卖家名下没有任何 ASIN 参与竞对新发现判定——`first_seen_at > NULL` 结果恒为
  SQL `NULL`（假），不需要额外的空值兜底

#### Scenario: 未关注任何卖家时不产生命中

- **WHEN** 用户在当前站点没有关注任何卖家
- **THEN** 该站点下没有任何关键词被判定为竞对新发现命中

### Requirement: 竞对新发现的私有性与站点边界跟随既有关注关系约定

判定使用的"当前用户关注的卖家"集合 SHALL 按 `user_seller_watch` 的 `user_id` 与查询
当前站点共同限定，MUST NOT 跨用户、MUST NOT 跨站点。

#### Scenario: 不同用户看到不同的命中结果

- **WHEN** 用户甲关注了卖家 A，用户乙关注了卖家 B，A、B 近期新发现的 ASIN 完全不重叠
- **THEN** 用户甲与用户乙对同一批关键词施加竞对新发现筛选，得到不同的命中结果

#### Scenario: 跨站点关注不影响本站点判定

- **WHEN** 用户在 `US` 站点关注了卖家 A（`site=US`），并在 `GB` 站点关注了卖家 B（`site=GB`）
- **THEN** 查询 `US` 站点时只用卖家 A 近期新发现的 ASIN 判定，不受卖家 B 影响

### Requirement: 「竞对新发现」筛选要求独立的竞对数据权限

`competitor_new=true` SHALL 额外要求当前用户拥有 `competitor:view` 权限，MUST NOT 仅凭
`analysis:view`/`analysis:export` 放行——这个筛选读取的是竞对监控域的私有数据（用户关注
了哪些卖家），与分析查询屏本身是两个独立的权限域（PR #66 复审 P1 定案）。

后端 SHALL 在 `/search`、`/export` 两个端点都校验，且必须在任何数据库访问（含 `/export`
的开流）之前完成。前端 SHOULD 按同一权限隐藏「竞对新发现」开关入口，但这只是体验层面的
优化——真正的授权边界在后端。

#### Scenario: 只有分析权限、没有竞对权限时拒绝

- **WHEN** 用户只拥有 `analysis:view`（或 `analysis:export`），在请求中传入
  `competitor_new=true`
- **THEN** 系统返回 403，不执行任何查询或导出

#### Scenario: 不使用该筛选时不要求竞对权限

- **WHEN** 用户只拥有 `analysis:view`，请求中不传 `competitor_new`（或传 `false`）
- **THEN** 系统正常处理请求，不因为缺少 `competitor:view` 而拒绝
