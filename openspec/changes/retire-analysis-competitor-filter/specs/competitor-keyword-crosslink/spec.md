## REMOVED Requirements

### Requirement: 竞对新发现命中一个关键词的判定只到 ASIN 级、只看近期新发现
**Reason**: #166 定下的业务边界是：竞对监控的对象是店铺商品，市场分析只查 ABA 关键词，两个模块不做联动。市场分析不再读取竞对关注和新发现数据。
**Migration**: 想看竞对店铺新出现的商品，到竞对监控的商品发现列表查看。已知 ASIN 的，可以在市场分析里用「ASIN 精确匹配」（前三槽位任一命中）查它关联的 ABA 关键词。

### Requirement: 竞对新发现的私有性与站点边界跟随既有关注关系约定
**Reason**: 筛选整体下架，市场分析不再读取任何用户私有的竞对关注数据，这条私有性约束已没有适用对象。竞对模块自身的私有性仍由 `competitor-storefront-watch` 约束。
**Migration**: 无需迁移。关注关系按用户私有的规则继续由 `competitor-storefront-watch` 管理。

### Requirement: 「竞对新发现」筛选要求独立的竞对数据权限
**Reason**: 筛选已下架，`/search`、`/export` 不再需要按请求内容额外校验 `competitor:view`。显式携带旧参数的请求一律按 `site-scoped-query`「已下架的竞对新发现参数明确拒绝」处理，不论有无权限，结果都一样。
**Migration**: 调用方删掉 `competitor_new` 参数即可。竞对监控各端点仍要求 `competitor:view`，不受影响。
