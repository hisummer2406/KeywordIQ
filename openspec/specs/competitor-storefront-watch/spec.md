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

分组的选择与管理 SHALL 摆在主表格**上方**的独立区域，MUST NOT 做成与表格并排的侧栏
——与「我的词库」屏同一约束，理由同样是横向裁掉的列找不回来、纵向多占几行只是滚动一下。

#### Scenario: 打开竞对监控页

- **WHEN** 用户进入竞对监控页
- **THEN** 分组区在表格上方横向排布，表格占据整个内容区宽度

### Requirement: 关注状态展示待建立基线，不隐含未实现的数据获取能力

**本要求限定于 Amazon 卖家行。** 独立站行有真实数据源，适用
`dtc-store-monitoring` 的状态规则，MUST NOT 沿用本要求。

Amazon 卖家行的状态列 SHALL 展示「待建立基线 / 监控中」**两态**之一，且 SHALL 由**该卖家
有没有基线**决定，MUST NOT 由界面写死：尚无任何上报的卖家显示「待建立基线」，已落定基线的
显示「监控中」。

**「已暂停」不在本能力范围内，MUST NOT 出现在状态列。** 它此前作为占位取值预留，但从未有
任何字段或接口能让一行进入该状态——一个永远取不到值的状态只会让读界面的人以为系统能暂停
某个卖家。要做暂停就在私有的 `user_seller_watch` 上加状态与暂停/恢复端点，那是另一次变更。

Amazon 侧的数字列 SHALL 命名为「近 7 日**新发现**」，MUST NOT 命名为「近 7 日上新数」。
被动采集只能证明"浏览器在某个时刻看见了这个 ASIN"，不能证明它何时上架，也不能证明没采到
就是没上新——这与本能力要求 Amazon 侧时间标注为「发现于」而非「上架于」是同一条纪律，
两处措辞 MUST NOT 分叉。独立站有数据源给出的 `published_at`，才继续称「近 7 日上架」。

该列 SHALL 与状态一致：**尚无基线的卖家显示占位符，MUST NOT 显示 0**——0 会被读成"没有
新发现"，而事实是"这个卖家还没有被采集过"；已有基线的卖家显示真实条数（含 0）。**0 的含义
SHALL 是"已采集范围内没有新发现"，MUST NOT 呈现或解释为"这家店没有上新"。**

已有基线的 Amazon 卖家行 SHALL 同屏展示最近一次采集的时刻，并在其陈旧时给出提示。Amazon
侧的覆盖率取决于运营的浏览习惯，"很久没采过"与"确实没有新发现"在界面上必须能分辨；缺了
这一列，一个三个月没被点开的卖家会长期显示「监控中 · 0」，与"一切正常且没有动静"完全一样。

#### Scenario: 新关注的卖家

- **WHEN** 用户刚关注一个 Amazon 卖家，该卖家尚无任何上报
- **THEN** 该行状态显示「待建立基线」，「近 7 日新发现」列显示占位符而非 0 或空白
  数字（0 会被读成"没有新发现"，占位符才准确表达"这个指标还没有数据来源"）

#### Scenario: 已被采集过的卖家

- **WHEN** 某 Amazon 卖家已有上报、基线已落定
- **THEN** 该行状态显示「监控中」，「近 7 日新发现」显示真实条数（可以是 0），并同屏显示
  最近一次采集的时刻

#### Scenario: 很久没被采集的卖家

- **WHEN** 某 Amazon 卖家已有基线，但最近一次采集在很久以前，近 7 日新发现为 0
- **THEN** 界面在显示 0 的同时给出数据陈旧的提示，用户能看出这个 0 来自"没有采集"而非
  "采集了但没有新发现"

#### Scenario: 状态列不出现已暂停

- **WHEN** 用户查看 Amazon 卖家清单的任意一行
- **THEN** 状态只会是「待建立基线」或「监控中」，界面上不存在「已暂停」这个取值

### Requirement: 权限点独立，不与关键词工具共用

竞对监控功能 SHALL 使用独立权限点 `competitor:view`，MUST NOT 复用 `keyword:view`
或任何其他一级菜单的权限点——每个用户的关注池互相独立，不是「全体共用一个开关」的
授权粒度。

`competitor:view` SHALL 授予内置角色 admin、operator、analyst。

#### Scenario: 未授权用户访问

- **WHEN** 一个只有 `analysis:view` 权限的用户请求竞对监控的任意端点
- **THEN** 返回 403

### Requirement: 上新流合并两类来源，但必须标注时间语义的差异

上新流 SHALL 同时展示 Amazon 卖家与独立站的新品，MUST NOT 拆成两个互不相干的页面
——运营要的是一个混合的流。

每一行 SHALL 标注来源。两类来源的时间语义不同，界面 MUST NOT 用同一个措辞呈现：

| 来源 | 时间的含义 | 界面措辞 |
|---|---|---|
| 独立站 | 数据源给出的上架时间，精确到分钟 | 「上架于」 |
| Amazon 卖家 | 我们首次见到该商品的时刻，取决于何时采集到 | 「发现于」 |

把 Amazon 侧的发现时间标注为「上架于」，等于拿「我们发现得晚」冒充「他上得晚」。

两类来源 SHALL 在**同一次查询内合并**后统一排序与分页，MUST NOT 各查一页再在应用层归并
——各来源自己的偏移量与合并后的全局序号对不上，翻页时会重复或漏掉条目，而这种错误在界面上
表现为「某条上新出现两次」，极难归因。

上新流的主列表 SHALL 只包含两类来源的**交集字段**（店铺/卖家、商品、时间、来源、
Amazon 侧另加站点码——见下方约束）；各自独有的字段 MUST NOT 进入主列表——否则主列表会
有一半的列对另一类来源恒为空。

**点开一行查看详情**：独立站已实现，见下方新增 Requirement「点开一行查看独立站商品
详情」。**Amazon 侧仍未实现**——卡在一个未决的数据可得性问题（"最近一次采集时刻"要不要
新增条目级明细表，当前 schema 只记了每个 ASIN *首次*被哪次上报引入），跟踪见 issue #65，
不在本变更范围内。若将来要补，MUST NOT 展示 BSR 与评价数——本能力有意不存储随时间变化的
指标，声称能展示它们就是承诺了一份不存在的数据。

Amazon 行的响应身份 SHALL 携带站点码。共享层的真实粒度是 `(seller_id, site, asin)`，
不是 `(seller_id, asin)`：同一用户可能同时关注同一 seller_id 在不同站点的店，两个站点
出现同一 ASIN 是常态。缺了站点码，这两行在响应与前端 `rowKey` 上无法区分，次级排序键
也不再是全序——同一分钟内的两行会被判成"同一行"，翻页时互相顶替。独立站没有站点这回事，
该列对 DTC 行恒为空。

#### Scenario: 混合来源的上新流

- **WHEN** 用户查看上新流，其中既有独立站新品也有 Amazon 卖家新品
- **THEN** 两类条目在同一个流里按时间排列，各自带来源标记；独立站条目的时间标注为
  「上架于」，Amazon 条目标注为「发现于」

#### Scenario: 翻到第二页

- **WHEN** 用户在两类来源都有条目的上新流里翻到第 2 页
- **THEN** 第 2 页的条目与第 1 页无重复、无遗漏，整体仍按时间倒序连续

#### Scenario: 同一 seller_id 跨站点卖同一 ASIN

- **WHEN** 用户关注的两个 Amazon 站点（如 US、GB）恰好被同一个 seller_id 上报了同一个 ASIN
- **THEN** 上新流中出现两行，各自标出自己的站点码，翻页时两行都不缺失也不重复

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

