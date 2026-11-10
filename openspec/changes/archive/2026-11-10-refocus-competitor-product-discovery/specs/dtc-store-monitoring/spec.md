## MODIFIED Requirements

### Requirement: 独立站监控的目的与边界

系统 SHALL 让运营把竞对的**独立站**（自建站，当前实测全部是 Shopify）纳入监控，
由服务端定时拉取公开的商品目录与畅销排序，产出三类可查询的数据：新品上架、价格变化、
店内销量名次。

与 Amazon 卖家侧（`competitor-storefront-watch`）的根本差异是数据源性质：独立站的上架
时间由数据源直接给出，是事实而非推断，且一次完整拉取即得到全量目录。因此本能力的上新
基线 SHALL 只是「首次完整拿到目录的时刻」这一个站点级时刻（见「上新基线为首次完整目录，
基线之前的商品不产生上新事件」），MUST NOT 引入 Amazon 侧那种按周期建立的基线批次，也
MUST NOT 依赖浏览器插件、用户登录或任何客户端配合。

本能力当前只实现 Shopify。遇到非 Shopify 站点 SHALL 显式拒绝并说明原因，
MUST NOT 静默降级或写入空数据。

**界面上 SHALL 称「Shopify 店铺」，标明「自动检查」**（`competitor-storefront-watch`「当前接入的建站平台命名为「Shopify 店铺」」，#166）。本规格内部沿用「独立站」一词指代这类数据源，但面向用户的文案 MUST NOT 用它暗示支持任意建站平台。改名只涉及展示，MUST NOT 借此改变抓取调度、频率、基线或下架判定。

**「不是 Shopify」与「打不开」SHALL 分开判定**：服务器答复了、只是没有可用的
`/meta.json`（含 4xx），归「不是 Shopify」；只有超时、DNS 解析失败、连接被拒才算
「无法访问」。合并这两类不会报错，表现是用户对着一个好好的站点反复重试——而它其实
永远不会成功。

**加入时被限流（429）SHALL 单独给出「暂时限制了访问频率，请稍后再试」**，MUST NOT 归入
「不是 Shopify」——否则用户再也不会加这个站点。加入是请求路径，MUST NOT 在请求内等待
`Retry-After`。

#### Scenario: 加入时被目标站点限流

- **WHEN** 用户提交一个 Shopify 域名，`/meta.json` 返回 429
- **THEN** 拒绝理由是「暂时限制了访问频率，请稍后再试」，不是「不是 Shopify 建站」；
  批量加入时该条归入拒绝段并附同一原因

#### Scenario: 加入一个非 Shopify 的站点

- **WHEN** 用户提交一个非 Shopify 的域名
- **THEN** 拒绝加入并说明该站点不受支持，MUST NOT 创建站点行或产生一次注定失败的抓取

#### Scenario: 站点访问得通但不是 Shopify

- **WHEN** 用户提交一个可以正常访问、但 `/meta.json` 返回 404 的域名
- **THEN** 拒绝理由是「不是 Shopify 建站」而不是「无法访问该站点」

#### Scenario: 加入一个 Shopify 站点

- **WHEN** 用户提交一个 Shopify 域名
- **THEN** 站点被创建，等待首次拉取；不需要用户安装任何插件或提供任何凭证
