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

      await setApiBaseUrl(apiBase);
      await setAuthToken(token);
      await setMaxReportItems(maxReportItems);
      status.textContent = '已保存';
    })();
  });
}

void init();
