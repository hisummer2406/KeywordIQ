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
  A1F83G8C2ARO7P: 'GB',  // 英国站，不是 UK——见 site_registry.py 的既有纪律；
                          // 末尾 P 不能漏（PR #68 二次复审 P2）
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

### 决策 3：`request_id` 的生成时机——每次真正重新读取 DOM 才生成新值，重试（自动或手动）复用原值

> **本节已按 PR #68 二次复审 P1 的修复重写**：初版实现里 `request_id` 由
> `background.ts` 在每条消息上各自生成，多页采集改成"采集/提交"两步流程后，
> 这个设计在"提交失败、运营不经过重新采集、直接再点一次提交上报"这条路径上
> 出现了裂缝——见下文"关键纪律"之后的说明。

服务端契约（`competitor-asin-ingest`）把这件事的边界写得很清楚：**幂等重试**要
重发已经序列化的原请求体（同一个 `request_id`）；**重新采集**（哪怕是同一个卖家、
同一批 ASIN）要换新的 `request_id`，因为它是另一次观测。

插件侧的落地（`extension/src/capture-accumulator.ts` 的 `freezeRequest`，
`extension/src/content-script.ts` 调用）：

```
1. content script 点"采集本页"解析出本次 DOM 快照，与此前已累积的条目按 ASIN
   合并去重（多页翻页场景，条目持久化在 `chrome.storage.local`），无论合并
   结果是否带来新内容，都把已冻结的 request_id/captured_at 重置为
   null——这是"重新触发一次采集"
2. content script 点"提交上报"时：若当前批次的 request_id/captured_at 还是
   null（尚未提交过），此刻才生成一对新值（crypto.randomUUID() +
   captured_at）并随批次一起持久化（chrome.storage.local）；若已经非 null
   （上一次提交失败、这次是"直接再点提交"，没有经过步骤 1），原样复用
3. 冻结好的 requestId/capturedAt 随 CaptureMessage 一起交给 background，
   background 不生成、只透传给 buildReportRequest——它是无状态的 service
   worker，没有能力区分"这是同一批条目的重新提交"还是"一次全新观测"
4. background 里 sendReport 的自动重试（网络失败/超时，指数退避 1s/2s/4s，
   上限 3 次）全部复用这同一份已序列化请求体，不受影响；自动重试耗尽后，
   运营手动再点一次"提交上报"（没有先点"采集本页"）会走到上面第 2 步的
   "原样复用"分支——这正是本节修复要保证的：不能让"服务端其实已经收到、
   只是响应在重试里全部丢了"这种情况在手动重试时产生第二条观测记录
```

**关键纪律**：`request_id` 在"一批累积条目**首次**被序列化为待发送的请求体"这个
时间点生成并冻结，之后无论是自动重试还是运营手动重新点击提交，只要没有经过
"重新触发一次采集"（点"采集本页"），就必须复用同一对值；只有重新采集（无论合并
结果是否带来新内容）才会作废、在下次提交时重新生成。这与 spec 原文"`request_id`
在一次采集结果被冻结为请求体时生成、重试复用、重新采集换新"完全对应——冻结与
复用的判断逻辑集中在 `freezeRequest` 一个纯函数里（`extension/test/
capture-accumulator.test.ts` 覆盖），不依赖写代码的人在每个调用点记住这条规则。

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

### 决策 6：网络请求发自后台 service worker，不发自 content script；API 地址可配置

content script 负责 DOM 解析、页面内 UI，以及决策 3 的 `request_id`/`captured_at`
冻结（`freezeRequest`，冻结值随批次一起持久化在 `chrome.storage.local`）；解析
结果连同已冻结的这两个字段通过 `chrome.runtime.sendMessage` 交给
background（MV3 service worker），由 background 持有 `getAuthToken()`、执行
`fetch`、做决策 5 的重试/错误分类，再把结果消息传回 content script 展示。
**不在 content script 里直接 `fetch`**：content script 与被注入的 Amazon 页面
共享执行上下文的网络策略认知，容易与页面自身的 CSP/网络行为纠缠；把网络逻辑
集中在 background 也让"重试与错误分类"这条纪律只需要在一个地方（background 的
消息处理器）维护——但幂等键的冻结/复用判断本身留在 content script（PR #68
二次复审 P1 之后的结论）：background 是无状态的 service worker，每条消息都当作
独立事件处理，没有能力判断"这是同一批条目的重新提交"，这个判断只有持有跨消息
持久化状态的一方（content script，靠 `chrome.storage.local`）才做得了。

