## Context

issue #56 的完整需求（竞对店铺上新监控，与 ABA 关键词数据打通）分成六个阶段，见 issue 的
「分阶段」表。数据获取层（浏览器插件/Keepa）与双向联查（P2）都依赖「运营在系统里维护了一份
结构化的关注清单」这个前提先成立，而清单本身——分组、导入、状态跟踪——不依赖任何未决项。
本变更只做这一份清单（对应 phasing 表的 P0），刻意不碰数据获取层，理由是 issue 里两个仍在
等待的决策（Keepa token 成本、插件后台巡检的风险接受度）都不影响这份清单怎么建。

已有的地基决定了本设计的大部分形状：

- 站点码唯一事实来源是 `app/site_registry.py`（英国是 `GB` 不是 `UK`），书签里的
  `marketplaceID` 参数要在落库前转换成这套码，不新起编码。
- `toolbox:view` 一个权限点覆盖工具箱全部屏幕的先例说明「一个功能域一个权限点」是本仓库
  的既定粒度；竞对监控是独立数据域（自己的表、自己的用户隔离逻辑），套用同一粒度就该开
  自己的权限点，而不是塞进 `toolbox:view`。
- 仓库没有 Redis/Celery，`OSS_UPLOADING` 批次「先落库再交出凭证」与
  `import_queue.py` 的轮询模式是仓库处理「短期状态」与「后台接手」的既有解法，本变更的
  预览暂存与过期清理沿用同一模式。
- `keyword_group`/`keyword_group_item`（我的词库功能）是结构最接近的先例：共享维度表 +
  用户私有清单的两层模型、`toolbox:view` 式的单一权限点、分组管理区摆表格上方不做侧栏。
  差异在于词库的行集合来自维度表（`dim_keyword` 已存在），而本变更的共享层
  `competitor_seller` 本身就是新建的、且要先解决「同一卖家被多用户关注、有多种输入格式、
  可能整批批量导入 3000+ 条」这几个词库场景没有的问题。

用户已定的关键决策（2026-08-29，issue #56「待确认」）：`competitor:view` 新权限点、
多站点同卖家分开算不合并、取消关注后保留 7 天（**不在本变更范围**——保留期清理属于数据
获取层落地后才有意义的 GC，本变更没有 ASIN 快照数据可清理）。

## Goals / Non-Goals

**Goals:**

- 运营能把浏览器收藏夹里的 2000+ 条卖家链接一次性导入，也能后续零散添加。
- 导入前必须能预览结果（新增/已存在/解析失败各多少条），不能一粘贴/一上传就直接落库。
- 卖家主体全局去重（`UNIQUE(seller_id, marketplace)`），关注关系与分组按用户私有。
- 新表与既有导入/查询/保留三条路径完全解耦：不写 `fact_daily`/`fact_weekly`，不接
  `ensure_fact_partitions`（这三张都是普通表，不分区）。
- 为后续数据获取层预留但不实现：`competitor_seller.baseline_at` 列存在，本变更只写
  NULL（状态恒为「待建立基线」），不产生任何 ASIN 级数据。

**Non-Goals:**

- 不做浏览器插件、Keepa 集成、任何形式的 ASIN 抓取——`competitor_asin`/
  `competitor_asin_snapshot` 两张表不在本变更创建。
- 不做「今日上新」页、不做与 `dim_product`/`dim_brand` 的联查、不做搜索页「竞对新品」
  筛选项——这些都读不存在的 ASIN 数据，做出来也无法验证。
- 不做取消关注后的保留期与 GC——没有快照数据，没有可保留可清理的东西。
- 不做共享/团队可见性、超管全局视角（同 issue「暂不实现」一节）。

## Decisions

### 1. 两层模型：共享卖家主体 + 私有分组/关注

```
competitor_seller     UNIQUE(seller_id, marketplace)
                       seller_id | marketplace | baseline_at | created_at
user_seller_group      UNIQUE(user_id, name)
                       group_id | user_id → user_center | name | created_at | updated_at
user_seller_watch      UNIQUE(user_id, seller_id, marketplace)
                       watch_id | user_id → user_center
                       (seller_id, marketplace) → competitor_seller
                       group_id → user_seller_group (nullable：未分组)
                       priority | note | created_at
```

不能给 `competitor_seller` 加 `user_id`：同一卖家被多个运营收藏会变成多行，
`UNIQUE(seller_id, marketplace)` 直接废掉；后续数据获取层按 unique seller 计成本，
按 (user, seller) 存会让抓取量随人数线性放大而非按去重后的卖家数计。

