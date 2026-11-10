## Context

「竞对新发现」筛选由 #56 P2 和 PR #66 引入。实现分四层：

| 层 | 现状 |
|---|---|
| 请求模型 | `AnalysisSearchRequest.competitor_new: bool \| None`，列表、数量、导出共用 |
| 权限 | `analysis_api` 在 `/search`、`/export` 里按请求内容调用 `simple_auth.check_permission(user, COMPETITOR_VIEW)` |
| 查询 | `analysis_crud._apply_filters` 关联 `DimProduct → CompetitorSellerAsin → CompetitorSeller → UserSellerWatch`，以 `now()` 计算窗口；`request_has_filters` 用 `competitor_new or None` 参与判定 |
| 结果键 | `result_key._PRIVATE_FLAGS = ("competitor_new",)`，命中时把 `user_id` 和 `competitor:view` 纳入键 |
| 前端 | `FilterDraft.competitorNew`、`sanitizeCompetitorNew`、`FilterPanel` 开关、`useAnalysisQuery` 的权限兜底、`toSearchBody` |

`search()`、`export` 链路上的 `user_id` 只喂给这个筛选（`analysis-query-efficiency` design 已核实），其余条件的结果与查询者无关。

另有一个尚未归档的 `analysis-query-efficiency` 变更，它的 delta `analysis-query-coordination` 以 `competitor_new` 作为私有字段的例子。

## Goals / Non-Goals

**Goals:**

- 分析查询的列表、数量、导出三条路径（日报、周报）都不再读取竞对表，也不再依赖 `competitor:view`。
- 显式携带旧参数的请求得到明确拒绝，不会被悄悄忽略。
- 用户已有的其他保存条件不丢失。

**Non-Goals:**

- 不动竞对模块的表、接口和权限（由 #166 的另一个变更负责）。
- 不删除事实表 `top{n}_product_id` 索引，不修改历史事实数据。
- 不新增「商品 → 关键词」自动联动。
- 不改 `check_permission` 这个通用方法本身。

## Decisions

### D1 退役参数：保留字段名，`true` 返回 422，`false`/不传放行

请求模型保留 `competitor_new: bool | None`，加一个字段校验器：值为 `True` 时抛出校验错误，提示「竞对新发现筛选已下架，请移除该条件」。按现有错误契约，它在解析阶段变成 422，不访问数据库；`/export` 因此也在开流之前失败。这与 `asin_schemas._cap_items` 把校验放在模型层是同一种做法。

- 备选「直接删除字段」被否决：模型默认 `extra='ignore'`，旧客户端传 `true` 会被静默丢弃并返回未筛选的全量结果，调用方会把它当成竞对命中结果使用。这正是 #166 要求避免的「误以为筛选仍有效」。
- 备选「改为 `extra='forbid'`」被否决：影响面是整个请求模型，任何多余字段都会被拒，超出本次范围。
- `false` 放行，因为原规格本来就规定它与不传等价。旧前端在用户没勾选时可能显式发送 `false`，这类请求不该报错。

`result_key._FIELDS` 仍需要给这个字段归类（该模块的测试要求每个字段都已归类），归为 `_FLAG`。因为 `true` 已在模型层被拒，它在键里恒为 `None`，`false` 与不传因此得到同一个键。

### D2 结果键的通用保护保留，登记表置空

`_PRIVATE_FLAGS` 改为空字典（字段 → 该筛选要求的权限点），`ResultKey.user_id` 及「命中私有字段才带用户标识与权限范围」的分支保留；命中私有字段却没给 `user_id` 时抛错，宁可失败也不算成共享键。这样以后再出现依赖私有数据的筛选时，只需登记到这里，键就会自动按用户区分，不必重新设计。`competitor:view` 不再出现在任何分析结果键的权限范围里。

- 备选「连同 `user_id` 字段一起删除」被否决：#166 要求不误删其他功能需要的通用缓存保护。这个分支不读竞对数据，代价只是几行代码。

### D3 `user_id` 链路一并删除