API 地址（开发环境 `http://localhost:8000`、生产环境实际域名）随 token 一起在
options page 配置、存进 `chrome.storage.local`（键名 `keywordiq_api_base`），
`auth.ts` 提供 `getApiBaseUrl()`/`setApiBaseUrl()`，与 `getAuthToken()` 同构。

**host permission，不是 CORS**（PR #68 四次复审 P1，订正本节此前的结论）：
此前这里推理过"后端 `allow_origins=["*"]`、插件不带 Cookie，因此 background 的
`fetch` 不声明 `host_permissions` 也能靠标准 CORS 规则放行"——这个推理是错的，
且错在对 MV3 权限模型的误判，不是对后端配置的误判。MV3 的 service worker 发起
跨域 `fetch` 前，浏览器网络层先按 `host_permissions`/`optional_host_permissions`
判断这个来源允不允许被访问；不满足时请求在浏览器侧就被拒绝，根本不会发到网络上，
与目标服务器返回什么 CORS 响应头无关（
https://developer.chrome.com/docs/extensions/develop/concepts/network-requests ）。
也就是说此前的实现在任何真机环境下都无法成功提交——本地单测测不出这个问题，
是因为 `report-client.ts` 的 `fetchImpl` 一直被测试替身注入，从未真正经过浏览器
的网络层。

API 地址是用户自填的（本地开发、生产域名皆有可能），不能在 manifest 里固定声明
一个 `host_permissions`。改为声明 `optional_host_permissions: ["http://*/*",
"https://*/*"]`，具体来源只在用户于 options page 点"保存"时按需申请
（`host-permission.ts` 的 `ensureHostPermission`）。拒绝时 SHALL 不落库这次填写
的地址——存一个连不上的地址没有意义，且会让用户以为已经配置成功。

**`ensureHostPermission` 不做"先查 `contains()` 再决定要不要 `request()`"的
短路**（PR #68 五次复审 P1，订正上一轮的实现）：初版在 `request()` 之前
`await` 了一次 `chrome.permissions.contains()`，这个 await 会丢失调用者的
user gesture 上下文——`permissions.request()` 要求必须在用户手势的调用链里
**同步**发起，Chromium 团队确认过"`request()` 前先 `await` 任意异步调用（哪怕
只是只读的 `contains()`）都可能导致这个上下文丢失"（
https://groups.google.com/a/chromium.org/g/chromium-extensions/c/u4txOB87_fo ），
表现为真机上 `request()` 直接报错"must be called during a user gesture"，
弹不出权限对话框，保存流程整个失败。本地单测测不出来：stub 的 `contains()`
是立即 resolve 的内存操作，不会触发真实 Chrome IPC 才有的这个坑。

现在的实现直接调用 `chrome.permissions.request()`，不先查询——Chrome 保证
已授权的来源调用 `request()` 会直接返回 `true` 且不弹窗，这个"跳过重复提示"
的职责本就该由 `request()` 自己承担，不需要调用方用 `contains()` 抢答。
配套约束：`options.ts` 的保存按钮 click handler 从事件触发到调用
`ensureHostPermission` 之间**不能插入任何 await**，这是保住 gesture 上下文
的唯一手段。

### 决策 7：单次上报条数上限——客户端预检并整批拒绝，上限是运行时配置不是协议常量

> 本节是 PR #68 三次复审新增的两项修复（P1 + P2）合起来的设计记录，初版没有
> 这条决策。

服务端 `COMPETITOR_MAX_REPORT_ITEMS`（`.env.example`，默认 200）对单次上报的
条目数整批拒绝、不截断。多页累积（决策 3）理论上可以攒出任意多条目，如果不做
客户端预检，运营翻完好几页之后点"提交上报"才会撞上一个 422——而且这份"超限"
批次此时已经无法拆分成可提交的更小批次（决策 3 的合并结果是一个整体）。

**预检本身**（`extension/src/capture-accumulator.ts` 的 `mergeItemsWithCap`）：
每次"采集本页"合并前先算一次合并后的条目数，若会超过上限，整批拒绝这一页的
并入、保留此前已累积的条目不变——与服务端"超限整批拒绝，不截断"同一条纪律，
不做部分合并制造一份"看起来采集完整、实际悄悄丢了几个商品"的批次。

