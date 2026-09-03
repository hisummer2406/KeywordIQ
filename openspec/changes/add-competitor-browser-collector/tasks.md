# 任务

> 进度留痕：并行会话只能通过这里知道做到哪了。勾选与说明必须一致。

## 1. 脚手架

- [x] 1.1 新建 `extension/` 目录（仓库根，与 `app/`/`frontend/` 平级），Manifest V3：
      `manifest.json`（`content_scripts` 的 `matches` 只能收窄到
      `*://www.amazon.com/s*`、`*://www.amazon.co.uk/s*`、`*://www.amazon.de/s*`
      这一级——查询参数不能直接匹配，实际判断放在 content script 入口内部按
      `location.search` 二次判断）、`background.service_worker`（不声明
      `"type": "module"`，见 1.2 的打包方式）、`options_page`。不声明
      `host_permissions`（design.md 决策 6 的 CORS 依赖说明）
- [x] 1.2 构建工具用 `esbuild`（不引入 `vite`/`@crxjs` 这类专用 MV3 插件——三个
      入口都打成独立的 IIFE 就足够，esbuild 零配置成本更低、依赖面更小）：
      `esbuild src/background.ts src/content-script.ts src/options.ts --bundle
      --outdir=dist --format=iife --target=chrome110`，配一个小脚本把
      `manifest.json`/`options.html`/图标一并拷进 `dist/`；`extension/` 自己的
      `package.json`（`esbuild`、`typescript`、`vitest`、`@types/chrome` 为
      devDependencies），不并入 `frontend/`（技术栈不同、构建目标不同）
- [x] 1.3 `extension/README.md`：如何本地构建、如何以开发者模式加载已解压的扩展程序、
      当前支持的站点（US/GB/DE）、鉴权占位方案的使用说明（去哪复制 token、存在哪、
      过期怎么办）
- **验证**：`pnpm --dir extension build` 产出 `extension/dist/`（含
  `background.js`/`content-script.js`/`options.js`/`manifest.json`/`options.html`/
  `icons/`），`python3 -c "import json; json.load(open('dist/manifest.json'))"`
  确认合法 JSON。**Chrome 实际加载未验证**（无可用图形环境跑 `chrome://extensions`），
  并入第 8 节真机验证，不重复占用一次

## 说明：图标是占位符

`icons/icon128.png` 是脚本生成的纯色 128×128 占位图（非设计稿），只为满足 Manifest
的 `icons` 字段与真机加载测试。正式使用前应替换为真实图标。

## 2. 站点识别

- [x] 2.1 `extension/src/site-map.ts`：`MARKETPLACE_ID_TO_SITE`、`DOMAIN_TO_SITE`
      两张手写映射表（design.md 决策 2），头部注释写明"手工镜像
      `app/site_registry.py` 的 `SITE_CATALOG`，两边改动必须同步"。英国
      marketplaceID 是 `A1F83G8C2ARO7P`（末尾带 P，对齐
      `app/table/competitor/seller_ref_parser.py`）——初版这里漏了这个 P，
      PR #68 二次复审 P2 修正
- [x] 2.2 `resolveSite(url: string): string | null`：优先取 `marketplaceID` 查询
      参数，查不到或未登记则回落域名；两者都失败返回 `null`（不猜测默认站点，
      design.md 决策 2/spec「站点识别失败时拒绝生成请求」）
- [x] 2.3 `extension/test/site-map.test.ts`（vitest）：
      - `marketplaceID` 命中三个已知站点各一条用例
      - 域名回落命中三个已知站点各一条用例
      - 两者都未命中返回 `null`
      - 固定断言 `Object.values(DOMAIN_TO_SITE)` 与
        `Object.values(MARKETPLACE_ID_TO_SITE)` 的去重集合等于
        `['US', 'GB', 'DE']`——这条测试的意义是"改动这份镜像时至少会被自己的测试
        看见"，不是自动同步（design.md 决策 2 已写明限制）
      - PR #68 二次复审 P2 补了一条：marketplaceID 与域名故意冲突（用真实英国
        ID 配一个非 GB 域名）时以 marketplaceID 为准——初版没有这条测试，
        `marketplaceID` 命中用例与错误的映射值都用了同一个（错的）字面量，
        错值因此一直没被测出来
