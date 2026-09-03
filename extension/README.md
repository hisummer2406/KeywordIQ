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
3. 填入 API 地址（本地开发通常是 `http://localhost:8000`，生产环境填实际域名）、
   上一步复制的令牌，以及**单次上报条数上限**——必须与部署此服务的后端环境变量
   `COMPETITOR_MAX_REPORT_ITEMS` 一致（默认 200，向管理员确认；不知道就先留
   默认值），填错的表现见下文"已知限制"。三项都填完后点保存
4. Chrome 会弹出一个权限对话框，要求允许插件访问你刚填的这个域名——**必须点
   "允许"**，否则本次不会保存，且插件之后发出的所有请求都会失败（这是 Chrome
   扩展的标准行为：service worker 访问哪个域名必须先拿到该域名的权限，见
   design.md 决策 6）。换了 API 地址（比如从本地开发切到生产域名）需要重新
   走一遍这个授权

**令牌有效期 24 小时**，过期后插件会提示"登录已过期"，重新做一遍上面的步骤即可。

## 使用

采集与上报是**两步**，不是一步：多页商品必须先在本地合并齐全，再合并成**一次**
上报，不能逐页各发一次请求——逐页上报会让第一页单独落定该卖家的基线，第二页的
存量商品因此被服务端误判成"新发现"（PR #68 复审 P1）。

1. 打开一个**已经在 KeywordIQ 里关注**的 Amazon 卖家的 `me=` 店铺全品列表页
2. 页面右上角会出现一个悬浮面板，点"采集本页"——只在本地解析并累积当前页可见的
   商品，不发起网络请求；面板显示已累积的商品总数
3. 如果店铺有多页商品，翻到下一页后再次点"采集本页"，新页面的条目会并入已累积
   的结果（按 ASIN 去重，不覆盖或丢弃前面几页已采集的条目）
4. 所有页面都采集完后，点"提交上报"——这时才把累积的全部条目合并为一次请求发出，
   面板显示结果（新发现数量、是否重复、或失败原因）
5. 提交失败（网络异常、令牌过期）时已累积的数据不会丢失，修好问题后重新点
   "提交上报"即可，不需要重新采集；只有服务端判定为幂等冲突（插件自身缺陷）时
   才需要点"清空已采集，重新开始"

`page_context` 只携带页码、排序这类无害的导航上下文，`me=` 链接里的 `ds=` 等
会话签名参数在解析处即丢弃，不读取、不存储、不参与请求（PR #68 复审 P1）。

## 已知限制

- **DOM 选择器未经真实 Amazon 页面验证**（本仓库无法访问真实登录会话）。
  若采集面板持续提示"未识别到任何商品"，很可能是 Amazon 页面结构与
  `src/parse-storefront.ts` 里写的选择器不一致，需要对着真实页面调整，
  见 design.md 决策 4
- `ship_to_region`（配送地区码）本次未采集，见 design.md Open Questions
- 只做"被动采集"，不做后台自动巡检（P3b）；不上架 Chrome Web Store
- **单个卖家的商品数超过单次上报上限时，插件无法完整采集这家店**（PR #68 三次
  复审 P1，见 design.md 决策 7）：撞上限后插件会**硬性阻止**继续采集和提交
  （"采集本页"/"提交上报"一起锁死），已采集的部分不会被提交——不是"建议先提交
  部分"，因为提交一份不完整的批次会给该卖家落定基线，之后继续采集剩下的商品
  再提交时会被判成"新发现"（与多页合并要修的是同一类基线污染，只是触发路径
  不同）。此时只能点"清空已采集，重新开始"放弃这次采集
- **这个上限是运行时配置，不是写死的默认值**：必须在插件设置页填一份与后端
  `COMPETITOR_MAX_REPORT_ITEMS` 一致的镜像值（未配置时兜底 200）——部署方把
  服务端上限改了而插件这边没有跟着改，插件的判断会基于过期的值，表现是提交
  时收到迟来的 422，或者翻页翻到一半就被过早拒绝

## 目录结构

```
extension/
  manifest.json       # Manifest V3
  options.html         # 设置页
  src/
    site-map.ts             # 站点码手工镜像（对齐 app/site_registry.py）
    parse-storefront.ts     # DOM 解析（纯函数）
    capture-accumulator.ts  # 多页合并去重 + page_context 白名单（纯函数）
    build-report.ts          # 组装符合后端契约的请求体
    auth.ts                  # token / API 地址 / 单次上报条数上限的本地存取
    host-permission.ts       # API 地址对应 host permission 的按需申请
    idempotency-log.ts       # 409 幂等冲突的环形缓冲区日志
    report-client.ts         # 发请求 + 失败分类 + 重试 + 超时
    background.ts            # service worker：唯一发网络请求的地方
    content-script.ts        # 注入店铺列表页的入口，多页采集在此累积
    messages.ts               # content script ↔ background 的消息形状
    options.ts                # 设置页脚本
  test/                # vitest，覆盖除真机 DOM 结构外的全部逻辑
  scripts/build.mjs    # esbuild 打包脚本
```
