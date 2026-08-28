## ADDED Requirements

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

系统 SHALL 提供卖家分组，每个分组归属**一个用户**。分组 MUST NOT 绑定站点——同一分组
内允许出现不同 `marketplace` 的卖家关注记录。

同一用户的分组名 SHALL 唯一；跨用户同名 SHALL 允许。

一条关注记录 SHALL 至多属于一个分组（`group_id` 可为 NULL，表示未分组）。

#### Scenario: 建组

- **WHEN** 用户新建分组「头部竞对」
- **THEN** 分组创建成功；此后可以把 US、UK、DE 站点的卖家关注记录都放进这一个分组

#### Scenario: 同名冲突

- **WHEN** 用户已有名为「头部竞对」的分组，再次以同名建组
- **THEN** 返回 409，错误信息说明该用户下已有同名分组

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

### Requirement: 权限点独立于工具箱

竞对监控功能 SHALL 使用独立权限点 `competitor:view`，MUST NOT 复用 `toolbox:view`
——每个用户的关注池互相独立，不是工具箱式的「全体共用一个开关」的授权粒度。

`competitor:view` SHALL 授予内置角色 admin、operator、analyst。

#### Scenario: 未授权用户访问

- **WHEN** 一个只有 `analysis:view` 权限的用户请求竞对监控的任意端点
- **THEN** 返回 403

### Requirement: 分组管理不得占用主表格的横向宽度

分组的选择与管理 SHALL 摆在主表格**上方**的独立区域，MUST NOT 做成与表格并排的侧栏
——与「我的词库」屏同一约束，理由同样是横向裁掉的列找不回来、纵向多占几行只是滚动一下。

#### Scenario: 打开竞对监控页

- **WHEN** 用户进入竞对监控页
- **THEN** 分组区在表格上方横向排布，表格占据整个内容区宽度

### Requirement: 关注状态展示待建立基线，不隐含未实现的数据获取能力

主表格的状态列 SHALL 展示「待建立基线 / 监控中 / 已暂停」三态之一。本能力范围内
（无数据获取层）SHALL 恒为「待建立基线」，MUST NOT 显示为「监控中」或任何暗示已有
上新数据的状态。

#### Scenario: 新关注的卖家

- **WHEN** 用户刚关注一个卖家
- **THEN** 该行状态显示「待建立基线」，「近 7 日上新数」列显示占位符而非 0 或空白
  数字（0 会被读成"没有新品"，占位符才准确表达"这个指标还没有数据来源"）
