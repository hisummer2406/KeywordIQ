# competitor-storefront-watch Specification

## Purpose
TBD - created by archiving change add-competitor-storefront-watch. Update Purpose after archive.
## Requirements
### Requirement: 卖家主体全局共享，关注关系按用户私有

系统 SHALL 维护一张全局共享的卖家主体表 `competitor_seller`（`UNIQUE(seller_id,
marketplace)`），MUST NOT 携带账号属性——同一卖家被多个用户关注时 SHALL 仍是同一行。

用户对卖家的关注关系 SHALL 记录在私有表 `user_seller_watch`
（`UNIQUE(user_id, seller_id, marketplace)`），MUST NOT 混入共享表。

#### Scenario: 两个用户关注同一卖家

- **WHEN** 用户甲与用户乙先后关注同一个 `(seller_id, marketplace)`
- **THEN** `competitor_seller` 中只有一行，`user_seller_watch` 中出现两行，各自归属
  甲、乙

#### Scenario: 跨用户不可见

- **WHEN** 用户乙请求用户甲的分组或关注记录（直接构造 id）
- **THEN** 返回 404，且响应中不包含该记录的任何信息

### Requirement: 分组归属用户，不绑定站点

系统 SHALL 提供竞对分组，每个分组归属**一个用户**。分组 MUST NOT 绑定站点——同一分组
内允许出现不同 `marketplace` 的卖家关注记录。

分组 SHALL 同时容纳两类成员：Amazon 卖家关注记录与独立站关注记录
（见 `dtc-store-monitoring`）。分组是纯标签，MUST NOT 因成员类型而分裂成两套分组体系
——运营的心智是「我在盯这一批竞对」，不是「我在盯这一批亚马逊卖家」和「我在盯这一批
独立站」。

同一用户的分组名 SHALL 唯一；跨用户同名 SHALL 允许。

一条关注记录 SHALL 至多属于一个分组（`group_id` 可为 NULL，表示未分组）。

#### Scenario: 建组

- **WHEN** 用户新建分组「头部竞对」
- **THEN** 分组创建成功；此后可以把 US、UK、DE 站点的卖家关注记录都放进这一个分组

#### Scenario: 同一分组混装两类成员

- **WHEN** 用户把 3 个 Amazon 卖家与 2 个独立站放进同一个分组「头部竞对」
- **THEN** 该分组同时包含这 5 个成员，界面按成员类型分别渲染其状态与指标列

#### Scenario: 同名冲突

- **WHEN** 用户已有名为「头部竞对」的分组，再次以同名建组
- **THEN** 返回 409，错误信息说明该用户下已有同名分组

#### Scenario: 删除分组不取消关注

- **WHEN** 用户删除一个含 50 个卖家关注记录的分组
- **THEN** 分组被删除，但这 50 条关注记录仍然存在，仅 `group_id` 变为空（未分组），
  用户仍然关注着这些卖家

#### Scenario: 删除混装分组

- **WHEN** 用户删除一个同时含 Amazon 卖家关注与独立站关注的分组
- **THEN** 两类关注记录都存活并变为未分组，两类都不因删组而取消关注

### Requirement: SellerRefParser 从多种输入形态提取卖家标识

系统 SHALL 提供一个不依赖数据库的纯函数解析器，从用户粘贴的文本或书签 HTML 中提取
`seller_id` 与 `marketplace`。

解析器 SHALL 支持以下输入形态：`i=merchant-items&me=` 链接、`/sp?seller=` 链接、
`/gp/help/seller/at-a-glance.html?seller=` 链接、裸卖家 ID（正则 `^A[A-Z0-9]{11,19}$`）。

解析器 SHALL 拒绝以下形态并说明原因，MUST NOT 静默丢弃：品牌店铺页（`/stores/...`，
无法提取卖家 ID）、商品详情页（`/dp/...`，无法提取卖家 ID）。

站点识别 SHALL 优先取 URL 的 `marketplaceID` 参数并映射为 `app/site_registry.py` 的
站点码，缺失时 SHALL 回落到域名 TLD；两条路径 MUST NOT 各自维护一套站点码。

