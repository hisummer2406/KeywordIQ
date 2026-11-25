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

## 分发

给运营的分发方式是 zip + 加载已解压：`pnpm run pack` 产出 `release/keywordiq-competitor-collector-v<版本>.zip`，
安装与使用说明见 [`docs/extension-install.md`](../docs/extension-install.md)。`manifest.json` 的 `key`
字段固定了扩展 ID，运营挪动解压目录后设置与 host 权限不会丢。

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
4. 点保存。**单次上报条数上限不用填**：v0.2.0 起插件打开采集面板时从
   `GET /api/competitor/collector-config` 读取服务端的实际值（#166）
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
- 自动采集只是默认关闭的试点（见上文「自动采集」），不是后台巡检，也不代表采集了全店商品；不上架 Chrome Web Store（改用 zip 分发，见上文「分发」）
- **单个卖家的商品数超过单次上报上限时，插件无法完整采集这家店**（PR #68 三次
  复审 P1，见 design.md 决策 7）：撞上限后插件会**硬性阻止**继续采集和提交
  （"采集本页"/"提交上报"一起锁死），已采集的部分不会被提交——不是"建议先提交
  部分"，因为提交一份不完整的批次会给该卖家落定基线，之后继续采集剩下的商品
  再提交时会被判成"新发现"（与多页合并要修的是同一类基线污染，只是触发路径
  不同）。此时只能点"清空已采集，重新开始"放弃这次采集。本轮默认支持单卖家 1000 条
- **上限只来自服务端**（#166 D4）：面板打开时读取 `collector-config`，读不到就禁止
  开始采集，**不回落到本地默认值**——按一个错的上限翻完很多页、提交时才被整批拒绝，
  比现在就停下更糟。此前的手填镜像值与本地常量已删除
- 卖家店名尽力从页面解析（`parse-storefront.ts` 的 `parseSellerName`，选择器未经真实
  页面验证），取不到就不带，不影响 ASIN 提交

## 自动采集（试点，#205 阶段 1）

v0.3.0 起插件可以每天自动采集关注清单里的 Amazon 店铺，**默认关闭**。行为契约见
`openspec/changes/pilot-extension-auto-collect/`（归档后在 `openspec/specs/competitor-browser-collector/`）。

**升级到 v0.3.0 会新增 `alarms` 权限**：以「加载已解压」方式安装的，重新加载后即生效；
若 Chrome 提示新权限或停用了插件，需要在 `chrome://extensions` 里重新启用。

### 工作方式

到了设置的每日时刻（或在设置页点「立即运行一次」），插件：

1. 读取当前令牌对应账号的 Amazon 关注清单（最多「每轮最多店铺数」家）；
2. 逐店在**后台标签页**打开按最新排序的店铺页（`s=date-desc-rank`），只采前 N 页（默认 2）；
3. 每家店多页合并后**上报一次**，关闭标签页，随机等待 30–60 秒再处理下一家；
4. 遇到验证码、访问拒绝或页面超时（90 秒），**立即停止整轮**；
5. 每页都核对页头「Deliver to」显示的配送位置是否是设置页的目标邮编（美国站默认 `10001`）：
   确认不符（如 `China`、`Berlin 10115, Germany`）记「配送位置不符」，读不到或只剩「Deliver to」提示记
   「配送位置未确认」，两种都在当前店**停止整轮**，当前店不上报，之前已提交的店保留；
6. 分页结果分三类：自然到达末页记「覆盖完整」；到了 N 页页面仍有下一页记「覆盖有限」（照常合并上报）；
   第 1 页为空记 `empty_page`，前一页提示有下一页而后页没有有效 ASIN 记 `missing_page`，这两种这家店都不上报。

运行状态存在插件本地，Service Worker 被回收或 Chrome 重启后会接着做；上报中断的店用同一个
`request_id` 重发。设置页显示最近一轮的汇总，并可导出全部运行记录（JSON，最多 30 轮）。

### 页面选择器由服务端下发（v0.3.3 起）

选择器在真实页面上对不上时，不用重装插件：在后端 `.env` 里把 `COMPETITOR_COLLECTOR_SELECTORS_FILE`
指向一个 JSON 文件，写好版本号和要覆盖的选择器，保存后**下一轮**自动采集就会用上（后端不用重启）。

```json
{
  "version": "2026-11-26a",
  "selectors": {
    "result_card": ["[data-component-type=\"s-search-result\"][data-asin]"],
    "next_page": ["a.s-pagination-next:not(.s-pagination-disabled)"],
    "seller_name": ["#p_6 li .a-text-bold"],
    "delivery": ["#glow-ingress-line2"],
    "blocked_selector": ["form[action*=\"validateCaptcha\"]"],
    "blocked_text": ["access restricted"]
  }
}
```

- 每个键都可以省略；下发值排在内置默认**之前**，写错了内置仍会兜底。`blocked_text` 只按纯文本匹配，不是正则。
- 文件不合法（未知键、空数组、超过 10 项或单项超过 200 字符）时整份不下发，后端日志有告警，插件用内置选择器。
- 每轮开跑时快照一次，跑到一半改文件不影响本轮；设置页「页面选择器」一行显示本轮用的版本（无覆盖显示「内置」）。
- 只影响自动采集，人工采集面板仍用内置选择器。

### 试点约束（必须遵守）

- **API 地址只能指向本地或隔离的后端，不要指向生产。** 首次自动采集静默入库要到阶段 2 才实现，
  直接写生产会让已有人工基线的卖家冒出一批假新发现。
- **宿主机的网络出口须与卖家后台分开。** Amazon 已明确表示自动访问违反其使用条款（#205 阶段 0 记录）。
- **令牌仍是全权限的网页 JWT**，有效期 24 小时，跑不过一天。试点时为本地后端的试点账号签发一个
  长有效期令牌，通过设置页「手动填写令牌」填入；这只是本地试跑的权宜做法，不是已收窄的设备凭证。
- **用专用 Chrome Profile 手动把 Amazon 配送位置设成 `US / 10001`**（页头「Deliver to」→ 输入邮编 → Apply）。
  插件只核对，不会自动切换配送地址；核对不过会停止整轮，改好后点「立即运行一次」重跑即可。
- 电脑和 Chrome 要保持开着（窗口可以最小化）；关机或退出 Chrome 期间不采集，重新打开后接着做。

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
    auto-config.ts            # 自动采集（试点）设置：默认关闭
    auto-plan.ts              # 自动采集纯逻辑：店铺页 URL、拦截识别、每页之后的决策
    auto-runner.ts            # 自动采集后台状态机（alarms + tabs 事件，可恢复）
    auto-options.ts           # 设置页的自动采集区块
    selector-overrides.ts     # 服务端下发的选择器覆盖：形状校验、与内置默认合并
    watch-client.ts           # 读取关注清单
  test/                # vitest，覆盖除真机 DOM 结构外的全部逻辑
  scripts/build.mjs    # esbuild 打包脚本
```