`group_id`/`priority`/`note` 挂在 `user_seller_watch` 而非 `competitor_seller`——分组
命名与优先级是个人习惯，同一个卖家在甲的池子里可能是「一级竞对」，在乙的池子里可能
根本没建组。

`user_seller_group` **不绑定站点**（与 `keyword_group` 不同）：一个卖家的 `marketplace`
已经由 `competitor_seller` 唯一确定，分组只是运营自己的归类习惯，同一个分组内允许出现
不同站点的卖家（例如「头部竞对」这个分组里 US/UK/DE 各有几家）——没有理由在这一层强加
站点边界。

**取舍：一个 watch 只属于一个分组**（`group_id` 单值，可为 NULL）。书签里跨文件夹重复
收藏的同一卖家合并成一条 watch，重复次数计入 `priority`。多标签（一个卖家同时属于多个
分组）属 YAGNI，需要时加关联表，不影响已有数据。

**删除分组不删除关注关系，只解除归属**（`group_id` 外键 `ON DELETE SET NULL`）。这与
`keyword_group`/`keyword_group_item`（删组连带删条目）不同：那边的条目除了归属关系
不携带其他状态，删组等于用户明确表示"这批词不要了"；这里的 watch 是"我在盯这个卖家"
这件事本身，分组只是归类标签，删分组不代表用户想取消关注——用户想取消关注会直接删除
watch。误删一个分组不该连带丢失关注关系。

### 2. 预览结果存数据库表，不引入 Redis

```
competitor_watch_preview
  preview_token PK | user_id → user_center | parsed JSONB | expires_at | created_at
```

仓库没有 Redis/Celery 依赖，为一个 5~10 分钟的预览缓存引入这两个组件不划算——它们要解决
的是跨进程共享短期状态，而本仓库已有更轻的解法：`import_batch` 的 `UPLOADING` 状态就是
「先落库再对外交付」的先例。`expires_at` 过期行的清理**不新开定时器**，挂在
`import_queue.py` 已有的 30 秒轮询循环上顺带扫一遍（同一个循环里加一个 `DELETE ...
WHERE expires_at < now()`，不新增 lifespan 任务）。

`preview_token` 落 `user_id`：即使预览尚未提交，也不该让用户 A 拿着自己的 token 猜到
用户 B 的预览内容——`commit` 端点校验 token 归属当前用户，不归属则 404。

书签文件很小（约 2MB），**同步处理**：解析在请求处理函数里直接跑完就返回，不走
`upload` 那套 OSS 直传 + 后台任务，那是给 GB 级 CSV 准备的，用在这里是过度设计。

### 3. `SellerRefParser`：纯函数，六种输入形态，两种明确拒绝

```python
SELLER_ID_RE = re.compile(r'^A[A-Z0-9]{11,19}$')  # 基于 2657 个实测样本：长度 12/13/14，A 开头
```

| 输入形态 | 处理 |
|---|---|
| `/s?i=merchant-items&me=XXX&...` | 取 `me` |
| `/sp?seller=XXX` | 取 `seller` |
| `/gp/help/seller/at-a-glance.html?seller=XXX` | 取 `seller` |
| 裸 ID（如 `A1TZ946F8HZTNY`） | 正则识别 |
| `/stores/Brand/page/UUID` | **拒绝**，明确提示「品牌店铺页无法提取卖家 ID」 |
| `/dp/B0XXXXXXX` | **拒绝**，明确提示「商品详情页无法提取卖家 ID」 |

站点识别：优先取 `marketplaceID` 查询参数（`ATVPDKIKX0DER`→`US`、`A1F83G8C2ARO7P`→`GB`、
`A1PA6795UKMFR9`→`DE`——issue #56 原文这里少写了 `GB` 那个 ID 末尾的 `P`，已用真实书签
文件核实并订正，`site_registry` 只做站点码合法性校验，marketplaceID 映射表是解析器
本地常量），缺失时回落到域名 tld
（`amazon.com`→`US`、`amazon.co.uk`→`GB`、`amazon.de`→`DE`）——97% 的书签 URL 带
`ds=` 会话签名参数、早已失效，**从不据此重建请求，只提取 `seller_id` + `marketplace`
两个值**，展示用的链接由模板 `https://www.amazon.{tld}/s?i=merchant-items&me={seller_id}
&s=date-desc-rank` 现拼。

两种拒绝形态不是静默丢弃：预览响应的 `unparsable` 列表带原始文本 + 拒绝原因，界面逐条
展示，用户可以自己判断要不要手动去找这些卖家的 ID。

