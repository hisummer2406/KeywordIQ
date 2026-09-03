## Why

issue #56（竞对店铺上新监控）的服务端上报接口已经实现并验收通过（PR #64，
`POST /api/competitor/asin-reports`，幂等四态、`baseline_at` 建立逻辑均已落地并
经真库集成测试验证）。但采集端——浏览器插件本体——完全没有写。这是分阶段表里的
P3a 客户端部分：运营手动点开收藏的 Amazon `me=` 店铺页面时，插件读页面 DOM 提取该
卖家的 ASIN 清单，上报给已经建好的后端接口。

不做这一半，已经写好的后端永远收不到真实数据：`competitor_seller_asin` 是空表，
`baseline_at` 永远是 `NULL`，卖家清单上的"近 7 日新发现"永远是占位符而非真实的 0。
本变更只影响 Amazon 卖家这条线（独立站/DTC 监控已有自己的服务端拉取式采集，不涉及
本变更）。

## What Changes

- 新增一个 Chrome 扩展（Manifest V3），实现 issue #56 P3a 里"被动采集"这一档能力：
  - content script 注入 Amazon `me=` 店铺全品列表页，解析 DOM 提取当前页可见的
    ASIN/标题/主图，支持翻页
  - 从 URL 解析 `marketplaceID`（回落到域名 TLD）得到站点码，按
    `app/site_registry.py` 的登记站点做手工镜像映射
  - 组装符合 `AsinReportRequest` 契约的请求体并上报到已存在的
    `POST /api/competitor/asin-reports`
  - **鉴权用占位实现**：options page 提供一个输入框，运营手动粘贴从网页登录后复制
    的 JWT，存入 `chrome.storage.local`。正式的取号方案（读网页会话 / 独立
    Personal Access Token / 插件内登录）是一个尚未拍板的产品决策，本变更**不实现**，
    只把它隔离成单一替换点（见 design.md 决策 1）
  - 上报失败（网络错误 / 401·403 令牌失效 / 409 幂等键复用）分别给出对应提示，
    不静默失败
- 分发方式：开发者模式加载已解压的扩展程序，供运营内部使用；不提交 Chrome Web Store

**不在本次范围内**（均是 issue #56 里明确列出但风险/前提不同的独立阶段）：

- P3b（后台自动巡检/标签页调度）——需要产品/运营层面单独确认账号级风险，issue #56
  的"待确认"清单里这条尚未拍板
- P3c（Keepa 拉取式接入）——独立路径，token 成本核算未完成
- 插件的正式鉴权方案——见上，另立决策
- 独立站/DTC 监控——已有单独的服务端拉取式采集，与本变更无关

## Capabilities

### New Capabilities

- `competitor-browser-collector`：浏览器插件作为 Amazon 卖家 ASIN 观测数据的采集端，
  覆盖 DOM 解析范围与失败处理、站点码映射的维护纪律、鉴权占位方案的边界、以及
  `request_id` 在"重试"与"重新采集"两种场景下的生成语义——这份契约是插件代码
  与已经定好的服务端契约（`competitor-asin-ingest`）之间的粘合层。

### Modified Capabilities

（无——服务端契约 `competitor-asin-ingest` 已在 PR #64 中定义，本变更是它的客户端，
不改动服务端行为）

## Impact

- 新增独立目录 `extension/`（或类似命名，具体在 design.md 中定），全新的前端技术栈
  （原生 TypeScript + Chrome Extension API，不复用 `frontend/` 的 Umi/React 技术栈，
  也不接入现有的前端构建产物发布流程）
- 后端零改动：完全消费已存在的 `POST /api/competitor/asin-reports`
- 无数据库迁移
- 无法进入 CI：DOM 选择器的正确性只能在真实登录的 Amazon 页面上人工验证，这是本变更
  验收方式里必须显式承认的一条边界（见 design.md 决策 4、tasks.md 收尾一节）
