# competitor-browser-collector Specification

## Purpose
TBD - created by archiving change add-competitor-browser-collector. Update Purpose after archive.
## Requirements
### Requirement: 只在店铺全品列表页激活

插件的 content script SHALL 只在 Amazon `me=` 店铺全品列表页
（`/s?i=merchant-items&me=<sellerId>&...`）上激活并注入采集入口。

MUST NOT 在其他 Amazon 页面（商品详情页、品牌店铺页、搜索结果页等）激活——本能力
的数据来源仅限"运营手动点开收藏的店铺全品列表"这一种场景，在其他页面激活既拿不到
正确的 `seller_id`，也会让运营困惑"这个按钮为什么在这里出现"。

#### Scenario: 打开一个店铺全品列表页

- **WHEN** 运营打开一个 `me=` 店铺全品列表页
- **THEN** 页面上出现采集入口（如注入的按钮或面板）

#### Scenario: 打开一个商品详情页

- **WHEN** 运营打开一个 `/dp/` 商品详情页
- **THEN** 插件不激活，页面上不出现任何采集入口

### Requirement: 采集当前已加载页面的 ASIN 清单，支持翻页

插件 SHALL 解析当前页面 DOM，提取可见的商品条目（ASIN，可选的标题与主图地址）。
店铺列表分页时，插件 SHALL 支持运营翻页后在新的一页再次触发采集，将多页结果合并
为一次上报。

插件 MUST NOT 承诺"已采集该卖家全部商品"——只表达"在某次触发时，页面上可见这些
ASIN"，与 `competitor-asin-ingest` 的推模型语义一致（采集范围由运营的浏览行为
决定，不由插件或后端决定）。

#### Scenario: 单页采集

- **WHEN** 运营在店铺列表第一页触发采集
- **THEN** 该页可见的全部商品条目被提取，不要求页面已滚动到底部之外的额外前提

#### Scenario: 翻页后再次采集

- **WHEN** 运营翻到第二页并再次触发采集
- **THEN** 第二页的条目被提取并入本次采集结果，不覆盖或丢弃第一页已采集的条目

### Requirement: 解析结果为空时拒绝上报并提示，不静默提交空列表

当一次采集的可见 ASIN 数为零时，插件 SHALL 拒绝发起上报请求，并在页面上给出明显
提示（例如"未识别到任何商品，页面结构可能已变化"）。

MUST NOT 静默提交一个空的 `items` 列表——服务端会把它当作"这次真的没有见到任何
商品"而接受（不会报错），但这种情况的真实原因几乎总是页面解析失败（DOM 结构变化、
页面未加载完成），而不是卖家真的清空了店铺。静默提交会让这个信号永远不被任何人
看到。

#### Scenario: 页面结构变化导致解析失败

- **WHEN** Amazon 调整了页面结构，插件的选择器匹配不到任何商品卡片
- **THEN** 插件不发起上报请求，页面上提示"未识别到任何商品"

### Requirement: 站点识别失败时拒绝生成请求，不猜测默认站点

插件 SHALL 优先从页面 URL 的 `marketplaceID` 查询参数识别站点码，查询参数缺失或
无法识别时 SHALL 回落到页面域名的站点映射。两者都无法识别已知站点时，插件 SHALL
拒绝生成上报请求并提示"未识别的站点"。

MUST NOT 在无法识别站点时回退到某个默认站点——这与 `app/site_registry.py` 的
`normalize_site_code`"非法/缺失站点报错不回退"是同一条纪律：猜错站点码会让数据
写入错误的站点分区，且没有任何报错能揭示这个错误。

#### Scenario: URL 带 marketplaceID

- **WHEN** 页面 URL 包含已知的 `marketplaceID` 参数
- **THEN** 按该参数确定站点码，不再查看域名

#### Scenario: URL 不带 marketplaceID，但域名已知

- **WHEN** 页面 URL 缺少 `marketplaceID` 参数，但域名是已登记站点的域名
- **THEN** 按域名映射确定站点码

#### Scenario: 站点无法识别

- **WHEN** `marketplaceID` 缺失或未登记，且域名也不属于任何已登记站点
- **THEN** 插件拒绝生成上报请求，提示"未识别的站点"，不发起网络请求

### Requirement: 上报请求体符合服务端幂等契约

插件组装的上报请求体 SHALL 包含服务端契约（`competitor-asin-ingest`）要求的全部
必填字段：`request_id`（客户端生成的幂等键）、`seller_id`、`site`、`items`
（去重后的 ASIN 及可选的标题/主图）。SHALL 同时携带 `captured_at`（采集时刻）与
`ship_to_region`（若能获取当前账号的配送地区）供服务端留存审计，即便这些字段不
参与后端的任何判定。

#### Scenario: 完整请求体

- **WHEN** 插件发起一次上报
- **THEN** 请求体包含 `request_id`、`seller_id`、`site`、`items`，且 `items` 内
  不含重复 ASIN

### Requirement: `request_id` 在一次采集结果被冻结为请求体时生成，重试复用、重新采集换新

`request_id` SHALL 在"一次 DOM 解析结果被序列化为待发送的请求体"这个时间点生成
并固定。此后针对**同一份已序列化请求体**的自动重试（网络失败/超时触发）SHALL 复用
这个 `request_id` 与 `captured_at`，MUST NOT 重新生成。

只有当运营重新触发一次采集（重新解析 DOM，无论内容是否变化）时，插件才 SHALL
生成新的 `request_id` 与新的 `captured_at`。

这条要求存在的原因：服务端把"同一 `request_id` 但请求摘要不同"判定为幂等键被
复用并拒绝（409），把"同一 `request_id` 且摘要相同"判定为重放；插件若在自动重试时
误重新生成 `request_id`，会让每次网络抖动都产生一条新的、实为重复的上报记录；若在
运营主动重新采集时误复用了旧值，会让新的观测被服务端当成重放而丢弃。

#### Scenario: 网络失败后自动重试

- **WHEN** 一次上报因网络错误失败，插件按退避策略自动重试
- **THEN** 重试请求携带与首次尝试相同的 `request_id` 与 `captured_at`

#### Scenario: 运营手动重新采集

- **WHEN** 运营在同一个页面上再次点击采集（无论页面内容是否变化）
- **THEN** 本次生成的 `request_id` 与 `captured_at` 与上一次不同

### Requirement: 鉴权令牌读取失败时明确提示，不静默发出无认证请求

插件 SHALL 在发起上报请求前从本地存储读取已保存的访问令牌。令牌不存在时，插件
MUST NOT 发起请求，SHALL 提示运营前往设置页粘贴令牌。

请求返回 401 或 403 时，插件 MUST NOT 重试，SHALL 提示"登录已过期，请重新粘贴
令牌"并引导至设置页。

#### Scenario: 尚未配置令牌

- **WHEN** 运营在未配置令牌的情况下触发采集
- **THEN** 插件不发起网络请求，提示前往设置页配置令牌

#### Scenario: 令牌已过期

- **WHEN** 上报请求返回 401
- **THEN** 插件停止重试，提示重新粘贴令牌

### Requirement: 未关注的卖家或非法请求需要把服务端原因透传给运营，不重试

上报被服务端以 422 拒绝时（卖家不在上报者的关注清单内、上报体本身不合法等），
插件 SHALL 将服务端返回的错误信息原样展示给运营，MUST NOT 自动重试——这类拒绝
不会因为重试而改变结果。

#### Scenario: 上报了未关注的卖家

- **WHEN** 运营在一个未加入自己关注清单的卖家页面触发采集
- **THEN** 插件展示服务端返回的拒绝原因，不自动重试

