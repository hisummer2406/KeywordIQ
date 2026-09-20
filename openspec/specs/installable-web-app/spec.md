# installable-web-app Specification

## Purpose
TBD - created by archiving change add-installable-web-app. Update Purpose after archive.
## Requirements
### Requirement: 提供可安装的 Web App Manifest

系统 SHALL 在前端产物根提供 Web App Manifest，并在每个入口页 `<head>` 中引用。Manifest
SHALL 声明：稳定的应用标识 `id`、应用名 `name` 与 `short_name`（均为「海眸系统」，与界面
既有产品名一致）、`start_url`、`scope`、`display: standalone`、`theme_color`、
`background_color`，以及 192×192、512×512 的 `any` 图标和 512×512 的 `maskable` 图标。
`start_url`、`scope` 与图标路径 SHALL 用相对路径，MUST NOT 硬编码站点根之外的前缀。
同时 SHALL 提供 180×180 的 Apple touch icon。

`id` 一经发布 MUST NOT 更改：浏览器据它判定「是不是同一个应用」，改了会让已安装用户
得到第二个应用而旧的不再更新。

#### Scenario: Manifest 与图标可被访问

- **WHEN** 已发布的站点收到对 Manifest 与其中每个图标的 GET 请求
- **THEN** 均返回 200，Manifest 的 `Content-Type` 为 `application/manifest+json`
- **AND** 图标的真实像素尺寸与 Manifest 声明一致

#### Scenario: 启动进入现有系统

- **WHEN** 用户从已安装的应用入口启动
- **THEN** 以独立窗口打开站点根，未登录时进入既有登录流程，已登录时进入既有默认页
- **AND** 系统内任何既有路由在独立窗口内仍在 `scope` 之内

### Requirement: 安装入口只在可操作时出现

系统 SHALL 在用户菜单中提供「安装桌面端」入口，其呈现只依据**可检测的状态**：

- 浏览器触发了 `beforeinstallprompt` 且当前不是独立窗口：显示入口，点击时才调用该事件的
  `prompt()`，安装必须由用户确认。
- 无程序化安装入口但属于 Mac Safari：显示入口，点击后给出「添加到程序坞」的简短指引。
- 当前已是独立窗口（`display-mode: standalone`，或 iOS 的 `navigator.standalone`）：
  MUST NOT 显示。
- 其余环境：MUST NOT 显示无效入口，继续作为普通网页使用。

系统 MUST NOT 声称能跨浏览器准确检测「是否已安装」。`beforeinstallprompt` 必须在应用
启动的最早期（模块加载时）就开始监听，而不是等到菜单组件挂载——事件只触发一次，晚于它
订阅的组件永远收不到，入口从此静默缺失而没有任何报错。

#### Scenario: 浏览器支持程序化安装

- **WHEN** 浏览器触发 `beforeinstallprompt` 且页面处于普通标签页
- **THEN** 用户菜单出现「安装桌面端」
- **AND** 点击后浏览器弹出自己的安装确认

#### Scenario: 安装确认用过即作废

- **WHEN** 用户点击入口、浏览器弹出确认后，用户接受或拒绝
- **THEN** 入口立即消失，直到浏览器再次触发 `beforeinstallprompt`
- **AND** 系统 MUST NOT 对同一个事件再次调用 `prompt()`（每个事件只能用一次，第二次会抛错）；
  浏览器自带的地址栏/菜单安装入口不受影响

#### Scenario: 独立窗口内不再提示

- **WHEN** 页面运行在已安装应用的独立窗口内
- **THEN** 用户菜单不出现安装入口

#### Scenario: 不支持的环境

- **WHEN** 浏览器既无 `beforeinstallprompt` 也不是 Mac Safari
- **THEN** 不出现安装入口，网页版功能不受影响

### Requirement: 不引入离线能力

系统 MUST NOT 为本能力新增 Service Worker、Cache Storage、离线页面、离线数据同步或
离线操作队列，MUST NOT 缓存业务 API 响应以支持离线使用。仓库中 MUST NOT 存在 Service
Worker 脚本或其注册代码（由自动化测试守住）。浏览器对静态资源的正常 HTTP 缓存 MAY 保留。

完全离线的冷启动 MAY 显示浏览器原生的网络错误页，MUST NOT 为此引入缓存或自定义离线页。

理由：一旦引入 Service Worker，「发布后用户拿到的是哪一版」就多了一层不受服务端控制的
缓存，而这层缓存的错误表现是用户长期停留在旧版本、且服务端没有任何信号。本产品的数据
新鲜度是核心语义（一次导入之后的数据），不允许被这一层悄悄旧化。

#### Scenario: 仓库不含 Service Worker

- **WHEN** 运行前端自动化测试
- **THEN** 断言 `public/` 与 `src/` 下没有 Service Worker 脚本，也没有
  `serviceWorker.register` 调用

### Requirement: 发布新版本不被旧入口页锁住

nginx SHALL 对 `index.html` 与 Manifest 返回 `Cache-Control: no-cache`（每次用 ETag 向
服务端确认，未变化时仍返回 304），并显式声明 `.webmanifest` 的 MIME 为
`application/manifest+json`。带哈希的构建产物文件名 SHALL 保持现状，不受影响。

#### Scenario: 发布后联网加载即得新版

- **WHEN** 发布了新的前端版本，用户随后联网重新加载（含独立窗口）
- **THEN** 入口页向服务端校验并取得新的 `index.html`，进而加载新哈希的产物
- **AND** 不因本次改造出现旧资源锁定

### Requirement: 请求失败不伪装成成功或空数据

已打开的页面遇到断网、服务端不可达或请求失败时，系统 SHALL 给出明确提示，且 MUST NOT
把失败呈现为操作成功、空数据或已同步；同时 SHALL 区分网络不通、服务端错误与登录失效
（以实际请求结果为依据，不单靠 `navigator.onLine`），恢复联网后由用户重新触发查询或
操作即为重试。系统 MUST NOT 为此引入持续心跳轮询。

#### Scenario: 断网时查询失败

- **WHEN** 用户在分析查询屏点击「立即查询」而网络不通
- **THEN** 出现明确的失败提示，表格不显示为「搜索结果：0」之类的伪造空结果
- **AND** 网络恢复后用户再次点击「立即查询」即可重试

### Requirement: 独立窗口下沿用既有登录与会话规则

安装形态 MUST NOT 引入新的登录路径、令牌或会话规则。钉钉扫码登录、回调、登录失效、
退出与账号级会话唯一（见 `dingtalk-sso`、`single-session-login`）在独立窗口内 SHALL
与普通标签页同一套行为；浏览器与已安装应用各自登录时 SHALL 遵守同一会话唯一规则，
不因安装形态额外创建或绕过会话。系统 MUST NOT 假设独立窗口与普通浏览器共享登录状态，
首次使用可能需要重新登录。

#### Scenario: 独立窗口内钉钉登录

- **WHEN** 用户在独立窗口内发起钉钉登录，跳到钉钉授权页后回到 `/sso/callback`
- **THEN** 登录完成后停留在应用窗口内并落到既有登录后页面，不循环跳转

