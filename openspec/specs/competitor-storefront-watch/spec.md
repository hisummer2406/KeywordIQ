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

Amazon 卖家行的状态列 SHALL 展示「待建立基线 / 监控中 / 已暂停」三态之一。在 Amazon
侧数据获取层落地之前 SHALL 恒为「待建立基线」，MUST NOT 显示为「监控中」或任何暗示
已有上新数据的状态。

两类行同处一张表时，界面 SHALL 让用户能看出某一行的指标是否有数据来源——独立站行显示
真实的近 7 日上新数，Amazon 卖家行显示占位符，二者 MUST NOT 呈现为同一种视觉状态。

#### Scenario: 新关注的卖家

- **WHEN** 用户刚关注一个 Amazon 卖家
- **THEN** 该行状态显示「待建立基线」，「近 7 日上新数」列显示占位符而非 0 或空白
  数字（0 会被读成"没有新品"，占位符才准确表达"这个指标还没有数据来源"）

#### Scenario: 两类行同屏

- **WHEN** 用户查看同时含 Amazon 卖家与独立站的清单
- **THEN** 独立站行显示真实的近 7 日上新数（含 0），Amazon 卖家行显示占位符；
  用户能据此分辨「这家确实没上新」与「这家还没有数据来源」

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

上新流的主列表 SHALL 只包含两类来源的**交集字段**（店铺/卖家、商品、时间、来源）；
各自独有的字段（独立站的变体与价格、Amazon 的 BSR 与评价数）SHALL 放入详情，
MUST NOT 进入主列表——否则主列表会有一半的列对另一类来源恒为空。

#### Scenario: 混合来源的上新流

- **WHEN** 用户查看上新流，其中既有独立站新品也有 Amazon 卖家新品
- **THEN** 两类条目在同一个流里按时间排列，各自带来源标记；独立站条目的时间标注为
  「上架于」，Amazon 条目标注为「发现于」

#### Scenario: 查看条目详情

- **WHEN** 用户点开一条独立站新品条目
- **THEN** 详情中展示其变体与价格；点开 Amazon 条目时详情展示 BSR 与评价数，
  两者的主列表列保持一致

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
