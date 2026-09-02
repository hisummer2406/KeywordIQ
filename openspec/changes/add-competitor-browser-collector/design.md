## Context

服务端契约已经在 `add-competitor-amazon-asin-ingest`（PR #64）里完整定义并验收：
`POST /api/competitor/asin-reports`，请求体是 `AsinReportRequest`（见
`app/table/competitor/asin_schemas.py`），鉴权走标准 `Depends(require_permission(competitor:view))`
（`app/table/competitor/asin_api.py`）。本变更是这份契约的**唯一消费者**，产出一个
Chrome 扩展（Manifest V3），不改动任何后端代码。

请求体的关键字段（供本设计后续决策直接引用）：

```
request_id: str          # 幂等键，必填
seller_id: str
site: str                # 站点码，取自 site_registry
captured_at: datetime | None
ship_to_region: str | None
page_context: str | None
items: [{ asin, title?, image_url? }]
```

服务端只接受**上报者本人已关注**的 `(seller_id, site)`，未关注则整个请求 422 拒绝
（`competitor-asin-ingest` 的"上报只接受上报者已关注的卖家"要求）。插件不需要在
上报前预先查询关注列表——直接尝试上报、把拒绝原因展示给运营即可，这样不必在插件里
再实现一份关注列表查询与本地缓存。

## Goals / Non-Goals

**Goals:**

- 运营打开一个已关注卖家的 `me=` 店铺页面时，插件能提取当前页可见的 ASIN 清单并
  成功上报
- 失败（网络、令牌失效、幂等冲突）时运营能看懂发生了什么、该做什么
- 站点识别、请求组装完全符合服务端契约，不需要服务端做任何妥协
- 把"token 从哪来"这个未决决策隔离到一个函数，使其成为将来唯一需要替换的点

**Non-Goals:**

- 不做后台自动巡检、标签页调度（P3b，另立决策）
- 不做 Keepa 集成（P3c，独立路径）
- 不解决插件的正式鉴权方案——本设计只定占位方案的边界，正式方案是另一个决策
- 不做插件的自动化 UI 测试（DOM 解析部分本就无法在 CI 里验证，见决策 4）
- 不上架 Chrome Web Store，不做多浏览器兼容（不考虑 Firefox/Edge 的 API 差异）

## Decisions

### 决策 1：鉴权占位方案——手动粘贴 token，隔离进单一函数

**方案**：插件的 options page 提供一个文本框，运营从已登录的 KeywordIQ 网页里手动
复制一个 JWT（`localStorage` 的 `access_token` 或类似键，具体读取方式由运营自己操作，
插件不做自动读取）粘贴进去，存进 `chrome.storage.local`（键名 `keywordiq_token`）。
所有需要鉴权的请求经由一个函数取值：

```ts
async function getAuthToken(): Promise<string | null> {
  const { keywordiq_token } = await chrome.storage.local.get('keywordiq_token');
  return keywordiq_token ?? null;
}
```

**为什么现在选它，而不是等真正方案定了再开工**：

- 三个候选方案（读网页会话 / 独立 Personal Access Token / 插件内登录）都不影响插件
  其余任何逻辑——DOM 解析、请求组装、幂等键生成、错误处理全部与"token 从哪来"正交，
  唯一耦合点就是这一个函数的实现
- 手动粘贴不需要任何新增后端功能（读网页会话需要 `host_permissions` 且要求两者
  同时打开；Personal Access Token 需要新的签发/吊销后端接口；插件内登录需要插件
  持有密码），今天就能端到端跑通并验收 DOM 解析与上报逻辑是否正确
- 当前 JWT 有效期 24 小时（`AuthService`），意味着运营每天要重新粘贴一次——这是
  已知的、暂时可接受的体验代价，**不是本变更要解决的问题**

**替换路径**：真正方案定下来后，只需要重写 `getAuthToken()` 的实现（以及 options
page 的 UI），不涉及 `content-script.ts`、`background.ts` 的请求组装逻辑。

**风险**：token 明文存在 `chrome.storage.local`（未加密，但仅限本机、需要访问设备
文件系统才能读取，与浏览器扩展的一般存储模型一致）；token 过期后运营会看到 401，
提示文案必须清楚地说"请重新粘贴 token"而不是让请求静默失败（见决策 5）。