**裸 ID 与站点信息缺失的链接需要 `fallback_site` 兜底。** 裸 ID 天生不带站点信息；
链接缺 `marketplaceID` 参数且域名也无法识别站点时同理。`preview` 接口接受一个可选的
`fallback_site` 字段（用户在「添加卖家」界面上选定），解析时优先用链接自带的站点信息，
拿不到才用这个兜底；两者都没有则该条判为解析失败（`REJECT_BARE_ID_NEEDS_SITE` /
`REJECT_SITE_UNKNOWN`），**不猜测默认站点**——与 `site_registry.normalize_site_code`
的既定纪律一致（非法/缺失站点报错不回退）。

单元测试不连库，参照 `import_source.py` 里文件名解析器的纯函数测试写法；测试样本取自
`docs/bookmarks_2026_8_18(1).html` 的抽样（不整份入库，只挑边界情形：12/13/14 位 ID
各一例、`marketplaceID` 缺失的 UK 链接、`/stores/` 与 `/dp/` 各一例）。

### 4. 预览/确认两步流的落库语义

```
POST /api/competitor/watch/preview
  入参 {raw_text} 或 {file}（bookmarks.html 或粘贴文本二选一）
  出参 {preview_token, parsed, new, existing, unparsable[{text, reason}], groups[], samples[≤20]}

POST /api/competitor/watch/commit
  入参 {preview_token, mode: "append" | "replace_group", target_group_id?}
  出参 {created_sellers, created_watches, updated_watches, skipped}
```

- **`groups`**：书签文件夹路径拍平成候选分组名（去掉「（数字）」这类去重后缀、按最深的
  有意义层级取名，具体规则由实现时对照真实文件的抽样定），预览阶段只展示候选、不落库，
  `commit` 时才真正建组。
- **`mode=append`**（默认）：新 `(seller_id, marketplace)` 建 `competitor_seller`
  （`ON CONFLICT DO NOTHING`）+ 建 `user_seller_watch`；已存在的 watch 只更新
  `priority`（取重复次数），**不动 `group_id`/`note`**——避免覆盖用户已经手工整理好的
  分组。
- **`mode=replace_group`**：先删除 `target_group_id` 下的全部 watch，再按预览结果重建
  ——用于「书签文件又导出一份新的，想让某个分组完全对齐最新导出结果」。删除只影响
  `user_seller_watch` 行，不删除 `competitor_seller`（可能被其他用户或其他分组关注）。
- **配额校验在 preview 阶段就做**：`MAX_WATCH_PER_USER`（3000）、`MAX_IMPORT_BATCH`
  （5000，单次预览的可解析条数上限）超限时 preview 响应即报错，不等到 commit 才失败。
  两者都进 `config.py`，`.env.example` 同步。

**备选：commit 不分 append/replace_group，一律追加，重复导入靠用户手动清理**。被否：
运营换电脑重新导出书签是预期场景（issue 背景写明「换台电脑或换个人接手就全丢了」），
没有 replace 语义等于每次都要手工对比增量,而这正是要消灭的人肉步骤。

### 5. 私有性靠查询条件，不靠可见性字段

与 `keyword_group` 同一模式：每条分组/关注查询带 `user_id = <当前用户>`；取不到自己的
数据一律 404（不是 403——403 泄露「这个 id 确实存在」）。用户被删除时分组与关注记录
连带删除（外键 `ON DELETE CASCADE`），`competitor_seller` 不受影响（可能被其他用户
关注）。

### 6. 权限点：新增 `competitor:view`，不复用 `toolbox:view`

理由已在 issue #56 定案：每个运营的收藏池互相独立，不需要平台统一看，这是「账号隔离」
约束的直接体现；竞对监控是独立数据域（自己的表、自己的隔离逻辑），与
`analysis:view`/`analysis:export`/`upload:import` 这几个「一个功能域一个开关」的粒度
一致，不塞进覆盖工具箱全部屏幕的 `toolbox:view`。

授予 admin/operator/analyst 三个内置角色（与 `toolbox:view` 当初的授予范围一致，理由
同样是「先给全体已知角色开，没有理由现在就把分析师排除在外」）。迁移仿
`f2a3b4c5d6e7`（工具箱权限点种子迁移）的写法：`INSERT ... ON CONFLICT DO NOTHING`
到 `permission` 表，再按角色码关联 `role_permission`。

### 7. 代码落位与分层

`app/table/competitor/`（新目录，与 `analysis`/`toolbox`/`upload` 并列）：