原始 URL 中的会话签名参数（如 `ds=`）SHALL 被丢弃，MUST NOT 存入数据库或用于重建请求
——展示链接 SHALL 由 `seller_id`/`marketplace` 现拼模板 URL。

#### Scenario: 解析带会话签名的收藏链接

- **WHEN** 输入 `https://www.amazon.com/s?i=merchant-items&me=A1TZ946F8HZTNY&ds=v1%3A...`
- **THEN** 提取出 `seller_id=A1TZ946F8HZTNY`、`marketplace=US`，`ds=` 参数不出现在
  任何存储或响应中

#### Scenario: 解析不支持的链接形态

- **WHEN** 输入一个 `/stores/Brand/page/UUID` 形式的品牌店铺链接
- **THEN** 该条被归入解析失败列表，附带原始文本与「品牌店铺页无法提取卖家 ID」的说明，
  MUST NOT 被丢弃且不出现在错误信息里

#### Scenario: marketplaceID 缺失时回落域名

- **WHEN** 输入一条不带 `marketplaceID` 参数、域名为 `amazon.co.uk` 的链接
- **THEN** 站点识别为 `GB`（不是 `UK`）

#### Scenario: 裸 ID 需要显式指定默认站点

- **WHEN** 用户粘贴一个裸卖家 ID，且预览请求未提供 `fallback_site`
- **THEN** 该条判为解析失败，原因说明「裸卖家 ID 不带站点信息，需要指定默认站点」，
  MUST NOT 静默归入某个默认站点

#### Scenario: 提供 fallback_site 后裸 ID 可解析

- **WHEN** 用户粘贴一个裸卖家 ID，且预览请求提供 `fallback_site=US`
- **THEN** 该条解析为该卖家 ID + `US`

### Requirement: 导入走预览再确认两步，不允许一次性直接落库

批量添加卖家（粘贴文本或上传书签文件）SHALL 先调用预览接口，只解析、MUST NOT 写入
任何表；预览响应 SHALL 包含解析总数、新增数、已存在数、解析失败列表（附原文与原因）、
识别出的候选分组、抽样结果（不超过 20 条）。

预览结果 SHALL 存入数据库表并设置过期时间，MUST NOT 使用 Redis 或其他新增的缓存基础
设施。确认接口 SHALL 校验预览 token 未过期且归属当前用户，否则拒绝。

确认接口 SHALL 支持两种模式：`append`（默认，新增记录 + 已存在记录仅更新
`priority`，MUST NOT 覆盖已有的 `group_id`/`note`）与 `replace_group`（先清空目标
分组下的全部关注记录再按预览结果重建）。

#### Scenario: 预览不写库

- **WHEN** 用户上传一份书签文件调用预览接口
- **THEN** 响应返回解析统计与抽样，`competitor_seller`/`user_seller_watch` 均无新增行

#### Scenario: append 模式不覆盖已整理的分组

- **WHEN** 用户对已关注且已手动分组的卖家再次导入（该卖家在新导入中出现）
- **THEN** 确认后该卖家的 `group_id`/`note` 保持不变，仅 `priority` 更新

#### Scenario: replace_group 模式对齐最新导出

- **WHEN** 用户选择 `replace_group` 并指定某个已有分组
- **THEN** 该分组下原有的关注记录被清空，代之以本次预览结果；`competitor_seller` 中
  的卖家主体不受影响

#### Scenario: 过期的预览 token 不可确认

- **WHEN** 用户使用一个已超过 `expires_at` 的 `preview_token` 调用确认接口
- **THEN** 返回错误，不发生任何写入，且提示需要重新预览

### Requirement: 关注数与单次导入解析数有明确上限且超限拒绝

系统 SHALL 限制每用户的关注总数（`MAX_WATCH_PER_USER`）与单次预览可解析的条数
（`MAX_IMPORT_BATCH`）。超限 SHALL 在**预览阶段**即返回错误并说明当前数与上限，
MUST NOT 等到确认阶段才失败，MUST NOT 静默截断。