### 决策 2：站点码映射表——手工镜像 `site_registry.py`，用测试钉住已知站点集合

`app/site_registry.py` 是站点码的唯一事实来源，但它是后端 Python 模块，插件作为
纯前端 JS/TS 无法直接 import。维护一份手写镜像：

```ts
// extension/src/site-map.ts
// 手工镜像 app/site_registry.py 的 SITE_CATALOG，两边改动必须同步。
export const MARKETPLACE_ID_TO_SITE: Record<string, string> = {
  ATVPDKIKX0DER: 'US',
  A1F83G8C2ARO7: 'GB',   // 英国站，不是 UK——见 site_registry.py 的既有纪律
  A1PA6795UKMFR9: 'DE',
};
export const DOMAIN_TO_SITE: Record<string, string> = {
  'amazon.com': 'US',
  'amazon.co.uk': 'GB',
  'amazon.de': 'DE',
};
```

站点识别优先取 URL 的 `marketplaceID` 参数，查不到时回落到域名 TLD（与 issue #56
记录的既有做法一致——约 303 条历史书签是 UK 域名但不带该参数）。两个表都查不到时，
**插件拒绝生成上报请求并提示"未识别的站点"**，不猜测、不回退到某个默认站点——与
`normalize_site_code` 的"非法/缺失站点报错不回退"是同一条纪律，猜错站点码会让数据
写入错误的站点分区且没有任何报错。

**防漂移手段**：写一条测试（`extension/test/site-map.test.ts`），固定断言当前
`site_registry.py` 里登记的站点集合（US/GB/DE）与 `DOMAIN_TO_SITE`/
`MARKETPLACE_ID_TO_SITE` 的 value 集合一致。这条测试**不能自动读取 Python 源码**
（跨语言、跨仓库结构），只能把当前已知的站点集合作为字面量断言写死——它的意义不是
自动同步，而是"改动这份镜像时至少会被自己的测试看见"，真正的同步纪律仍然要靠人
（tasks.md 收尾一节会写一条"新增站点时该改哪两个文件"的说明，同时补一句到
`app/site_registry.py` 的模块 docstring，指回这份镜像的位置）。

### 决策 3：`request_id` 的生成时机——每次真正重新读取 DOM 才生成新值，网络失败重试复用原值

服务端契约（`competitor-asin-ingest`）把这件事的边界写得很清楚：**幂等重试**要
重发已经序列化的原请求体（同一个 `request_id`）；**重新采集**（哪怕是同一个卖家、
同一批 ASIN）要换新的 `request_id`，因为它是另一次观测。

插件侧的落地：

```
1. content script 解析出本次 DOM 快照 → 生成一个 request_id（crypto.randomUUID()）
   与 captured_at（此刻），连同解析结果一起交给 background script
2. background script 序列化出完整请求体后即固定下来，网络失败/超时的重试
   全部重发这同一份已序列化的请求体（同一个 request_id、同一个 captured_at）
3. 重试次数上限 3 次，指数退避（1s/2s/4s），超过上限后提示运营手动重试
   （手动重试 = 重新点一次插件按钮，会重新执行步骤 1，生成新的 request_id——
   这是"重新采集"，不是"重试"，语义上是对的：运营手动触发时页面很可能已经变化）
```

**关键纪律**：`request_id` 在"一次解析结果被序列化为请求体"这个时间点生成并冻结，
之后的自动重试绝不重新生成；只有回到步骤 1（重新解析 DOM）才会有新值。这与设计
决策的措辞完全对应"重新读取 DOM 后继续复用原 request_id 不是重试"——本设计通过
"先解析、后冻结、重试不重新解析"的结构，让这条边界在代码结构上就是成立的，不依赖
写代码的人记住这条规则。

### 决策 4：DOM 解析——手工维护选择器，无法自动化测试，验收方式是人工真机验证

`me=` 店铺搜索结果页的 DOM 结构（商品卡片、ASIN、标题、主图、分页控件的选择器）
只能对着真实 Amazon 页面写和调——没有任何本地 fixture 或录制的页面快照能保证长期
有效（Amazon 会调整页面结构，选择器需要在插件失效时人工发现并更新）。这不是本变更
可以修复的limitations，而是被动 DOM 采集这个方案本身自带的性质。

**设计上如何降低这个风险的影响面**：

