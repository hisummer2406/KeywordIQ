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
      `app/site_registry.py` 的 `SITE_CATALOG`，两边改动必须同步"
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
- [x] 2.4 `app/site_registry.py` 模块 docstring 补一句：新增站点时同步更新
      `extension/src/site-map.ts`，指明文件路径
- **验证**：`pnpm --dir extension test site-map` 全绿——6 个用例通过

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
- [x] 4.3 `request_id` 生成与冻结（design.md 决策 3）：`background.ts` 里，一次
      "采集 → 序列化请求体"的流程只生成一次 `crypto.randomUUID()` 与 `capturedAt`
      （`new Date().toISOString()`），此后同一份请求体的自动重试（见第 6 节）
      **必须**复用这两个值——实现上建议把"已冻结的请求体"作为一个不可变对象传给
      重试逻辑，而不是每次重试都重新调用组装函数，从代码结构上杜绝"重试时不小心
      重新生成了 request_id"这类错误
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
      一个采集入口（按钮/面板，UI 细节实现时定，功能优先于美观）
- [x] 6.2 点击采集：调 `parseStorefrontPage` → 若结果为空，页面内提示"未识别到
      任何商品，页面结构可能已变化"并终止（不发请求，spec「解析结果为空时拒绝
      上报并提示」）；否则调 `resolveSite`（拿不到站点则提示"未识别的站点"并
      终止，spec「站点识别失败时拒绝生成请求」）；通过后 `chrome.runtime.sendMessage`
      把 `{ sellerId, site, items }` 交给 background，等待响应并展示结果/错误
- [x] 6.3 `extension/src/background.ts`：`chrome.runtime.onMessage` 监听器，收到
      6.2 的消息后：调 `getAuthToken()`（为空则回复"请先在设置页配置访问令牌"，
      不发请求）→ 调 `buildReportRequest` 生成并**冻结**本次请求体（决策 3）→
      调 `report-client.ts` 发送 → 把结果（成功摘要或错误提示）回复给
      content script
- [x] 6.4 `extension/src/report-client.ts`：`sendReport(baseUrl, token, body)`
      发起 `POST /api/competitor/asin-reports`（`Authorization: Bearer <token>`，
      **不带** `credentials: 'include'`——design.md 决策 6，插件不依赖 Cookie），
      失败分类处理（design.md 决策 5）：
      - 网络错误/超时：退避重试 3 次（1s/2s/4s），复用同一份已冻结请求体
      - 401/403：不重试，提示"登录已过期，请重新粘贴令牌"，引导至 options page
      - 422：不重试，把服务端返回的错误信息原样展示（`RejectedItem` 列表或顶层
        错误消息）
      - 409 `competitor.idempotency_key_reused`：不重试，提示"上报异常，请刷新
        页面重试"（视为插件自身缺陷，见 design.md 决策 5），并把原始响应体写入
        一个环形缓冲区（`chrome.storage.local`，容量与格式实现时定，供运营截图
        反馈问题用）
      - 2xx 成功：展示 `accepted`/`new_asins`/`baseline_established` 供运营确认
        本次采集结果（`duplicate_of` 非空时提示"这是重复上报，未产生新数据"）
- [x] 6.5 `extension/test/report-client.test.ts`：用 `fetch` mock 覆盖上述五种
      响应场景（网络错误、401、422、409、200），断言重试次数与是否重试的行为
      符合 6.4 的分类
- **验证**：`pnpm --dir extension test report-client` 全绿——8 个用例通过。
  另加了一份本任务清单原先没列的 `extension/test/background.test.ts`（5 个用例，
  `sendReport` 打桩，覆盖 `handleCapture` 的分支映射与 missing_token/
  missing_base_url 前置检查）——`background.ts` 是把 6.2/6.3/6.4 串起来的唯一
  位置，不测这一层就测不到"映射对不对"这件事本身

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
      无报错，`pnpm --dir extension run test` 35 passed（6 个测试文件）

## 8. 人工真机验证（无法用自动化测试替代）

- [ ] 8.1 Chrome 开发者模式加载 `extension/dist/`
- [ ] 8.2 用已登录 KeywordIQ 网页的账号复制一个 JWT，粘贴进插件 options page
- [ ] 8.3 用真实登录的 Amazon 账号，打开一个**该 KeywordIQ 账号已关注**的卖家的
      `me=` 店铺全品列表页，触发采集，确认解析出的商品清单与页面肉眼可见的一致
- [ ] 8.4 点击上报，确认返回成功；在数据库里核实 `asin_report` 新增一行、
      `competitor_seller_asin` 写入了预期的 ASIN 行、该卖家的 `baseline_at`
      被落定（若这是该卖家的首次上报）
- [ ] 8.5 验证失败路径至少两条：故意清空/改错 options page 里的 token 触发
      401 提示；故意用一个未关注的卖家页面触发采集，确认看到服务端的拒绝原因
- [ ] 8.6 验证翻页：找一个有多页商品的已关注卖家，先在第一页采集并上报，翻到
      第二页再次采集并上报，确认第二次的"新发现数"只包含第二页新出现的 ASIN
      （第一页已上报过的不重复计入）
- **本节没有可以替代它的自动化测试**——设计上就是如此（design.md 决策 4），
  验证记录（截图或文字描述实际观察到的结果）直接写在对应任务的勾选说明里，
  不能只打勾不留证据