#### Scenario: 预览阶段命中总量上限

- **WHEN** 用户已关注 2990 个卖家，上传的书签会新增 50 个
- **THEN** 预览接口返回错误，说明关注总数将超过上限，不进入可确认状态

### Requirement: 分组管理不得占用主表格的横向宽度

店铺管理页中，分组的选择与管理 SHALL 摆在主表格**上方**的独立区域；商品发现页中，分组 SHALL 以筛选控件的形式出现在列表上方。两处都 MUST NOT 做成与表格并排的侧栏
——与「我的词库」屏同一约束，理由同样是横向裁掉的列找不回来、纵向多占几行只是滚动一下。左侧已有任务菜单（`top-nav-structure`「竞对监控以任务菜单组织」），再叠一条分组侧栏会让表格被挤两次。

#### Scenario: 打开竞对监控页

- **WHEN** 用户进入店铺管理页
- **THEN** 分组区在表格上方横向排布，表格占据除任务菜单外的整个内容区宽度

### Requirement: 权限点独立，不与关键词工具共用

竞对监控功能 SHALL 使用独立权限点 `competitor:view`，MUST NOT 复用 `keyword:view`
或任何其他一级菜单的权限点——每个用户的关注池互相独立，不是「全体共用一个开关」的
授权粒度。

`competitor:view` SHALL 授予内置角色 admin、operator、analyst。

#### Scenario: 未授权用户访问

- **WHEN** 一个只有 `analysis:view` 权限的用户请求竞对监控的任意端点
- **THEN** 返回 403

### Requirement: 独立站监控不新增权限点

独立站监控 SHALL 沿用 `competitor:view`，MUST NOT 新增独立的权限点。

「能盯 Amazon 卖家但不能盯独立站」不是一个真实存在的授权需求；为它开权限点会重蹈
`user:manage` 那种「可授予但无实际区分意义」的复选框。

#### Scenario: 已有 competitor:view 的用户

- **WHEN** 一个拥有 `competitor:view` 的用户请求独立站监控的任意端点
- **THEN** 请求被授权，无需任何额外权限

#### Scenario: 无 competitor:view 的用户

- **WHEN** 一个只有 `analysis:view` 的用户请求独立站监控的任意端点
- **THEN** 返回 403

### Requirement: 点开一行查看独立站商品详情

用户点开上新流中一条独立站（DTC）来源的行时，界面 SHALL 展示该商品的变体价格历史与
店内相对名次历史，MUST NOT 呈现为图表——价格观测点"仅在取值变化时追加"，在时间轴上
不均匀分布，套用等距点的图表会误导调价频率本身；改为按时间倒序陈列的表格。

价格历史的每一行 SHALL 包含观测时刻、变体标识、价格、划线价（如有）、是否在售。
店内名次历史 MUST NOT 呈现为销量或用于跨店比较——数据源不提供任何绝对销量，这是
`competitor-storefront-watch` 既有约束（「销量只有店内相对名次，没有绝对数字」），
本详情视图延续同一措辞。

点开 Amazon 来源的行 SHALL 不触发本能力——Amazon 侧的详情仍是已知缺口，见上方
「上新流合并两类来源」Requirement 的说明与 issue #65。

#### Scenario: 独立站商品有多次调价记录

- **WHEN** 用户点开一条独立站新品，该商品在监控期间发生过多次调价
- **THEN** 详情按时间倒序展示每一次价格变化，包含变化前后的价格与划线价

#### Scenario: 独立站商品自监控以来价格从未变化

- **WHEN** 用户点开一条独立站新品，该商品自首次拉取以来价格从未变化
- **THEN** 详情展示唯一一条记录（首次拉取时的价格），不显示为空

#### Scenario: 点开 Amazon 来源的行

- **WHEN** 用户点开上新流中一条 Amazon 来源的行
- **THEN** 不触发详情展示——本能力仅覆盖独立站来源

### Requirement: 点开一行查看 Amazon 卖家 ASIN 详情