- 选择器集中写在一个模块（`extension/src/parse-storefront.ts`），不散落在多处
- 解析函数是纯函数（输入一段 HTML/Document，输出结构化的 `{ asin, title?,
  image_url? }[]`），可以用**手工保存的一份脱敏 HTML 片段**做单元测试——这份 fixture
  只能覆盖"当前已知的页面结构"，Amazon 改版后 fixture 测试仍然会绿、但真实页面已经
  解析不出数据，**这条限制必须写进 tasks.md 的验收标准，不能让"单测过了"被误读成
  "线上能用"**
- 解析不出任何 ASIN 时，插件在页面上给出明显提示（不静默交一个空列表上报——那会被
  服务端当成"这次真的没看到任何 ASIN"，虽然不会造成数据损坏，但会误导运营以为已经
  成功采集）

**验收方式**：本变更的 tasks.md 显式包含一步人工验证——开发者模式加载扩展、用真实
登录的 Amazon 账号打开一个已关注卖家的 `me=` 页面、确认解析结果与页面肉眼可见的
商品一致、点击上报、在数据库里核实 `competitor_seller_asin` 与 `asin_report` 两张
表确实写入了预期的行。这一步没有可以替代它的自动化测试。

### 决策 5：失败处理——分三类，401/403 与 409 不能表现为"看起来失败了但说不清为什么"

| 情形 | 插件行为 |
|---|---|
| 网络错误/超时 | 按决策 3 的退避重试 3 次；仍失败则提示"网络异常，请稍后重试"并保留本次解析结果（不丢弃，允许运营手动点"重新上报"复用同一份数据） |
| 401/403（令牌失效或无权限） | 不重试（重试无意义）。提示"登录已过期，请重新粘贴 token"并跳转到 options page |
| 409 `competitor.idempotency_key_reused` | 不应该发生（决策 3 的结构保证幂等键与请求体是绑定的一对一），若发生视为插件自身缺陷，提示"上报异常，请刷新页面重试"（= 走一次全新的步骤 1），并在插件内部日志（`chrome.storage.local` 的一个环形缓冲区，供运营截图反馈问题）记录原始响应体 |
| 422（未关注该卖家 / 站点码非法 / 上报体本身不合法） | 提示服务端返回的具体原因（`RejectedItem`/错误消息透传），不重试——重试不会让"未关注"变成"已关注" |

## Risks / Trade-offs

- **[风险] 鉴权占位方案的每日重新粘贴体验差** → 缓解：这是有意的临时状态，proposal
  已明确排除正式方案；真正影响是采纳率，需要产品/运营决定能接受多久的占位期
- **[风险] DOM 选择器随 Amazon 改版失效，且失效不会主动报警** → 缓解：解析不出结果时
  插件给出明显的页面内提示（决策 4），但这是"运营发现"而非"系统告警"；如果这个
  产品要长期维护，未来应该补一条监控（比如后端按卖家统计"最近一次上报据今已过多久"，
  这已经是 `competitor-asin-ingest` 里"最近采集时刻"字段的自然用途，界面已经在展示）
- **[风险] 手写站点映射表与 `site_registry.py` 漂移** → 缓解：决策 2 的固定断言测试
  + 文档指回，但这是人工纪律不是自动化保证，新增站点时如果漏改插件这边，表现是
  "该站点的卖家永远上报失败"而不是静默错误，属于"响亮失败"而非"安静失败"，可接受
- **[权衡] 插件代码不接入现有的前端 CI/门禁** → 这是全新技术栈决定的，`pnpm
  lint`/`tsc`/`vitest` 的门禁范围不覆盖 `extension/` 目录；tasks.md 需要为这个目录
  单独定义"提交前跑什么"（哪怕只是 `tsc --noEmit` 与一份独立的 vitest 配置）

## Open Questions

- 插件的正式鉴权方案（决策 1 的替换目标）——需要产品/运营决定，不在本变更内解决
- P3b 的账号级风险接受度——issue #56 待确认清单里的既有问题，不因本变更而变化
- 站点映射表要不要在插件启动时打一条"当前支持站点：US/GB/DE"的可见提示，让运营
  在遇到不支持的站点时立刻知道是"插件不支持"而不是"我操作错了"——留给实现时按
  UI 便利性决定，不影响数据正确性
