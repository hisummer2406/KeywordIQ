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

## 设置：地址与鉴权

**访问令牌不需要手动填**——插件从已登录的 KeywordIQ 网页会话里自己读
（`sync-extension-token-from-web` 变更）。

1. 点插件图标（或 `chrome://extensions` 里该插件的"扩展程序选项"）打开设置页
2. 填 **API 地址**（本地开发 `http://localhost:8000`，生产填实际域名）
3. 填 **网页地址**——**本地开发必须单独填**（`http://localhost:8001`）。网页在 8001、
   API 在 8000，是两个来源；留空会去 API 地址读登录态，而那里从来没写过令牌，
   表现和"没登录"一模一样。**生产环境网页与 API 同域，留空即可**
4. 填 **单次上报条数上限**——必须与部署此服务的后端环境变量
   `COMPETITOR_MAX_REPORT_ITEMS` 一致（默认 200，向管理员确认；不知道就先留
   默认值），填错的表现见下文"已知限制"。点保存
5. 在浏览器里**打开并登录 KeywordIQ 网页**，回到设置页点 **「从网页同步令牌」**
6. Chrome 会弹权限对话框，要求允许插件访问你填的域名——**必须点"允许"**。
   这是 Chrome 扩展的标准行为：访问哪个域名必须先拿到该域名的权限
   （design.md 决策 6）。换了地址需要重新走一遍授权

之后**不必再管令牌**：上报时若发现令牌缺失或已过期，插件会自动重新同步一次并重试
一次上报。同步失败时的三种提示各自对应不同的动作——"没找到网页"要你打开网页，
"尚未登录"要你去网页登录，"未授予权限"要你重新点同步并允许。

设置页底部的「手动填写令牌」是**兜底**，正常用不到；自动同步依赖标签页与浏览器权限，
真机上总有失败的可能，留这条路是为了那时插件不至于完全不可用。

### 仍未解决的三条

自动同步解决的是"要手动复制粘贴"，**不解决下面这些**——要解决必须由后端签发另一种
令牌（可吊销、可限权限、长有效期），那是另一个变更：

- **令牌仍是 24 小时期的网页 JWT。** 自动同步把"每天手动粘一次"降成"自动重来一次"，
  但前提是网页那边的登录还有效；网页也过期时，仍要人去网页重新登录。
- **令牌是全权限的。** 插件拿到的令牌能做该用户在本系统能做的一切，包括导出数据。
- **无法单独收回插件的权限。** 要收回，目前只能改该用户的密码或禁用账号。

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