`analysis_service.search/count/export`、`analysis_crud` 中的计数、取页和导出语句，以及 `search_summary_fields`，原本接收的 `user_id` 只供该筛选使用，本次一并删除。API 层的鉴权依赖（`require_permission(ANALYSIS_VIEW)` 等）保持不变。`result_key` 的 `user_id` 改为可选参数（缺省 `None`，见 D2），当前唯一调用方 `search_summary_fields` 不再传它。`simple_auth.check_permission` 按 D2 的同一理由保留，目前没有调用方。

### D4 前端保存条件：读取时丢弃残留字段，不提升版本号

前端规则规定「改保存条件形状要提升 `SAVED_MODE_VERSION`，旧 payload 不迁移」。这条针对的是新增或改变字段语义：旧形状缺少新字段，渗进 UI 后会出现空白控件。这次是删除一个字段，旧 payload 只是多出一个不再读取的键。提升版本号会把所有用户的有效保存条件整份清掉，违背 #166「其他有效保存条件妥善保留」的要求。

做法：`readSavedMode` 在通过版本校验后，从 `filters` 中显式删除 `competitorNew`，再返回结果，并配一个测试用例：旧 payload 里 `competitorNew: true`，恢复后其余条件不变，请求体中没有 `competitor_new`。`frontend.md` 中「改形状提升版本」那条规则补一句「删除字段可以在读取时剔除，不必提升版本」，并注明来源为本变更。

会话快照（`stateCache`）存在模块级内存里，新版本前端加载时它必然为空，不存在旧快照，所以 `CACHE_VERSION` 不变。

- 备选「提升 `SAVED_MODE_VERSION`」被否决：会造成大面积静默丢失用户配置，而收益只是省掉一行剔除代码。

### D5 规格退役的形态

`competitor-keyword-crosslink` 的三条 Requirement 全部用 REMOVED 删除。归档后如果 CLI 留下一份没有 Requirement 的空规格（validate 不一定接受），归档提交中手工删除 `openspec/specs/competitor-keyword-crosslink/` 目录，并同步删除 `openspec/guides/{competitor,frontend}.md` 里指向它的链接。

### D6 与 `analysis-query-efficiency` 的先后

`analysis-query-coordination` 还不在 `openspec/specs/` 下，本变更没有可以 MODIFY 的对象，也不修改另一个变更的目录。顺序是：用户先对 `analysis-query-efficiency` 执行 `/close-change`，让它归档；之后本变更补上 MODIFIED delta，把结果键要求里的 `competitor_new` 例子改为通用表述（「依赖查询者私有数据的筛选」，当前没有此类筛选），并删除两个以 `competitor_new` 为条件的 Scenario。如果本变更先合并，就在 `analysis-query-efficiency` 归档时由归档者核对这份 delta 是否还要改写，并在 tasks 1.1 记录去向。

## Risks / Trade-offs

- [风险] 外部脚本或书签仍会发送 `competitor_new=true` → 收到明确的 422 提示，而不是错误结果。生产里目前只有前端这一个调用方。
- [风险] 删除 `user_id` 链路时漏掉调用点，导致参数个数不匹配 → 由类型检查、ruff 和现有 search/export 单元及集成测试覆盖。
- [取舍] 保留一个不再生效的请求字段，OpenAPI 上会继续出现它 → 字段描述改为「已下架，传 true 返回 422」。以后旧客户端都消失了，可以再单独删掉。
- 性能：只是去掉一个子查询分支，没有新增查询。连接池占用、内存和执行计划都不会变差。本来就没开这个筛选的请求，执行计划完全不变。事实表索引保留，因为 ASIN 精确匹配仍在使用。
- 数据库：无迁移，不碰 `analysis` schema。

## Migration Plan

- 前后端同一版本发布。新后端配旧前端（例如浏览器缓存了旧包）时，只有勾选了该开关的用户会收到 422 提示，刷新页面后即正常。
- 回滚：直接回退代码即可，没有数据变更。