**撞上限之后是硬性阻止，不是"建议先提交部分"**（PR #68 三次复审 P1）：初版的
提示是"请先点提交上报，提交已采集的部分"，这条提示本身自相矛盾——同一段文案
里又承认分批提交会污染基线。问题在于：这批不完整的条目一旦被提交，就会给该
卖家落定基线；运营大概率会紧接着继续采集剩下的商品并再次提交，那一批因为
`first_seen_at > baseline_at` 会被判成"新发现"，与决策 1 提到的"手动重试换新
request_id"是完全不同的另一条基线污染路径（决策 3 修的是"同一批条目重复提交"，
这里是"同一个卖家的第二批条目"）。插件目前没有能安全分批建立基线的协议，所以
选择在这里硬性止步：撞上限后 `pending.capped` 落为 `true`，"采集本页"与
"提交上报"两个按钮一起锁死，只能点"清空已采集，重新开始"放弃这次采集。这是
一个已知的产品限制（记在 `extension/README.md`），不是本变更要解决的场景。

**上限本身是运行时配置，插件不能把默认值 200 当协议常量写死**（PR #68 三次
复审 P2）：部署方可以把 `COMPETITOR_MAX_REPORT_ITEMS` 改成任意正整数，插件若
硬编码 200，服务端调低时插件会等到提交才收到迟来的 422，服务端调高时插件又会
过早拒绝、白白把能一次提交的采集拆成不必要的多次。本变更的既有非目标是"不改动
任何后端代码"（见 Non-Goals），因此没有新增一个只读配置端点的选项；退而求其次
让运营在 options page 里手填一份镜像值（`auth.ts` 的
`getMaxReportItems()`/`setMaxReportItems()`，未配置时兜底 200），与 API 地址、
token 走同一条本地配置路径。**这是已知的漂移风险**——运营不去手动同步这个值，
插件的判断就会继续基于过期的镜像，表现是"响亮失败"（提交时收到 422 或过早拒绝）
而不是"安静的数据错误"，可接受程度与决策 2 的站点映射漂移风险同一类。

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
- **[权衡] 插件代码起初未接入 GitHub Actions**（PR #68 二次复审 P2 已修）→
  本地提交门禁（`.claude/hooks/pre_commit.py` 的 `EXTENSION_PREFIX` 判据）能挡住
  经这个 harness 提交的改动，但挡不住其他智能体或直接 `git push` 的场景；
  `.github/workflows/ci.yml` 现已新增 `extension` job（typecheck + test +
  build），结构照抄既有的 `frontend` job
- **[风险] 单批上限的镜像值可能与服务端运行时配置漂移**（决策 7）→ 缓解：与决策 2
  的站点映射同一类风险——人工纪律而非自动化保证，表现是"响亮失败"（提交时收到
  迟来的 422，或翻页翻到一半就被过早拒绝）而非安静的数据错误，可接受
- **[产品限制] 单个卖家商品数超过单批上限时，插件目前无法完整采集这家店**（决策 7）
  → 这不是本变更范围内要解决的场景：分批提交会重新引入基线污染，本变更选择在
  撞上限时硬性阻止提交、要求运营放弃这次采集，而不是设计一个更复杂的安全分批
  协议——真要支持大店铺需要服务端配合（比如允许在同一个基线批次内分片提交），
  是另一个决定

## Open Questions

- 插件的正式鉴权方案（决策 1 的替换目标）——需要产品/运营决定，不在本变更内解决
- P3b 的账号级风险接受度——issue #56 待确认清单里的既有问题，不因本变更而变化
- 站点映射表要不要在插件启动时打一条"当前支持站点：US/GB/DE"的可见提示，让运营
  在遇到不支持的站点时立刻知道是"插件不支持"而不是"我操作错了"——留给实现时按
  UI 便利性决定，不影响数据正确性
- **`ship_to_region` 本次实现未采集，字段留空（`undefined`）。** 该字段是"配送
  地区码"，schema 限长 8 字符，暗示的是一个短码（国家码/邮编一类），但店铺列表页
  上肉眼可见的送货地址信息是完整文本（如"Deliver to United States"），不是短码，
  也不确定页面上哪个元素能可靠给出一个符合"码"这个形态的值——这需要对着真实登录
  会话核实，本仓库做不到（与决策 4 同一类限制）。该字段本就是可选的审计字段、
  不参与任何判定，留空不影响数据正确性，只是暂时失去"这批数据是在什么配送地区
  设置下采的"这层审计能力。tasks.md 第 8 节真机验证时应顺手确认页面上有没有
  合适的短码来源，有就在此后补一个小改动接上，没有就把这条 Open Question
  改写成"确认为不可得，接受这个信息缺口"
