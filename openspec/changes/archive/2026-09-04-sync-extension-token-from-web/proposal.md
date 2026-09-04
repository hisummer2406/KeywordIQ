## Why

插件的鉴权是 `add-competitor-browser-collector` 决策 1 明确标注的**占位方案**：运营打开
已登录的 KeywordIQ 网页，从开发者工具里翻出 `localStorage` 的 `keywordiq_token`，复制、
粘贴进插件设置页。当初选它的理由成立（三个候选方案都不影响插件其余逻辑，占位方案让
DOM 解析与上报逻辑今天就能验收），但它留下了两个问题：

1. **要人工复制粘贴**，且路径是"打开开发者工具翻 localStorage"——这不是一个能写进
   操作手册交给运营的动作。
2. **JWT 24 小时过期**（`login_auth.py` 硬编码 `timedelta(hours=24)`），于是这个动作
   **每天都要重做一次**。

本变更解决第 1 个，并把第 2 个的痛感降到接近于零：**令牌改为从已登录的 KeywordIQ 网页
会话自动读取**，并在令牌缺失或过期时自动重新读取一次。

**它不解决第 2 个问题本身，这是自觉取舍。** 令牌仍是 24 小时期的网页 JWT，仍是**全权限**
的（能做该用户在本系统能做的一切，包括导出数据），插件的权限也仍然无法在不改该用户密码
的前提下单独收回。要同时解决这三条，需要后端新增一种可吊销、可限权限、长有效期的令牌
（Personal Access Token 或插件授权流），那是另一个变更、且必须改后端。本变更的边界是
**后端零改动**。

## What Changes

- **新增「网页地址」设置**（`keywordiq_web_origin`）。**不能复用 API 地址**：本地开发下
  网页在 `:8001`、API 在 `:8000`，是两个来源；生产环境经 nginx 才是同一个域。未填时
  回退到 API 地址（对生产环境即正确）。
- **新增 `session-sync.ts`**：向配置的网页来源注入一段读取脚本（`chrome.scripting`），
  取回 `localStorage.keywordiq_token` 并存入 `chrome.storage.local`。三种结局各自可辨：
  没有该来源的标签页 / 有标签页但没有令牌（未登录）/ 成功。
- **设置页改版**：令牌输入框改为只读的状态显示（是否已持有、何时同步的），新增
  「从网页同步令牌」按钮。**手动粘贴入口保留**为折叠的兜底——同步依赖 `chrome.scripting`
  与标签页，真机上总有失败的可能，把唯一入口拆掉会让插件在那种时候完全不可用。
- **`background.ts` 在两处自动同步**：取不到令牌时先试一次；上报收到 401 时试一次并
  **重试一次上报**。重试安全的前提是 `request_id` 已被 content script 冻结（决策 3），
  重发的是逐字节相同的请求体，服务端按幂等键回放，不会产生第二条观测。
- `manifest.json` 增加 `scripting` 权限；网页来源的 host permission 仍走既有的
  `optional_host_permissions` 按需申请。

## Capabilities

### Modified Capabilities

- `competitor-browser-collector`：「鉴权令牌读取失败时明确提示」这条 requirement 的两个
  场景措辞随之改变——提示不再是"前往设置页粘贴令牌"，而是"请在浏览器里登录 KeywordIQ
  网页后重试"；并新增一条要求：401 后自动重新同步并重试**至多一次**，仍失败才提示。
  见 `specs/competitor-browser-collector/spec.md`。

## Impact

**受影响文件**（全部在 `extension/` 下，后端与前端零改动）：
- 新增 `src/session-sync.ts` 与 `test/session-sync.test.ts`
- `src/auth.ts`：新增网页来源与同步时刻的读写
- `src/background.ts`：两处自动同步 + 401 重试一次
- `src/options.ts` / `options.html`：设置页改版
- `manifest.json`：`scripting` 权限
- `README.md`：鉴权一节重写

**不受影响**：DOM 解析、请求组装、幂等键冻结、累积与上限判定、错误分类——它们与"令牌
从哪来"正交，这正是决策 1 当初把耦合点收在 `getAuthToken()` 一个函数里的目的。
