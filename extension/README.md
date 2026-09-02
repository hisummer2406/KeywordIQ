# KeywordIQ 竞对采集插件（被动采集）

issue #56 P3a 的客户端半边。运营手动打开一个已关注卖家的 Amazon 店铺全品列表页
（`me=` 页面）时，插件读取当前页可见的商品并上报给 KeywordIQ 后端
（`POST /api/competitor/asin-reports`，服务端契约见
`app/table/competitor/asin_schemas.py`）。

设计决策见 `openspec/changes/add-competitor-browser-collector/design.md`；行为
契约见同目录 `specs/competitor-browser-collector/spec.md`。

## 当前支持的站点

US（amazon.com）、GB（amazon.co.uk，不是 UK）、DE（amazon.de）——与
`app/site_registry.py` 登记的站点一致。新增站点时两边都要改，见
`src/site-map.ts` 头部注释。

## 本地构建

```bash
pnpm install
pnpm run build       # 产出 extension/dist/
pnpm run typecheck   # tsc --noEmit
pnpm run test        # vitest
```

## 加载到 Chrome（开发者模式）

1. `pnpm run build`
2. Chrome 打开 `chrome://extensions`，右上角开启"开发者模式"
3. 点击"加载已解压的扩展程序"，选择 `extension/dist/` 目录

## 设置：鉴权令牌与 API 地址（临时方案）

**这是刻意的临时方案**，正式方案（读网页会话 / 独立 Personal Access Token /
插件内登录）还没有拍板，见 design.md 决策 1。当前用法：

1. 打开已登录的 KeywordIQ 网页，浏览器开发者工具 → Application（或 Storage）
   → Local Storage → 找到当前站点的 `keywordiq_token` 这一项，复制它的值
   （这与插件内部存储用的键名恰好同名，但两者是完全独立的存储——网页的
   `localStorage` 与插件的 `chrome.storage.local` 互不相通，只是巧合地取了
   同一个名字）
2. 点插件图标右键 → "选项"（或 `chrome://extensions` 里该插件的"扩展程序选项"）
3. 填入 API 地址（本地开发通常是 `http://localhost:8000`，生产环境填实际域名）
   与上一步复制的令牌，点保存

**令牌有效期 24 小时**，过期后插件会提示"登录已过期"，重新做一遍上面的步骤即可。

## 使用

1. 打开一个**已经在 KeywordIQ 里关注**的 Amazon 卖家的 `me=` 店铺全品列表页
2. 页面右上角会出现一个悬浮面板，点"采集并上报给 KeywordIQ"
3. 面板会显示结果（新发现数量、是否重复、或失败原因）
4. 如果店铺有多页商品，翻到下一页后可以再次点击采集——两次结果会分别上报，
   不会互相覆盖

## 已知限制

- **DOM 选择器未经真实 Amazon 页面验证**（本仓库无法访问真实登录会话）。
  若采集面板持续提示"未识别到任何商品"，很可能是 Amazon 页面结构与
  `src/parse-storefront.ts` 里写的选择器不一致，需要对着真实页面调整，
  见 design.md 决策 4
- `ship_to_region`（配送地区码）本次未采集，见 design.md Open Questions
- 只做"被动采集"，不做后台自动巡检（P3b）；不上架 Chrome Web Store

## 目录结构

```
extension/
  manifest.json       # Manifest V3
  options.html         # 设置页
  src/
    site-map.ts        # 站点码手工镜像（对齐 app/site_registry.py）
    parse-storefront.ts# DOM 解析（纯函数）
    build-report.ts     # 组装符合后端契约的请求体
    auth.ts             # token / API 地址的本地存取
    report-client.ts    # 发请求 + 失败分类 + 重试
    background.ts       # service worker：唯一发网络请求的地方
    content-script.ts   # 注入店铺列表页的入口
    messages.ts          # content script ↔ background 的消息形状
    options.ts           # 设置页脚本
  test/                # vitest，覆盖除真机 DOM 结构外的全部逻辑
  scripts/build.mjs    # esbuild 打包脚本
```