- [x] 2.4 `app/site_registry.py` 模块 docstring 补一句：新增站点时同步更新
      `extension/src/site-map.ts`，指明文件路径
- **验证**：`pnpm --dir extension test site-map` 全绿——7 个用例通过

## 3. DOM 解析

- [x] 3.1 `extension/src/parse-storefront.ts`：`parseStorefrontPage(doc: Document):
      { asin: string; title?: string; image_url?: string }[]`，纯函数，输入
      `Document`（或字符串 HTML，实现时二选一，测试可控即可）
- [x] 3.2 分页支持：识别当前页是否有下一页（"下一页"按钮或分页控件），供 content
      script 决定要不要提示运营"还有更多页，建议翻页后再次采集"（design.md「采集
      当前已加载页面的 ASIN 清单，支持翻页」——插件不自动翻页，翻页动作由运营完成，
      插件只做提示与多页结果的合并去重）
- [x] 3.3 `extension/test/parse-storefront.test.ts`：用**手工保存的一份脱敏 HTML
      片段**（`extension/test/fixtures/storefront-sample.html`，从真实页面另存后
      去除卖家/账号相关的个人信息，只保留商品卡片结构）做单元测试，覆盖正常解析、
      空页面（返回空数组）、部分商品缺标题/主图三种情况
      **必须在此任务的说明里写明**：这份 fixture 只能覆盖"当前已知的页面结构"，
      Amazon 改版后测试仍可能全绿但真实页面解析失败，这条限制记在 design.md 决策 4，
      不是本任务能力范围内能解决的（不要在勾选说明里把"单测过了"写成"线上能用"）
- [x] 3.4 解析结果为空时的处理：由调用方（content script，见第 6 节）负责拒绝
      发起请求并提示，本模块只负责如实返回空数组，不在这里加"重试解析"之类的逻辑
      （职责边界：解析是纯函数，决定要不要上报是调用方的事）
- **验证**：`pnpm --dir extension test parse-storefront` 全绿——8 个用例通过，
  fixture 是**手工构造的样本，不是从真实页面另存的**（本仓库拿不到真实登录会话，
  design.md 决策 4）。**不能**据此声称"DOM 解析已验证"，真机验证见第 8 节

## 4. 请求组装与幂等键

- [x] 4.1 `extension/src/build-report.ts`：`buildReportRequest(sellerId, site,
      items, capturedAt, requestId, shipToRegion?, pageContext?):
      AsinReportRequest`——字段形状严格对齐
      `app/table/competitor/asin_schemas.py` 的 `AsinReportRequest`（开工前重新
      读一遍该文件确认字段名与类型，不要凭本 tasks.md 的复述）
- [x] 4.2 ASIN 去重：同一批 `items` 内按 `asin`（大写、去首尾空白后）去重，保留
      第一次出现的 `title`/`image_url`（服务端也会做这一步，但插件侧先去重能减少
      请求体大小，且方便运营在上报前预览"实际会提交多少条"）
- [x] 4.3 `request_id` 生成与冻结（design.md 决策 3）。**PR #68 二次复审 P1 后
      冻结点从 `background.ts` 挪到了 `content-script.ts`**：两步流程下"提交
      上报"可能因网络异常反复点击而不重新解析 DOM，若仍由无状态的 background
      在每条消息上各自生成一对新值，手动重新提交会被当成全新观测、绕开幂等
      保护。现在 `capture-accumulator.ts` 的 `freezeRequest` 只在"当前累积批次
      从未提交过"（`requestId`/`capturedAt` 为 `null`）时才生成新值，否则原样
      复用已持久化的值；这两个值随 `CaptureMessage` 一起传给 background，
      `sendReport` 内部的自动重试（见第 6 节）依旧复用同一份序列化请求体不变。
      "重新采集"（点"采集本页"，无论合并结果是否带来新条目）才会把这两个字段
      重置为 `null`，触发下次提交时重新生成——对应 spec 原文"无论内容是否变化"
- [x] 4.4 `extension/test/build-report.test.ts`：ASIN 去重用例、字段形状与
      `AsinReportRequest` 一致的用例（可以用一份手写的 JSON Schema 或者简单的
      字段清单断言比对，不需要引入跨语言的契约测试工具）；`request_id` 冻结语义的
      用例——用同一个"已冻结请求体"对象连续调用两次"重试发送"，断言两次网络请求
      携带的 `request_id` 相同；用两次独立的"采集 → 组装"调用，断言两次的
      `request_id` 不同
