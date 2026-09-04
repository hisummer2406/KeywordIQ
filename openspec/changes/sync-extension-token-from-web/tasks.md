## 1. 配置与存储（`auth.ts`）

- [x] 1.1 新增 `getWebOrigin()` / `setWebOrigin()`（键 `keywordiq_web_origin`）；
      取不到时回退 `getApiBaseUrl()`（design 决策 1）
- [x] 1.2 `setAuthToken` 同时记录同步时刻（`keywordiq_token_synced_at`），
      供设置页显示"何时同步的"

## 2. 同步模块（`session-sync.ts`）

- [x] 2.1 `syncTokenFromWeb(webOrigin)` → `'ok' | 'no_permission' | 'no_tab' | 'no_token'`
      四态，**不合并**（design 决策 3）
- [x] 2.2 用 `chrome.tabs.query` 找目标来源的标签页；用 `chrome.scripting.executeScript`
      读 `localStorage.getItem('keywordiq_token')`
- [x] 2.3 单元测试 `test/session-sync.test.ts`：四种结局各一条，
      以及"读到空串视同 no_token"

## 3. 自动同步（`background.ts`）

- [x] 3.1 取不到令牌时先同步一次，仍无则按具体原因返回可辨的结局
- [x] 3.2 401 时同步一次；**仅当拿到不同的令牌**才用**同一份请求体**重试**一次**
      （design 决策 4）
- [x] 3.3 `messages.ts` 的 `CaptureResult` 增补对应结局

## 4. 设置页（`options.ts` / `options.html`）

- [x] 4.1 新增网页地址输入；令牌区改为状态显示 + 「从网页同步令牌」按钮
- [x] 4.2 手动粘贴保留为**折叠**兜底（design 决策 5）
- [x] 4.3 权限申请仍必须在 click 处理器里**同步**发起——`ensureHostPermission`
      之前不得有任何 `await`（PR #68 五次复审 P1）

## 5. 权限与文档

- [x] 5.1 `manifest.json` 增加 `scripting` 权限
- [x] 5.2 `extension/README.md` 鉴权一节重写；写明仍未解决的三条
      （24 小时过期 / 全权限 / 无法单独吊销）

## 6. 验证

- [ ] 6.1 提交门禁：`extension` typecheck + vitest 全绿
- [ ] 6.2 CI
- [ ] 6.3 人工真机验收（并入 issue #72）：网页已登录时点同步能取到令牌；
      未登录时提示"请先登录"而不是"没有标签页"；令牌过期后上报能自愈
