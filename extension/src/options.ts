// options.html 的脚本：读写 auth.ts 管理的本地设置（API 地址、网页地址、单次上报
// 条数上限），并提供「从网页同步令牌」这条主路径。手动填写令牌是折叠起来的兜底。

import {
  clearWebOrigin,
  getApiBaseUrl,
  getAuthToken,
  getMaxReportItems,
  getTokenSyncedAt,
  getWebOrigin,
  setApiBaseUrl,
  setAuthToken,
  setMaxReportItems,
  setWebOrigin,
} from './auth';
import { MAX_REPORT_ITEMS } from './capture-accumulator';
import { ensureHostPermission } from './host-permission';
import { syncTokenFromWeb } from './session-sync';

function describeTokenState(token: string | null, syncedAt: string | null): string {
  if (!token) return '尚未持有令牌。请先在浏览器里登录 KeywordIQ 网页，再点下面的按钮同步。';
  const when = syncedAt ? new Date(syncedAt).toLocaleString() : '时间未知';
  // 只显示"何时拿到的"，不显示令牌本身——它是一份完整的登录凭据，没有理由摊在屏幕上。
  // 时间是有用的：令牌 24 小时过期，运营看一眼就知道该不该重新同步。
  return `已持有令牌，同步于 ${when}（有效期 24 小时；上报时若已过期会自动重新同步）。`;
}

async function init(): Promise<void> {
  const apiBaseInput = document.getElementById('api-base') as HTMLInputElement;
  const webOriginInput = document.getElementById('web-origin') as HTMLInputElement;
  const tokenInput = document.getElementById('token') as HTMLInputElement;
  const maxReportItemsInput = document.getElementById('max-report-items') as HTMLInputElement;
  const saveButton = document.getElementById('save') as HTMLButtonElement;
  const saveTokenButton = document.getElementById('save-token') as HTMLButtonElement;
  const syncButton = document.getElementById('sync-token') as HTMLButtonElement;
  const tokenState = document.getElementById('token-state') as HTMLElement;
  const status = document.getElementById('status') as HTMLElement;

  const apiBase = (await getApiBaseUrl()) ?? '';
  apiBaseInput.value = apiBase;
  // 输入框留空表示"与 API 地址相同"，所以只在确实存过一个**不同**的值时才回填。
  // 直接回填 getWebOrigin() 的回退值会把"没填"显示成"填了同一个"，用户下次就看
  // 不出这一项其实是跟随 API 地址的。
  const storedWebOrigin = await getWebOrigin();
  webOriginInput.value = storedWebOrigin && storedWebOrigin !== apiBase ? storedWebOrigin : '';
  tokenInput.value = '';
  maxReportItemsInput.value = String(await getMaxReportItems());

  const refreshTokenState = async (): Promise<void> => {
    tokenState.textContent = describeTokenState(await getAuthToken(), await getTokenSyncedAt());
  };
  await refreshTokenState();

  syncButton.addEventListener('click', () => {
    // 网页来源从输入框**同步**读取，不能先 `await getWebOrigin()`：那会跨越微任务
    // 边界、丢掉 user gesture 上下文，`permissions.request()` 在真机上直接报
    // "must be called during a user gesture"、弹不出对话框（PR #68 五次复审 P1，
    // 见 host-permission.ts 顶部）。输入框留空时退回 API 地址输入框的值，与
    // `getWebOrigin()` 的回退规则一致。
    const origin = webOriginInput.value.trim() || apiBaseInput.value.trim();
    if (!origin) {
      status.textContent = '请先填写 API 地址（或网页地址）';
      return;
    }

    let permissionRequest: Promise<boolean>;
    try {
      permissionRequest = ensureHostPermission(origin);
    } catch {
      status.textContent = '网页地址不是合法的 URL';
      return;
    }

    void (async () => {
      if (!(await permissionRequest)) {
        status.textContent = '未授予访问该网页地址的权限（请在弹出的 Chrome 提示中点击允许）';
        return;
      }
      // 四种结局分开提示：三种失败要运营做的事完全不同
      const result = await syncTokenFromWeb(origin);
      switch (result.outcome) {
        case 'ok':
          status.textContent = '已同步令牌';
          break;
        case 'no_permission':
          status.textContent = '尚未获得访问该网页地址的权限，请重新点击并允许';
          break;
        case 'no_tab':
          status.textContent = `没有找到 ${origin} 的标签页，请先打开 KeywordIQ 网页`;
          break;
        case 'no_token':
          status.textContent = '找到了网页，但那里尚未登录。请先在网页上登录再同步';
          break;
      }
      await refreshTokenState();
    })();
  });

  saveButton.addEventListener('click', () => {
    void (async () => {
      const apiBaseValue = apiBaseInput.value.trim();
      const webOriginValue = webOriginInput.value.trim();
      const maxReportItems = Number.parseInt(maxReportItemsInput.value, 10);

      if (!apiBaseValue) {
        status.textContent = 'API 地址不能为空';
        return;
      }
      if (!Number.isInteger(maxReportItems) || maxReportItems <= 0) {
        status.textContent = `单次上报条数上限必须是正整数（默认 ${MAX_REPORT_ITEMS}）`;
        return;
      }

      // service worker 向这个地址发起的 fetch 需要 host permission 才能真正
      // 发出（PR #68 四次复审 P1）；在这里申请，不在保存之后才发现——权限被拒绝
      // 时不该把一个连不上的地址存进去。**这一句之前不能插入任何 await**（哪怕
      // 是看似无害的只读检查）：`ensureHostPermission` 内部直接、同步地调用
      // `chrome.permissions.request()`，依赖的正是"从 click 事件到这里都没有
      // 经过微任务边界"这件事来保住 user gesture 上下文（PR #68 五次复审 P1，
      // 见 host-permission.ts 顶部注释）。
      let granted: boolean;
      try {
        granted = await ensureHostPermission(apiBaseValue);
      } catch {
        status.textContent = 'API 地址不是合法的 URL';
        return;
      }
      if (!granted) {
        status.textContent = '未授予该地址的访问权限，无法保存（请在弹出的 Chrome 权限提示中点击允许）';
        return;
      }

      await setApiBaseUrl(apiBaseValue);
      // 留空必须**清掉**已存的旧值，不能只是"不写入"：不清的话，用户曾经填过一个
      // 独立网页地址、现在清空输入框想回到"跟随 API 地址"，storage 里那个旧值会
      // 原样留着，getWebOrigin() 继续读到它——"留空回退 API 地址"这条契约就没有
      // 兑现（PR #73 审核意见 P1）。
      if (webOriginValue) {
        await setWebOrigin(webOriginValue);
      } else {
        await clearWebOrigin();
      }
      await setMaxReportItems(maxReportItems);
      status.textContent = '已保存';
    })();
  });

  saveTokenButton.addEventListener('click', () => {
    void (async () => {
      const token = tokenInput.value.trim();
      if (!token) {
        status.textContent = '令牌不能为空';
        return;
      }
      await setAuthToken(token);
      tokenInput.value = '';
      status.textContent = '已保存令牌';
      await refreshTokenState();
    })();
  });
}

void init();