- **验证**：`pnpm --dir extension test build-report` 全绿——4 个用例通过。
  `ship_to_region` 本次未接入采集（design.md Open Questions 已记录原因：页面上
  肉眼可见的送货地址是完整文本，不是 schema 暗示的短码形态，需要真机核实后再接），
  字段留空不影响正确性

## 5. 鉴权占位

- [x] 5.1 `extension/src/auth.ts`：`getAuthToken()`/`setAuthToken()`（design.md
      决策 1，读写 `chrome.storage.local` 的 `keywordiq_token`）、
      `getApiBaseUrl()`/`setApiBaseUrl()`（design.md 决策 6，键名
      `keywordiq_api_base`，供 background 拼接请求 URL）
- [x] 5.2 `extension/src/options.ts` + `options.html`：两个文本框（token、API
      地址）+ 保存按钮，保存成功给出确认提示；页面上说明"从已登录的 KeywordIQ
      网页复制 token 粘贴到这里"（具体去哪个页面复制、复制哪个字段，写进
      `extension/README.md`，不在 options page 里重复整段说明）
- [x] 5.3 `extension/test/auth.test.ts`：`getAuthToken` 在未设置时返回 `null`、
      设置后能取回；`chrome.storage.local` 用 vitest 的 mock（`@types/chrome` +
      手写 stub，不引入额外的 Chrome API 测试框架）
- **验证**：`pnpm --dir extension test auth` 全绿——4 个用例通过；真机验证 options
  page 的保存/读取并入第 8 节，不单独占一次真机验证

## 6. 采集触发、失败处理与重试

**网络请求发自 background，不发自 content script**（design.md 决策 6）；content
script 只做 6.1/6.2 两步本地判断+解析，之后把结果整个交给 background 处理。

- [x] 6.1 `extension/src/content-script.ts`：判断当前页面是否是 `me=` 店铺全品
      列表页（`location.search` 解析 `i=merchant-items` 与 `me` 参数）；是则注入
      一个采集入口（"采集本页"/"提交上报"两个按钮 + 一个"清空重新开始"链接，
      UI 细节实现时定，功能优先于美观）
- [x] 6.2 **采集与提交是两步，不是一步**（PR #68 复审 P1 修正：初版实现每次点击
      立即把当前页作为独立报告发出，导致第一页单独落定基线、第二页存量商品被
      误判成"新发现"，与 spec「支持翻页，多页结果合并为一次上报」不符）。
      点"采集本页"：调 `parseStorefrontPage` → 若结果为空，页面内提示"未识别到
      任何商品，页面结构可能已变化"并终止（不改变已累积状态，spec「解析结果为
      空时拒绝上报并提示」）；否则与此前已累积的条目按 ASIN 合并去重
      （`capture-accumulator.ts` 的 `mergeItems`），存入 `chrome.storage.local`
      （按 `(site, sellerId)` 持久化，翻页导致 content script 重新注入后仍能读
      回）。点"提交上报"：累积为零时不动作；`resolveSite` 拿不到站点则在初始化
      阶段就提示"未识别的站点"并禁用采集入口（spec「站点识别失败时拒绝生成
      请求」），否则 `chrome.runtime.sendMessage` 把
      `{ sellerId, site, items: 全部累积条目, pageContext }` 交给 background，
      等待响应并展示结果/错误；`try/finally` 保证按钮不会因异常路径永久停在
      禁用态（PR #68 复审 P2）。`pageContext` 只取页码/排序等白名单参数
      （`buildPageContext`），不透传完整查询串——`me=` 链接普遍带 `ds=` 等会话
      签名参数，仓库既有契约要求这类参数不得读取、存储或用于重建请求
      （PR #68 复审 P1）。成功后清空本地累积；网络异常等可重试失败保留累积，
      允许直接重新点"提交上报"
- [x] 6.3 `extension/src/background.ts`：`chrome.runtime.onMessage` 监听器，收到
      6.2 的消息后：调 `getAuthToken()`（为空则回复"请先在设置页配置访问令牌"，
      不发请求）→ 调 `buildReportRequest` 生成并**冻结**本次请求体（决策 3）→
      调 `report-client.ts` 发送 → 把结果（成功摘要或错误提示）回复给
      content script
