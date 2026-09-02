## Context

`fact_daily`/`fact_weekly` 每行是一个关键词，`top1/2/3_product_id` 是该词点击份额前三
的商品（关联到 `dim_product`）。`competitor_seller_asin`（P3a，issue #56/PR #64）记录
某个 Amazon 卖家当前在售的 ASIN 集合；`user_seller_watch` 记录用户关注了哪些卖家。三者
目前互不相连——分析查询屏能查"某个 ASIN 排第几"（只查 `top1_product_id`），但查不出
"我关注的这些卖家，有没有商品挤进了某个词的前三"。

`brands`/`categories` 筛选已经建立了"前三任一命中"的标准形状（解析成 id 集合的子查询 →
`or_(top1.in_(ids), top2.in_(ids), top3.in_(ids))`），且在真实数据上验证过性能（US 周报
278.5 万行：无索引 499ms → 建索引后 187ms，见迁移 `d3e4f5a6b7c8` 的注释）。本次改动复用
这个已验证的形状，不发明新的查询模式。

## Goals / Non-Goals

**Goals:**
- ASIN 精确匹配从只命中 `top1_product_id` 扩展到三个槽位任一命中
- 新增"竞对新品"筛选：命中条件是该词前三商品里，有一个 ASIN 是当前用户在当前站点关注
  的卖家**近期新发现**的商品——判据与卖家清单「近 7 日新发现」（`watch_crud.
  NEW_DISCOVERY_WINDOW_DAYS`）同构：`first_seen_at > baseline_at`（基线批次不算）且
  落在窗口内（PR #66 复审 P1 定案；首版把它写成"当前在售集合、不限时间"，但
  `competitor_seller_asin` 不做下架删除，无时间限定会让这个开关命中卖家有史以来上报过
  的全部商品，与字段名"新品"名实不符）
- 索引补齐到位，两条筛选都不退化成全表扫描
- 保持"带筛选的查询锁死在单一事实切片里"这条既有纪律不变——不引入新的查询模式

**Non-Goals:**
- 方向 C（品牌级：卖家 → 品牌名 → `top1/2/3_brand_id`）——数据源不存在，移出本次
- 不做"选择具体关注哪几个卖家再筛"这种细粒度控制——首版只有一个布尔开关（是否命中
  "我关注的任意卖家"），YAGNI；需要时再加，不影响已有数据形状
- 不做历史回溯（"这个词三天前是不是也命中"）——命中判定只看当前这次查询的事实切片，
  与其他筛选维度的语义一致
- 不新增独立的"从卖家查关联关键词"端点——ASIN 精确匹配 + "竞对新品"筛选组合起来已经
  覆盖 issue 里方向 A/B 的实际需求（复制一个 ASIN 到搜索框 = 方向 A；开着"竞对新品"开关
  浏览关键词列表 = 方向 B），新增端点是重复造轮子

## Decisions

### 决策 1：索引补齐照抄品牌索引的形状与迁移手法

`fact_daily`/`fact_weekly` 各建 `idx_{table}_top2_product`/`idx_{table}_top3_product`，
列序 `(site, date_col, topN_product_id)`，与既有 `idx_{prefix}_top1_product`/
`idx_{prefix}_top{n}_brand` 完全同构。迁移手法照抄 `d3e4f5a6b7c8` 那条迁移的 3.5 步：
父表上 `CREATE INDEX IF NOT EXISTS`（元数据操作，秒级；分区表的 `CREATE INDEX` 不支持
`CONCURRENTLY`，已装满的库需要先按该迁移注释里的 ONLY + CONCURRENTLY + ATTACH 流程离线
补建，此处的 `IF NOT EXISTS` 才会空转而过）。

备选：表达式 GIN（`ARRAY[top1,top2,top3] && ARRAY[...]`）。品牌索引那次的决策已经否决过
这条路——GIN 会把 SQL 形状焊死在索引上，`_apply_filters` 一旦改回自然的 `OR ... IN`，
索引静默失效退回全表扫描，且测试不会报错。三个独立 btree + `BitmapOr` 是计划器自己选的
方案，不依赖 SQL 写法保持某种固定形状。

### 决策 2：ASIN 扩展与"竞对新品"用同一套"解析 id 集合 → OR 三列"形状