用户点开上新流中一条 Amazon 来源的行时，界面 SHALL 展示该 ASIN 的首次发现时刻
（`CompetitorSellerAsin.first_seen_at`，真实数据），并 SHALL 附带该卖家最近一次被
采集的时刻（卖家级近似值，与卖家清单「最近采集于」列同一来源）。

展示最近采集时刻时，界面 MUST NOT 让用户误读成"这个商品本身的采集时刻"——该时刻是
卖家级的，同一卖家名下其他 ASIN 共享同一个值，措辞 SHALL 明确说明这一点。

MUST NOT 展示 BSR 与评价数——本能力有意不存储随时间变化的指标。

私有性 SHALL 由请求者是否关注该卖家决定：非本人关注的卖家一律 404，不是 403。

#### Scenario: 已关注卖家的 ASIN 详情

- **WHEN** 用户点开一条属于自己已关注卖家的 Amazon 新品行
- **THEN** 详情展示该 ASIN 的首次发现时刻，以及该卖家最近一次采集时刻，附带"这是卖家级
  近似值、不代表本商品采集时刻"的说明

#### Scenario: 卖家从未被再次采集

- **WHEN** 该卖家自基线建立后只被采集过一次（即首次上报即当前唯一一次）
- **THEN** 最近采集时刻等于首次发现时刻附近的那次上报时间，不显示为空

#### Scenario: 非本人关注的卖家

- **WHEN** 用户尝试查看一个自己未关注的卖家的 ASIN 详情
- **THEN** 返回 404，不返回 403

### Requirement: Amazon 卖家行展示手动采集与三态数据状态

**本要求只适用于 Amazon 卖家行。** Shopify 店铺行适用 `dtc-store-monitoring` 的状态规则。

Amazon 卖家行 SHALL 标明采集方式为「手动采集」，并说明数据随运营在浏览器里用插件采集而更新、后台不会自动检查。MUST NOT 用无说明的「监控中」暗示后台在巡检。

状态 SHALL 由后端根据数据计算后下发，MUST NOT 在界面写死，取值为三态之一：

| 状态 | 判据 | 含义 |
|---|---|---|
| 未采集 | 该卖家没有基线 | 收藏只代表加入关注清单，还没有任何数据 |
| 已采集 | 有基线，且最近一次上报在 `NEW_DISCOVERY_WINDOW_DAYS` 天内 | 近期有人工采集 |
| 数据陈旧 | 有基线，但最近一次上报早于该窗口 | 「0 新发现」可能只是因为很久没采集 |

「已暂停」MUST NOT 出现，理由与原有约定相同：没有任何字段或接口能让一行进入该状态。

数字列 SHALL 命名为「近 7 日**新发现**」，MUST NOT 叫「上新数」。没有基线的卖家 SHALL 显示占位符，MUST NOT 显示 0。有基线时显示真实条数（含 0），0 的含义 SHALL 是「已采集的范围内没有新发现」，MUST NOT 被解释为「这家店没有上新」。

有基线的行 SHALL 同屏展示最近一次采集时刻，以及那次上报的条目数。条目数 SHALL 注明「不代表全店总量」，MUST NOT 呈现为「全店采集完成」或店铺在售总数：被动采集没有完整性依据，上报多少条取决于运营翻了几页。

行内 SHALL 提供打开该卖家 Amazon 商品列表页的链接，以及采集步骤说明。说明 SHALL 写明数据只随人工采集更新，且手动翻页同样可能触发站点的访问限制，MUST NOT 宣称手动采集没有风险。

#### Scenario: 刚收藏的卖家

- **WHEN** 用户刚关注一个 Amazon 卖家，该卖家还没有任何上报
- **THEN** 状态显示「未采集」，采集方式显示「手动采集」，近 7 日新发现显示占位符而不是 0，最近采集显示「未采集」

#### Scenario: 近期采集过的卖家

- **WHEN** 某卖家 3 天前有过一次上报，那次 480 条
- **THEN** 状态显示「已采集」，最近采集显示时刻和「上报 480 条（不代表全店总量）」，近 7 日新发现显示真实条数