- [x] 6.4 `extension/src/report-client.ts`：`sendReport(baseUrl, token, body)`
      发起 `POST /api/competitor/asin-reports`（`Authorization: Bearer <token>`，
      **不带** `credentials: 'include'`——design.md 决策 6，插件不依赖 Cookie），
      失败分类处理（design.md 决策 5）：
      - 网络错误/超时：每次尝试挂一个 `AbortController` 超时（默认 15s，
        PR #68 复审 P2 修正：初版没有超时机制，请求挂起时永远不会进入重试分支），
        超时与断网/DNS 失败一样按退避重试 3 次（1s/2s/4s），复用同一份已冻结
        请求体
      - 401/403：不重试，提示"登录已过期，请重新粘贴令牌"，引导至 options page
      - 422：不重试，把服务端返回的错误信息原样展示（`RejectedItem` 列表或顶层
        错误消息）
      - 409 `competitor.idempotency_key_reused`：不重试，提示"上报异常，请刷新
        页面重试"（视为插件自身缺陷，见 design.md 决策 5），并把原始响应体写入
        一个环形缓冲区（`idempotency-log.ts`，`chrome.storage.local`，最近 20 条，
        供运营截图反馈问题用；PR #68 复审 P2 修正：初版只读了 `message` 就返回，
        没有真的写日志）
      - 2xx 成功：展示 `accepted`/`new_asins`/`baseline_established` 供运营确认
        本次采集结果（`duplicate_of` 非空时提示"这是重复上报，未产生新数据"）
- [x] 6.5 `extension/test/report-client.test.ts`：用 `fetch` mock 覆盖上述场景
      （网络错误、超时、401、422、409 含环形缓冲区容量断言、200），断言重试
      次数与是否重试的行为符合 6.4 的分类
- **验证**：`pnpm --dir extension test report-client` 全绿——11 个用例通过
  （较初版新增：请求挂起超时重试、超时重试后成功、409 写入环形缓冲区、环形
  缓冲区超容量丢弃最旧记录，共 4 条，对应 PR #68 复审 P2 的两项修复）。
  另加了一份本任务清单原先没列的 `extension/test/background.test.ts`（6 个用例，
  `sendReport` 打桩，覆盖 `handleCapture` 的分支映射、missing_token/
  missing_base_url 前置检查、以及"requestId/capturedAt 直接透传消息里的冻结值、
  不自己生成"这条 PR #68 二次复审 P1 的回归）——`background.ts` 是把 6.2/6.3/6.4
  串起来的唯一位置，不测这一层就测不到"映射对不对"这件事本身。多页合并、
  page_context 白名单、单批上限、幂等键冻结/复用这几个纯函数单独测在
  `extension/test/capture-accumulator.test.ts`（14 个用例，对应 PR #68 两轮
  复审的四项修复：分页合并、page_context 白名单、200 条上限整批拒绝、
  `freezeRequest` 的复用/重新生成语义）——`content-script.ts` 里剩下的 DOM
  绑定部分仍按 design.md 决策 4 的既有边界不做自动化测试，走第 8 节人工真机验证

## 7. 文档与收尾

- [x] 7.1 CLAUDE.md「竞对监控」一节补充本变更的说明（浏览器插件被动采集客户端
      已实现，鉴权是占位方案待定，P3b/P3c 仍未开工）——按既有密度写，只展开
      "错了不会报错"的部分（鉴权占位的替换点、站点映射的漂移风险、DOM 解析
      无法自动化验证这三处）
- [x] 7.2 检查后**未改动** `app/table/competitor/seller_model.py`：该文件的
      `baseline_at` 注释描述的是 `add-competitor-storefront-watch`（P0）那次
      变更自身的范围（"本变更只写 NULL……这条判定逻辑属于数据获取层，本变更
      不实现"），历史陈述仍然准确，且基线落定逻辑其实已经在
      `asin_service.py` 里实现（P3a 服务端），与本变更（P3a 客户端）无关——
      改这里对应的是另一次变更的收尾遗漏，不属于本变更范围