```python
# ASIN：单个 ASIN 解析成单个 product_id
if req.asin:
    pid = select(DimProduct.product_id).where(DimProduct.asin == req.asin).scalar_subquery()
    stmt = stmt.where(or_(fact.top1_product_id == pid, fact.top2_product_id == pid,
                           fact.top3_product_id == pid))

# 竞对新品：解析成一个 product_id 集合，限定「近期新发现」
if req.competitor_new:
    ids = (
        select(DimProduct.product_id)
        .join(CompetitorSellerAsin, CompetitorSellerAsin.asin == DimProduct.asin)
        .join(CompetitorSeller, and_(
            CompetitorSeller.seller_id == CompetitorSellerAsin.seller_id,
            CompetitorSeller.site == CompetitorSellerAsin.site,
        ))
        .join(UserSellerWatch, and_(
            UserSellerWatch.seller_id == CompetitorSellerAsin.seller_id,
            UserSellerWatch.site == CompetitorSellerAsin.site,
        ))
        .where(
            UserSellerWatch.user_id == user_id,
            CompetitorSellerAsin.site == site,
            CompetitorSellerAsin.first_seen_at > CompetitorSeller.baseline_at,
            CompetitorSellerAsin.first_seen_at
            > func.now() - text(f"interval '{NEW_DISCOVERY_WINDOW_DAYS} days'"),
        )
        .scalar_subquery()
    )
    stmt = stmt.where(or_(fact.top1_product_id.in_(ids), fact.top2_product_id.in_(ids),
                           fact.top3_product_id.in_(ids)))
```

两处子查询都不相关（uncorrelated：`user_id`/`site`/`asin` 对整条查询是常量），计划器会
物化一次再对三列做 `IN`/`=` 探测，与已验证的品牌/类目筛选同一执行形状，不需要单独再证明
一次性能。`user_id` 从令牌取（服务层传入，不从请求体读，与本仓库既有鉴权模式一致）。

**"新发现"两个时间条件复用 `watch_crud.NEW_DISCOVERY_WINDOW_DAYS`，不重新定义一个窗口
常量**（这条是 PR #66 复审后补的决策，首版没有）。`competitor_seller_asin` 是被动、增量
采集的历史观察集合，不做下架删除（见 `asin_model.py` 的既有边界）——不加时间限定的话，
「竞对新品」会命中卖家有史以来上报过的全部商品，与字段名不符，且与卖家清单页"近 7 日
新发现"的口径不一致（同一个数据源，两处却在回答不同的问题）。两个条件缺一都会制造假新品：
- `first_seen_at > baseline_at`——排除首次上报那批（基线批次不是"新发现"，是"我们第一次
  看到这家店在卖什么"，见 `asin_service.py` 的基线不变式）
- `first_seen_at > now() - interval 'N days'`——排除"新发现"但已经不新鲜的历史记录，
  `baseline_at` 为 NULL（卖家尚未建立基线）时比较结果是 SQL NULL，天然不命中，不需要
  额外的 `CASE WHEN` 兜底（与 `_new_discoveries_subq` 那种"要区分 NULL 与 0"的场景不同，
  这里只是一个 WHERE 条件的真假判断）

**改动前 ASIN 筛选是 `DimProduct.asin == req.asin`（借用 base query 里已 JOIN 到
`top1_product_id` 的那个 `DimProduct` 别名）。改成子查询形式后，这个 JOIN 不再是 ASIN
筛选的依赖，但 `product_title` 筛选仍然依赖它（`product_title` 本次不扩展到三个槽位，
保持只命中 top1）——`_count_base_query` 里的 `DimProduct` LEFT JOIN 不能删。**

**改动前 ASIN 筛选是 `DimProduct.asin == req.asin`（借用 base query 里已 JOIN 到
`top1_product_id` 的那个 `DimProduct` 别名）。改成子查询形式后，这个 JOIN 不再是 ASIN
筛选的依赖，但 `product_title` 筛选仍然依赖它（`product_title` 本次不扩展到三个槽位，
保持只命中 top1）——`_count_base_query` 里的 `DimProduct` LEFT JOIN 不能删。**

### 决策 3：`competitor_new` 必须计入 `request_has_filters`

`request_has_filters()` 决定总数走 `site_data_stats`（O(1)，无筛选路径专用）还是
`count_filtered`（精确实算，带筛选路径专用）。这个函数当前逐个列出所有筛选字段——
**`competitor_new` 不加进这个元组，效果是它虽然真的过滤了行，却仍然报告"总条数=站点当期
总词数"这个与筛选前一样的数字，分页页码全部对不上，且不会有任何报错或测试失败**（两条
路径都能正常跑完，只是总数是错的）。这是本次改动里"错了不会报错"的地方，必须在实现时
显式核对，并补一个断言 `request_has_filters` 结果的单测。

