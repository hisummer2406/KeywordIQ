// 注入到 Amazon 店铺全品列表页的入口。只做本地判断（是不是 me= 页面）与 DOM
// 解析，网络请求全部交给 background（design.md 决策 6）。

import { hasNextPage, parseStorefrontPage } from './parse-storefront';
import { resolveSite } from './site-map';
import type { CaptureMessage, CaptureResult } from './messages';

function getSellerId(url: URL): string | null {
  if (url.searchParams.get('i') !== 'merchant-items') return null;
  return url.searchParams.get('me');
}

function createPanel(): { root: HTMLElement; button: HTMLButtonElement; status: HTMLElement } {
  const root = document.createElement('div');
  root.id = 'keywordiq-collector-panel';
  root.style.cssText =
    'position:fixed;top:12px;right:12px;z-index:2147483647;background:#fff;' +
    'border:1px solid #ccc;border-radius:6px;padding:10px 12px;font-size:13px;' +
    'font-family:system-ui,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.15);max-width:280px;';

  const button = document.createElement('button');
  button.textContent = '采集并上报给 KeywordIQ';
  button.style.cssText = 'padding:6px 10px;cursor:pointer;';

  const status = document.createElement('div');
  status.style.cssText = 'margin-top:6px;color:#333;white-space:pre-wrap;';

  root.appendChild(button);
  root.appendChild(status);
  document.body.appendChild(root);

  return { root, button, status };
}

function describeResult(result: CaptureResult): string {
  switch (result.outcome) {
    case 'missing_token':
      return '请先在插件设置页配置访问令牌';
    case 'missing_base_url':
      return '请先在插件设置页配置 API 地址';
    case 'auth_error':
      return '登录已过期，请重新粘贴访问令牌（在插件设置页）';
    case 'idempotency_conflict':
      return `上报异常，请刷新页面重试：${result.message}`;
    case 'rejected':
      return `上报被拒绝：${result.message}`;
    case 'network_error':
      return '网络异常，请稍后重试';
    case 'success': {
      const parts = [`已提交 ${result.accepted} 条`];
      if (result.duplicate) {
        parts.push('（这是重复上报，未产生新数据）');
      } else {
        parts.push(`，新发现 ${result.newAsins} 条`);
        if (result.baselineEstablished) parts.push('（本次为该卖家建立了基线）');
      }
      return parts.join('');
    }
  }
}

function init(): void {
  const url = new URL(window.location.href);
  const sellerId = getSellerId(url);
  if (!sellerId) return; // 不是 me= 店铺全品列表页，不激活

  const { button, status } = createPanel();

  button.addEventListener('click', () => {
    void handleCapture(url, sellerId, status, button);
  });
}

async function handleCapture(
  url: URL,
  sellerId: string,
  status: HTMLElement,
  button: HTMLButtonElement,
): Promise<void> {
  button.disabled = true;
  status.textContent = '采集中…';

  const items = parseStorefrontPage(document);
  if (items.length === 0) {
    status.textContent = '未识别到任何商品，页面结构可能已变化';
    button.disabled = false;
    return;
  }

  const site = resolveSite(url.href);
  if (!site) {
    status.textContent = '未识别的站点，暂不支持此站点';
    button.disabled = false;
    return;
  }

  if (hasNextPage(document)) {
    status.textContent = '采集中（检测到还有下一页，建议翻页后再次采集）…';
  }

  const message: CaptureMessage = {
    type: 'CAPTURE_REPORT',
    sellerId,
    site,
    items,
    pageContext: url.search.replace(/^\?/, ''),
  };

  const result = (await chrome.runtime.sendMessage(message)) as CaptureResult;
  status.textContent = describeResult(result);
  button.disabled = false;
}

init();