```
seller_model.py    CompetitorSeller（共享层）
watch_model.py      UserSellerGroup / UserSellerWatch / CompetitorWatchPreview（私有层 + 技术表）
seller_ref_parser.py  纯函数，不连库
watch_crud.py       只出语句
watch_service.py     业务判定（配额、命名冲突、跨用户 404）+ 抛 APIError 子类
watch_api.py         路由，Depends(require_permission(COMPETITOR_VIEW)) + 当前用户
watch_schemas.py     Pydantic v2 请求/响应
```

路由挂 `/api/competitor/*`，在 `main.py` 注册。三张表都是普通表（不分区、无物化视图），
迁移用 `alembic revision --autogenerate`，不必手写。

### 8. 前端：独立顶层菜单，不挂在工具箱下

`competitor:view` 是独立权限点，菜单结构照 `分析查询屏`（`/analysis`，`canViewAnalysis`）
的单页顶层模式，不是工具箱那种「一个权限点覆盖多个二级屏」的结构：

```ts
{
  name: '竞对监控',
  path: '/competitor',
  component: './Competitor',
  icon: 'EyeOutlined',   // 待实现时对照 antd icon 库选定
  access: 'canViewCompetitor',
}
```

页面内部（`frontend/src/pages/Competitor/`）结构参照 `Toolbox/KeywordLibrary`：分组管理区
在表格**上方**（横向排布，不做侧栏——`keyword-library` capability 里「分组管理不得占用
盯盘表格横向宽度」那条 requirement 的理由同样适用：本页表格列数只多不少，侧栏形态会把
可视区压窄）、主表格用 ProTable（卖家名、站点、分组、状态、`priority`）、顶部配额条
「已关注 N / 3000」+「添加卖家」（textarea）+「导入书签」（文件上传），两个入口都先弹出
预览结果、用户确认后才调 commit。

状态列固定显示「待建立基线」——本变更没有数据获取层，`baseline_at` 恒为 NULL，「监控中」
「已暂停」两种状态在本变更里不会出现，但状态展示逻辑按三态实现（预留位置，避免数据获取层
落地时还要改这一列的渲染逻辑）。

## Risks / Trade-offs

- **[导入 3000+ 条卖家的预览响应体较大]** → `samples` 限 20 条，`unparsable` 全量返回
  但每条只是短文本，`groups` 是去重后的分组名列表（远小于卖家数）；真正大的是
  `parsed`/`new`/`existing` 计数，不是逐行明细。
- **[分组不绑定站点，允许跨站点分组]** → 与 `keyword_group` 的既定模式不同，接受这个
  不一致：`keyword_group` 的行为是查询语义的一部分（站点决定读哪张事实表），而本变更的
  分组只是管理页的归类展示，没有下游查询依赖分组的站点单一性。
- **[`baseline_at` 列在没有数据获取层时是纯粹的占位]** → 接受。列存在不产生维护负担，
  换来后续变更（P3a）落地时不需要一条新迁移加列；风险是如果 P3a 最终设计出不同的基线
  记录方式，这一列会成为死列，需要在那次变更里显式清理或复用说明。
- **[`user_seller_watch` 允许 `group_id` 为 NULL（未分组）]** → 接受，导入时如果解析
  不出候选分组名（纯文本粘贴、非书签来源）应该能落库而不是强制建一个「默认分组」。

## Migration Plan

1. 一条 Alembic 迁移：建三张普通表 + 种入 `competitor:view` 权限点与角色授权。
   `migration_scope.py` 的 `V2_TABLES` 登记 `competitor_seller`/`user_seller_group`/
   `user_seller_watch`/`competitor_watch_preview` 四张（预览表也要登记，否则 autogenerate
   看不见它未来的结构变化）。
2. `alembic upgrade head` 后表是空的，无 backfill。
3. 后端上线（新端点，不影响既有端点）。
4. 前端上线（新菜单项，`access.ts` 新增判断）。
5. 回滚：`alembic downgrade -1` 删四张表 + 撤销权限点授予；前后端各自回退上一版本。
   无数据损失风险——本变更不产生任何 `fact_daily`/`fact_weekly`/`dim_*` 的写入。

按 V2 的既有约定，本变更不单独发布，随 V2 整体上线。

## Open Questions

无——本变更范围内的决策已在 issue #56 逐条定案（权限点新增、分组不合并跨站点卖家、
两步预览/确认、不引入 Redis）。issue 里仍未决的两项（Keepa token 成本、插件后台巡检
风险接受度）都只影响后续的数据获取层变更，与本变更无关。`import_batch` 是否被书签导入
复用——已在设计阶段决定不复用（新建 `competitor_watch_preview`，语义更贴切），不再是
open question。