**元组里放的是 `req.competitor_new or None`，不是裸值**（PR #66 复审 P2 补的修正）。
`competitor_new` 与三态的 `is_new` 不同——它只有两态，`_apply_filters` 里是
`if req.competitor_new:`，`False` 与 `None` 的查询结果完全一样。裸传 `req.competitor_new`
会让 `False` 被 `v is not None` 误判成"带筛选"，错误地切到精确计数路径；`or None` 把
`False` 收敛回 `None`，与它在 `_apply_filters` 里的实际行为对齐。

### 决策 4：不做"按具体卖家筛选"，只做全局布尔开关

请求字段是 `competitor_new: bool | None = Field(None, ...)`（`None` = 不筛，与 `is_new`
同构），不是 `competitor_seller_ids: list[str]`。理由：分析查询屏的筛选面板已经很宽，
每加一个下拉都要占横向空间（这个屏的横向宽度是全产品最紧张的资源，见 CLAUDE.md 分析查询屏
一节）；而"我关注的任意竞对有没有挤进这个词"已经回答了 issue 里的核心问题。要按单个卖家
细分，用户可以先去竞对监控页确认是哪个卖家的 ASIN、再把该 ASIN 粘到 ASIN 搜索框（决策 2
已经支持三槽位任一命中）——两个入口组合起来覆盖了这个需求，不需要为此新增一个筛选维度。

### 决策 5：`competitor_new=true` 额外要求 `competitor:view` 权限

`/search`、`/export` 只挂了 `analysis:view`/`analysis:export`，而 `competitor_new` 读取
的是竞对监控域的私有数据（用户关注了哪些卖家）——两个数据域的权限点是独立的
（`app/auth/permissions.py`：`COMPETITOR_VIEW` 的注释明确写着"每个用户的关注池互相独立，
不是全体共用一个开关的授权粒度，与 `ANALYSIS_VIEW` 等同一档次"）。首版遗漏了这一点
（PR #66 复审 P1），补法是在两个端点里按需校验：`if req.competitor_new:
simple_auth.check_permission(user, COMPETITOR_VIEW)`——`check_permission` 是新增的静态
方法，逻辑与 `require_permission` 依赖内部一致，独立成方法是因为这里的权限要不要求取决于
请求体内容，不是逐路由的静态声明，套不进 `Depends()` 那一套。前端同步按
`access.canViewCompetitor` 隐藏开关（`FilterPanel` 新增 `canCompetitorNew` prop）——
但前端隐藏只是体验层面，真正的边界仍然在后端这两处检查。

## Risks / Trade-offs

- **[风险] "新发现"判定依赖 `baseline_at` 落定，且落定需要至少两次上报** → 首次上报把
  `baseline_at` 与该批 `first_seen_at` 落在同一事务、严格相等（`asin_service.py` 的不变式），
  `first_seen_at > baseline_at` 因此恒为假；要让某个 ASIN 被判定为"新发现"，必须是该卖家
  **第二次及以后**的上报里出现的新 ASIN。这不是缺陷，是"新发现"这个词本身的含义（"这次
  比上次多了什么"，不可能只凭一次观测就回答），但上线后短期内——尤其是刚被关注、只上报过
  一次的卖家——`competitor_new` 筛选大概率是空的，前端文案需要说清楚（"依赖浏览器插件采集
  的竞对在售数据，插件未上线前这里可能暂无命中"这句已经覆盖了"数据还没来"，但没覆盖"数据
  来了、但还不够两次"这层）
- **[风险] 已装满的生产库跑迁移会长时间持锁** → 与品牌索引那次同一个风险，缓解手法也一样：
  迁移里的 `CREATE INDEX IF NOT EXISTS` 只在索引不存在时才真的建；上线前按迁移注释里的
  ONLY + CONCURRENTLY + ATTACH 流程离线补建，迁移本身空转
- **[权衡] `competitor_new` 与 `asin`/`brands`/`categories` 同时勾选时是 AND 还是 OR** →
  AND（与现有筛选维度之间的组合方式一致，"筛选面板里勾的所有条件同时成立"是这个屏所有
  筛选维度共享的语义，不为这一个维度破例）