- [x] 7.3 `openspec validate add-competitor-browser-collector --strict` 通过
- [x] 7.4 **没有单独的 lint 步骤**（这是从简，不是漏配——占位实现阶段没有引入
      eslint 配置，`tsc --noEmit`（strict）已经覆盖大部分同类问题）。已接入根级
      提交门禁 `.claude/hooks/pre_commit.py`：新增 `EXTENSION_PREFIX = "extension/"`
      判据，改动范围含这个前缀时跑 `pnpm --dir extension run typecheck` +
      `pnpm --dir extension run test`，写法参照既有的 `FRONTEND_PREFIX` 分支。
      `uv run pytest test/test_pre_commit_hook_unit.py -q` 验证门禁自身逻辑未受
      影响：19 passed。`extension/` 全量：`pnpm --dir extension run typecheck`
      无报错，`pnpm --dir extension run test` 54 passed（7 个测试文件，PR #68
      两轮复审修复后新增 `capture-accumulator.test.ts` 一份（14 例）、
      `report-client.test.ts` 新增 4 例、`background.test.ts` 新增 1 例）
- [x] 7.5 GitHub Actions（PR #68 二次复审 P2 修正：初版只接入了本地提交门禁，
      `.github/workflows/ci.yml` 一直只有 backend/frontend 两个 job，`extension/`
      的 typecheck/test/build 在 CI 里完全不可见——本地 hook 不保护其他智能体或
      直接 `git push` 的场景，`AGENTS.md` 已经写明这一点）。新增 `extension` job，
      结构照抄 `frontend` job（pnpm 9 + Node 22 + frozen-lockfile install），跑
      `pnpm run typecheck` + `pnpm run test` + `pnpm run build`，`working-directory`
      指到 `extension/`

## 8. 人工真机验证（无法用自动化测试替代）

- [ ] 8.1 Chrome 开发者模式加载 `extension/dist/`
- [ ] 8.2 用已登录 KeywordIQ 网页的账号复制一个 JWT，粘贴进插件 options page
- [ ] 8.3 用真实登录的 Amazon 账号，打开一个**该 KeywordIQ 账号已关注**的卖家的
      `me=` 店铺全品列表页，点"采集本页"，确认解析出的商品清单与页面肉眼可见的
      一致、面板显示的累积数正确、"提交上报"按钮从禁用变为可点
- [ ] 8.4 点"提交上报"，确认返回成功；在数据库里核实 `asin_report` 新增**一行**
      （不是多行）、`competitor_seller_asin` 写入了预期的 ASIN 行、该卖家的
      `baseline_at` 被落定（若这是该卖家的首次上报）
- [ ] 8.5 验证失败路径至少两条：故意清空/改错 options page 里的 token，点"提交
      上报"触发 401 提示，确认已累积的数据未被清空（面板仍显示累积数，无需
      重新点"采集本页"）；故意用一个未关注的卖家页面触发采集，确认看到服务端的
      拒绝原因
- [ ] 8.6 验证多页合并（PR #68 复审 P1 修正的核心场景）：找一个**至少两页商品**
      的已关注卖家，在第一页点"采集本页"（此时不应发出任何网络请求，可用浏览器
      开发者工具的 Network 面板确认），翻到第二页再点"采集本页"，确认面板显示的
      累积数是两页商品数之和（去重后）；此时才点"提交上报"，确认只产生**一次**
      网络请求、`asin_report` 只新增一行，且第二页原本就存在的商品**不会**出现
      在这次的"新发现"里（它们与第一页的商品同属这次上报的基线批次）
- [ ] 8.7 验证失败后重新提交复用幂等键（PR #68 二次复审 P1 修正的核心场景）：
      开发者工具 Network 面板里把 `/api/competitor/asin-reports` 设为离线/超时，
      点"提交上报"直到自动重试耗尽、面板提示网络异常；恢复网络后不点"采集本页"，
      直接再点"提交上报"，对比两次请求体，确认 `request_id`/`captured_at`
      完全相同（这正是"服务端其实已经收到、只是响应丢了"这种情况下不产生第二条
      观测记录的保证）
- **本节没有可以替代它的自动化测试**——设计上就是如此（design.md 决策 4），
  验证记录（截图或文字描述实际观察到的结果）直接写在对应任务的勾选说明里，
  不能只打勾不留证据
