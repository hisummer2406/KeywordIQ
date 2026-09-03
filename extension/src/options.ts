// options.html 的脚本：读写 auth.ts 管理的本地设置（token、API 地址、单次上报
// 条数上限）。

import {
  getApiBaseUrl,
  getAuthToken,
  getMaxReportItems,
  setApiBaseUrl,
  setAuthToken,
  setMaxReportItems,
} from './auth';
import { MAX_REPORT_ITEMS } from './capture-accumulator';
import { ensureHostPermission } from './host-permission';

async function init(): Promise<void> {
  const apiBaseInput = document.getElementById('api-base') as HTMLInputElement;
  const tokenInput = document.getElementById('token') as HTMLInputElement;
  const maxReportItemsInput = document.getElementById('max-report-items') as HTMLInputElement;
  const saveButton = document.getElementById('save') as HTMLButtonElement;
  const status = document.getElementById('status') as HTMLElement;

  apiBaseInput.value = (await getApiBaseUrl()) ?? '';
  tokenInput.value = (await getAuthToken()) ?? '';
  maxReportItemsInput.value = String(await getMaxReportItems());

  saveButton.addEventListener('click', () => {
    void (async () => {
      const apiBase = apiBaseInput.value.trim();
      const token = tokenInput.value.trim();
      const maxReportItems = Number.parseInt(maxReportItemsInput.value, 10);

      if (!apiBase || !token) {
        status.textContent = 'API 地址与访问令牌都不能为空';
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
        granted = await ensureHostPermission(apiBase);
      } catch {
        status.textContent = 'API 地址不是合法的 URL';
        return;
      }
      if (!granted) {
        status.textContent = '未授予该地址的访问权限，无法保存（请在弹出的 Chrome 权限提示中点击允许）';
        return;
      }

      await setApiBaseUrl(apiBase);
      await setAuthToken(token);
      await setMaxReportItems(maxReportItems);
      status.textContent = '已保存';
    })();
  });
}

void init();
