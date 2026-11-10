## REMOVED Requirements

### Requirement: 竞对新发现是独立的筛选维度，按前三任一命中
**Reason**: #166 让市场分析与竞对监控解耦，ABA 查询不再依赖用户私有的竞对关注与新发现数据。
**Migration**: 请求中删除 `competitor_new`。如果仍显式传 `true`，按「已下架的竞对新发现参数明确拒绝」返回 422。按 ASIN 查关联关键词改用「ASIN 精确匹配覆盖三个商品槽位」。

## ADDED Requirements

### Requirement: 已下架的竞对新发现参数明确拒绝

分析查询的列表、数量与导出接口 SHALL 继续识别 `competitor_new` 这个字段名，目的只是给出明确的退役处理，MUST NOT 再据它施加任何筛选，也 MUST NOT 读取竞对关注、卖家或新发现数据。

- 请求显式传 `competitor_new=true` 时，系统 SHALL 返回 422，提示该筛选已下架。这个判定 MUST 在任何数据库访问之前完成，`/export` 则 MUST 在开流之前完成。系统 MUST NOT 忽略该参数后返回未筛选的结果，否则调用方会以为筛选仍然有效，把全量结果当成竞对命中结果使用。
- 请求传 `competitor_new=false` 或不传时，系统 SHALL 正常处理，与完全不带该字段的请求等价，包括「是否带筛选」的判定和结果键。
- 拒绝与否 MUST NOT 取决于用户是否拥有 `competitor:view`。这个参数已经没有可以授权的含义。

前端 SHALL 不再提供「竞对新发现」开关。从保存条件或会话快照恢复出的残留 `competitorNew` 值 SHALL 在读取时丢弃，MUST NOT 进入草稿、生效条件或请求体。其余保存条件 SHALL 照常恢复。

#### Scenario: 旧客户端显式开启已下架筛选

- **WHEN** 请求 `/search` 或 `/export` 时带 `competitor_new=true`
- **THEN** 响应为 422，提示竞对新发现筛选已下架；不执行查询、不开流，也没有任何竞对表访问

#### Scenario: 传 false 与不传等价

- **WHEN** 请求带 `competitor_new=false`，不带其他筛选
- **THEN** 系统正常返回结果，总数走无筛选快路径，结果键与完全不传该字段的请求相同

#### Scenario: 无竞对权限的用户传 false

- **WHEN** 只有 `analysis:view` 的用户请求时带 `competitor_new=false`
- **THEN** 请求正常处理，不因缺少 `competitor:view` 被拒绝

#### Scenario: 旧保存条件里开着竞对新发现

- **WHEN** 用户此前保存的条件中 `competitorNew` 为真，下架后进入分析查询屏
- **THEN** 其余保存条件（站点、视图、品牌、首次入榜等）照常恢复，首屏请求体中没有 `competitor_new`，界面上也没有该开关

#### Scenario: 日报与周报同样处理

- **WHEN** 分别在日视图和周视图下请求时带 `competitor_new=true`
- **THEN** 两个视图都返回同样的 422