#### Scenario: 很久没采集的卖家

- **WHEN** 某卖家有基线，但最近一次上报在 30 天前，近 7 日新发现为 0
- **THEN** 状态显示「数据陈旧」，用户能看出这个 0 来自「没有采集」而不是「采集了但没有新发现」

#### Scenario: 状态不出现已暂停

- **WHEN** 用户查看任意一行 Amazon 卖家
- **THEN** 状态只会是「未采集」「已采集」「数据陈旧」之一

### Requirement: 商品发现合并两类来源，标注时间语义，筛选在同一次查询内生效

商品发现（原「上新流」）SHALL 在同一个列表里展示已关注的 Amazon 卖家与 Shopify 店铺在纳入监控后新出现的商品，MUST NOT 拆成两个互不相干的页面。基线之前的存量商品 MUST NOT 进入列表（判据分别见 `competitor-asin-ingest` 与 `dtc-store-monitoring` 的基线要求）。

两类来源 SHALL 在**同一次查询内合并**，再统一排序和分页，MUST NOT 各查一页再在应用层归并。各来源自己的偏移量与合并后的全局序号对不上，翻页时会重复或漏掉条目。

**筛选** SHALL 包括：时间范围、来源（Amazon / Shopify）、Amazon 站点、分组、单个店铺。每个筛选条件 SHALL 传到后端，在合并之前分别施加到两侧，列表与总数 SHALL 基于同一个合并后的子查询计算。MUST NOT 只在前端过滤当前页，那样总数和页数会与实际可看到的行对不上。选了 Amazon 站点时，Shopify 侧 SHALL 不参与（Shopify 没有站点概念）。分组与店铺筛选 SHALL 只在当前用户自己的关注范围内生效。

**每一行** SHALL 包含：主图、标题、来源店铺名、平台与站点、商品标识（Amazon 为 ASIN，Shopify 为商品 ID）、时间，以及原商品链接（Amazon 为该站点域名下的 `/dp/{ASIN}`，Shopify 为 `/products/{handle}`）。图片、标题、店名缺失时 SHALL 如实降级：没有图显示占位图，没有标题显示商品标识，没有店名显示 seller_id 或域名，MUST NOT 编造内容。Amazon 的店名 MUST NOT 用用户私有的备注顶替。

主列表 SHALL 只放两类来源都有的字段，各自独有的字段（变体、价格、采集元数据等）MUST NOT 进入主列表。

两类来源的时间语义不同，界面 MUST NOT 用同一个措辞：

| 来源 | 时间 | 界面措辞 |
|---|---|---|
| Shopify 店铺 | 数据源给出的上架时间（有值时）+ 首次发现时刻 | 「上架于」+「首次发现于」 |
| Amazon 卖家 | 首次见到该商品的时刻，取决于何时采集到 | 「首次发现于」 |

Amazon 的「首次发现于」MUST NOT 被说成上架时间。首次采集漏掉、之后补采到的老商品，同样会以「首次发现于」进入列表，界面 SHALL 说明这不能证明它是真正的新品。

Amazon 行的身份 SHALL 包含站点码，排序的次级键 SHALL 包含站点码。共享层的真实粒度是 `(seller_id, site, asin)`，缺了站点码，同一 seller_id 在两个站点卖同一个 ASIN 时两行无法区分，翻页时会互相顶替。

MUST NOT 展示 BSR 与评价数，本能力有意不存储随时间变化的指标。

#### Scenario: 混合来源

- **WHEN** 列表中既有 Shopify 店铺的新商品，也有 Amazon 卖家的新发现
- **THEN** 两类条目在同一个列表里按时间排列，各自带平台标记；Shopify 条目显示「上架于」和「首次发现于」，Amazon 条目只显示「首次发现于」

#### Scenario: 只看 Amazon 的某个站点

- **WHEN** 用户选择来源「Amazon」、站点「DE」
- **THEN** 列表只包含 DE 站卖家的新发现，总数等于这些行的条数，不包含任何 Shopify 条目

