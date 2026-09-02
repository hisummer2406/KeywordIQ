// options.html 的脚本：读写 auth.ts 管理的两项本地设置（token、API 地址）。

import { getApiBaseUrl, getAuthToken, setApiBaseUrl, setAuthToken } from './auth';

async function init(): Promise<void> {
  const apiBaseInput = document.getElementById('api-base') as HTMLInputElement;
  const tokenInput = document.getElementById('token') as HTMLInputElement;
  const saveButton = document.getElementById('save') as HTMLButtonElement;
  const status = document.getElementById('status') as HTMLElement;

  apiBaseInput.value = (await getApiBaseUrl()) ?? '';
  tokenInput.value = (await getAuthToken()) ?? '';

  saveButton.addEventListener('click', () => {
    void (async () => {
      const apiBase = apiBaseInput.value.trim();
      const token = tokenInput.value.trim();

      if (!apiBase || !token) {
        status.textContent = 'API 地址与访问令牌都不能为空';
        return;
      }

      await setApiBaseUrl(apiBase);
      await setAuthToken(token);
      status.textContent = '已保存';
    })();
  });
}

void init();