#### Scenario: 按分组筛选后翻页

- **WHEN** 用户选择某个分组，结果有 45 条，每页 20 条
- **THEN** 总数显示 45，三页合计正好 45 条，没有重复也没有遗漏

#### Scenario: 看品与打开原商品

- **WHEN** 用户查看一条 Amazon 新发现
- **THEN** 能看到主图、标题、ASIN、店名（取不到时显示 seller_id），点击链接在新窗口打开该站点的商品页

#### Scenario: 缺少图片和标题

- **WHEN** 某条 Amazon 新发现上报时没有带标题和图片
- **THEN** 该行显示占位图，标题位置显示 ASIN，不显示空白，也不显示编造的内容

#### Scenario: 同一 seller_id 跨站点卖同一 ASIN

- **WHEN** 用户关注的 US、GB 两个站点被同一个 seller_id 上报了同一个 ASIN
- **THEN** 出现两行，各自标明站点码，翻页时两行都不缺失也不重复

### Requirement: 店铺管理在一页内按店铺类型切换

店铺管理 SHALL 在同一页内用店铺类型切换「Amazon 卖家」与「Shopify 店铺」，切换状态 SHALL 写在 URL 中，刷新后保留。添加或导入、分组、备注、取消关注 SHALL 跟随当前类型，复用各自现有的接口。两类的关注上限、导入流程与批量加入规则保持原有约定不变。

分组 SHALL 是两类共用的同一套标签，MUST NOT 因为类型切换分裂成两套。

#### Scenario: 刷新后停留在当前类型

- **WHEN** 用户在店铺管理切换到「Shopify 店铺」后刷新页面
- **THEN** 仍停在「Shopify 店铺」

#### Scenario: 同一个分组下两类成员

- **WHEN** 用户把一个 Amazon 卖家和一个 Shopify 店铺放进同一个分组
- **THEN** 两种类型下选择该分组，分别列出对应的成员；删除分组不会取消任何关注

### Requirement: 点开 Amazon 卖家查看已采集到的商品

店铺管理中点开一个 Amazon 卖家，SHALL 分页列出该卖家在该站点下已采集到的商品，按首次发现时刻倒序，每行包含主图、标题、ASIN、首次发现于，并标明是否属于基线批次（首次采集时就已存在）。同时 SHALL 展示采集步骤说明。

列表 SHALL 只读，来自现有的共享观测，MUST NOT 新建一套产品库或复制商品数据。列表条数 MUST NOT 被表述为该卖家的全部在售商品。

私有性 SHALL 由请求者是否关注该卖家决定：不是本人的关注一律 404，不返回 403。

#### Scenario: 查看已采集商品

- **WHEN** 用户点开自己关注的、已采集过两次的卖家
- **THEN** 看到两次采集合计见过的全部商品，首批标为「基线」，之后新出现的不带该标记

#### Scenario: 查看他人的关注

- **WHEN** 用户构造另一个用户的 `watch_id` 请求已采集商品
- **THEN** 返回 404，不泄露任何商品信息

### Requirement: 当前接入的建站平台命名为「Shopify 店铺」

界面、插件说明和用户文档中，凡是指当前接入平台的地方 SHALL 称「Shopify 店铺」，MUST NOT 用「独立站」这类会让人以为支持任意建站平台的泛称。Shopify 店铺 SHALL 标明采集方式为「自动检查」，与 Amazon 的「手动采集」区分开。接口路径、内部枚举（`DTC`）和表名不在这条要求范围内。

#### Scenario: 店铺类型与来源标签

- **WHEN** 用户查看店铺管理的类型切换，以及商品发现的来源筛选
- **THEN** 选项是「Amazon 卖家」和「Shopify 店铺」，界面上不出现「独立站」

#### Scenario: 加入一个非 Shopify 站点

- **WHEN** 用户尝试加入一个不是 Shopify 的站点
- **THEN** 按 `dtc-store-monitoring` 的现有规则拒绝，提示文案说明只支持 Shopify 店铺
