// 注入到 Amazon 店铺全品列表页的入口。只做本地判断（是不是 me= 页面）与 DOM
// 解析，网络请求全部交给 background（design.md 决策 6）。
//
// 分页天然跨"页面"——Amazon 翻页会整页刷新，content script 重新注入、内存状态
// 清零——因此"已采集但未提交"的条目按 (site, sellerId) 持久化在
// chrome.storage.local（capture-accumulator.ts），多页结果合并为**一次**上报后
// 才发起网络请求：逐页各发一次会让第一页单独落定基线，第二页的存量商品因此被
// 服务端判成"新发现"（spec「采集当前已加载页面的 ASIN 清单，支持翻页」，
// PR #68 复审 P1）。

import { hasNextPage, parseSellerName, parseStorefrontPage, type ParsedItem } from './parse-storefront';
import { resolveSite } from './site-map';
import { buildPageContext, freezeRequest, mergeItemsWithCap, pendingItemsKey } from './capture-accumulator';
import type {
  AutoControlMessage,
  AutoControlResult,
  AutoScanRequest,
  CaptureMessage,
  CaptureResult,
  ConfigMessage,
  ConfigResult,
} from './messages';
import { detectBlockedPage, type PageScan } from './auto-plan';

// 自动采集（试点，#205 阶段 1）：后台在页面加载完成后发来 AUTO_SCAN，这里解析并回答。
// 只读 DOM，不发网络请求、不碰人工面板的暂存区（本变更 design 决策 7）。
// 监听器在模块顶层注册，越早越好：后台在 `complete` 后就会来问。
chrome.runtime.onMessage.addListener((message: AutoScanRequest, _sender, sendResponse) => {
  if (message?.type !== 'AUTO_SCAN') return undefined;
  const scan: PageScan = detectBlockedPage(document)
    ? { kind: 'blocked' }
    : {
        kind: 'ok',
        items: parseStorefrontPage(document),
        hasNext: hasNextPage(document),
        sellerName: parseSellerName(document),
      };
  sendResponse(scan);
  return undefined;
});

async function isAutoCollectTab(): Promise<boolean> {
  try {
    const message: AutoControlMessage = { type: 'AUTO_IS_AUTO_TAB' };
    const result = (await chrome.runtime.sendMessage(message)) as AutoControlResult | undefined;
    return result?.ok === true && result.isAutoTab === true;
  } catch {
    return false;
  }
}

function getSellerId(url: URL): string | null {
  if (url.searchParams.get('i') !== 'merchant-items') return null;
  return url.searchParams.get('me');
}

/**
 * 已累积但未提交的一批条目，连同该批的冻结幂等键一起持久化——`requestId`/
 * `capturedAt` 为 `null` 表示"这批条目还没有被提交过一次"，非 `null` 表示
 * "上一次提交用的是这对值，提交失败后不经过重新采集、直接再点提交要复用它们"
 * （capture-accumulator.ts 的 freezeRequest，PR #68 二次复审 P1）。
 *
 * `capped: true` 表示这批条目在某次"采集本页"时撞上了单次上报上限——不是"这一
 * 页没并入"就完了，而是**永久**拒绝提交这批数据，直到运营点"清空已采集，重新
 * 开始"。原因：一旦提交这批不完整的条目，就会用它们给该卖家落定基线；运营接下来
 * 大概率会继续采集剩下的商品并再次提交，而那一批会被服务端判成"新发现"（同一个
 * bug 的另一种触发方式，PR #68 复审 P1 已经修过一次）。允许提交部分数据、只是
 * 不建议，等于把"会污染数据"的操作留在用户可以选择的路径上——PR #68 三次复审
 * P1 明确要求改成硬性阻止。
 */
interface PendingCapture {
  items: ParsedItem[];
  requestId: string | null;
  capturedAt: string | null;
  capped: boolean;
}

const EMPTY_PENDING: PendingCapture = { items: [], requestId: null, capturedAt: null, capped: false };

async function loadPendingCapture(site: string, sellerId: string): Promise<PendingCapture> {
  const key = pendingItemsKey(site, sellerId);
  const stored = await chrome.storage.local.get(key);
  const value = stored[key] as Partial<PendingCapture> | undefined;
  if (!value || !Array.isArray(value.items)) return { ...EMPTY_PENDING };
  return {
    items: value.items,
    requestId: typeof value.requestId === 'string' ? value.requestId : null,
    capturedAt: typeof value.capturedAt === 'string' ? value.capturedAt : null,
    capped: value.capped === true,
  };
}

async function savePendingCapture(site: string, sellerId: string, pending: PendingCapture): Promise<void> {
  await chrome.storage.local.set({ [pendingItemsKey(site, sellerId)]: pending });
}

async function clearPendingCapture(site: string, sellerId: string): Promise<void> {
  await chrome.storage.local.remove(pendingItemsKey(site, sellerId));
}

interface Panel {
  collectButton: HTMLButtonElement;
  submitButton: HTMLButtonElement;
  resetLink: HTMLAnchorElement;
  status: HTMLElement;
}

function createPanel(): Panel {
  const root = document.createElement('div');
  root.id = 'keywordiq-collector-panel';
  root.style.cssText =
    'position:fixed;top:12px;right:12px;z-index:2147483647;background:#fff;' +
    'border:1px solid #ccc;border-radius:6px;padding:10px 12px;font-size:13px;' +
    'font-family:system-ui,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.15);max-width:280px;';

  const collectButton = document.createElement('button');
  collectButton.textContent = '采集本页';
  collectButton.style.cssText = 'padding:6px 10px;cursor:pointer;margin-right:6px;';

  const submitButton = document.createElement('button');
  submitButton.textContent = '提交上报';
  submitButton.style.cssText = 'padding:6px 10px;cursor:pointer;';
  submitButton.disabled = true;

  const buttonRow = document.createElement('div');
  buttonRow.appendChild(collectButton);
  buttonRow.appendChild(submitButton);

  const resetLink = document.createElement('a');
  resetLink.textContent = '清空已采集，重新开始';
  resetLink.href = '#';
  resetLink.style.cssText = 'display:block;margin-top:6px;font-size:12px;color:#888;';

  const status = document.createElement('div');
  status.style.cssText = 'margin-top:6px;color:#333;white-space:pre-wrap;';

  root.appendChild(buttonRow);
  root.appendChild(resetLink);
  root.appendChild(status);
  document.body.appendChild(root);

  return { collectButton, submitButton, resetLink, status };
}

function describeResult(result: CaptureResult): string {
  switch (result.outcome) {
    case 'missing_token':
      return '请先在插件设置页配置访问令牌';
    case 'missing_base_url':
      return '请先在插件设置页配置 API 地址';
    // 同步失败的三种成因分开说：运营要做的事完全不同，合并成一句等于没说
    case 'sync_no_permission':
      return '插件尚未获得访问 KeywordIQ 网页的权限，请到插件设置页点"从网页同步令牌"并在弹窗中允许';
    case 'sync_no_tab':
      return '没有找到已打开的 KeywordIQ 网页，请先打开它（并确认插件设置页里的「网页地址」填对了）';
    case 'sync_not_logged_in':
      return '找到了 KeywordIQ 网页，但那里尚未登录。请先在那个页面登录，再回来点"提交上报"——已采集的数据仍会保留';
    case 'auth_error':
      return '登录已过期，且 KeywordIQ 网页的登录也已失效。请在网页上重新登录，已采集的数据仍会保留，登录后再点"提交上报"即可';
    case 'idempotency_conflict':
      return `上报异常：${result.message}。请点"清空已采集，重新开始"后重新采集`;
    case 'rejected':
      return `上报被拒绝：${result.message}`;
    case 'network_error':
      return '网络异常，已采集的数据仍会保留，可稍后重新点"提交上报"';
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

function describeAccumulated(count: number, hasMore: boolean): string {
  const base = count === 0 ? '尚未采集' : `已采集 ${count} 个商品，尚未提交`;
  return hasMore ? `${base}（检测到还有下一页，建议翻页后继续点"采集本页"）` : base;
}

/**
 * 撞上单次上报上限后的硬性阻止提示（PR #68 三次复审 P1）：不建议、而是明确
 * 阻止提交这批不完整的数据——提交会给该卖家落定一个不完整的基线，运营后续
 * 采集剩余商品并再次提交时，那批会被服务端判成"新发现"。插件目前没有能安全
 * 分批建立基线的协议，只能在这里止步，让运营决定放弃采集这个卖家还是清空重来。
 */
function describeCapExceeded(cap: number | null, currentCount: number): string {
  const limit = cap === null ? '服务端上限' : `${cap} 条`;
  return (
    `本轮支持单个卖家最多 ${limit}，该卖家的商品数超过了这个范围，插件不支持完整采集。` +
    `已采集的 ${currentCount} 条不会被提交——不能分批提交：第一批会单独给该卖家落定基线，` +
    `之后采到的剩余商品会被误判成"新发现"。请点"清空已采集，重新开始"放弃这次采集。`
  );
}

/** 读不到服务端上限时的提示（#166 D4）：禁止开始采集，不按任何本地默认值继续累积。 */
const CONFIG_UNAVAILABLE =
  '无法获取服务端的单次上报上限，暂不能开始采集。请确认插件设置里的 API 地址正确、并已在 KeywordIQ 网页上登录，然后刷新本页';

async function loadMaxReportItems(): Promise<number | null> {
  try {
    const message: ConfigMessage = { type: 'GET_COLLECTOR_CONFIG' };
    const result = (await chrome.runtime.sendMessage(message)) as ConfigResult | undefined;
    return result?.outcome === 'ok' ? result.maxReportItems : null;
  } catch {
    return null; // background 未响应：按取不到处理，不回落到默认值
  }
}

async function init(): Promise<void> {
  const url = new URL(window.location.href);
  const sellerId = getSellerId(url);
  if (!sellerId) return; // 不是 me= 店铺全品列表页，不激活
  // 自动采集打开的标签页不渲染人工面板，避免两套流程混在同一页（本变更 design 决策 7）
  if (await isAutoCollectTab()) return;

  const panel = createPanel();
  const site = resolveSite(url.href);

  if (!site) {
    panel.status.textContent = '未识别的站点，暂不支持此站点';
    panel.collectButton.disabled = true;
    return;
  }

  // 以下三个处理函数写成 const 箭头函数（而非函数声明）并放在 `site`/`sellerId`
  // 的 null 检查之后：TypeScript 只在这种写法下才会把外层已收窄的 `string | null`
  // → `string` 带进闭包，写成 hoisted 的 function 声明拿不到这份收窄。

  // 上限**只来自服务端**（GET /api/competitor/collector-config，经 background 读取，#166 D4）。
  // 读不到就禁止开始采集：按一个错的上限翻完很多页，提交时才被整批拒绝，比现在就停下更糟。
  // 已累积、未提交的条目仍可提交——服务端会自己校验上限。
  const maxReportItems = await loadMaxReportItems();
  let pending = await loadPendingCapture(site, sellerId);
  const collectBlocked = (): boolean => pending.capped || maxReportItems === null;
  panel.collectButton.disabled = collectBlocked();
  panel.submitButton.disabled = pending.items.length === 0 || pending.capped;
  panel.status.textContent = pending.capped
    ? describeCapExceeded(maxReportItems, pending.items.length)
    : maxReportItems === null
      ? CONFIG_UNAVAILABLE
      : describeAccumulated(pending.items.length, hasNextPage(document));

  const handleCollect = async (): Promise<void> => {
    if (pending.capped) return; // 已被硬性阻止，只能走"清空已采集，重新开始"
    if (maxReportItems === null) {
      panel.status.textContent = CONFIG_UNAVAILABLE;
      return;
    }

    const items = parseStorefrontPage(document);
    if (items.length === 0) {
      panel.status.textContent = '未识别到任何商品，页面结构可能已变化';
      return;
    }

    const merged = mergeItemsWithCap(pending.items, items, maxReportItems);
    if (merged.rejectedByCap) {
      pending = { items: pending.items, requestId: null, capturedAt: null, capped: true };
      await savePendingCapture(site, sellerId, pending);
      panel.collectButton.disabled = true;
      panel.submitButton.disabled = true;
      panel.status.textContent = describeCapExceeded(maxReportItems, pending.items.length);
      return;
    }

    // 每次真正解析到 DOM 结果都视为"重新触发一次采集"，无论合并后条目集合是否
    // 带来新内容都要作废已冻结的幂等键（spec「`request_id`……重新采集换新」：
    // 无论内容是否变化）。只有"提交上报"失败后不经过重新采集、直接再点
    // "提交上报"才会复用冻结值（PR #68 二次复审 P1）。
    pending = { items: merged.items, requestId: null, capturedAt: null, capped: false };
    await savePendingCapture(site, sellerId, pending);
    panel.submitButton.disabled = pending.items.length === 0;
    panel.status.textContent = describeAccumulated(pending.items.length, hasNextPage(document));
  };

  const handleSubmit = async (): Promise<void> => {
    if (pending.items.length === 0 || pending.capped) return;

    if (!pending.requestId || !pending.capturedAt) {
      const frozen = freezeRequest(pending);
      pending = {
        items: pending.items,
        requestId: frozen.requestId,
        capturedAt: frozen.capturedAt,
        capped: false,
      };
      await savePendingCapture(site, sellerId, pending);
    }
    const requestId = pending.requestId;
    const capturedAt = pending.capturedAt;
    if (!requestId || !capturedAt) return; // 不可达，只是安抚上面刚冻结完的 TS 类型

    panel.collectButton.disabled = true;
    panel.submitButton.disabled = true;
    panel.status.textContent = `提交中…（合并 ${pending.items.length} 个商品）`;

    try {
      const message: CaptureMessage = {
        type: 'CAPTURE_REPORT',
        sellerId,
        site,
        items: pending.items,
        requestId,
        capturedAt,
        pageContext: buildPageContext(url),
        // 店名尽力而为：取不到就不带，不影响 ASIN 提交（#166 D5）
        sellerName: parseSellerName(document),
      };

      const result = (await chrome.runtime.sendMessage(message)) as CaptureResult;
      panel.status.textContent = describeResult(result);

      if (result.outcome === 'success') {
        pending = { ...EMPTY_PENDING };
        await clearPendingCapture(site, sellerId);
      }
      // 其余结局（网络异常、鉴权失效、409、422）一律保留 pending 原样，包括其中
      // 已冻结的 requestId/capturedAt——这正是本轮修复要保证的：不经过重新采集
      // 的下一次提交必须携带同一对值。
    } catch {
      // sendMessage 本身抛出（如 background 未响应）：按网络异常处理，保留已
      // 采集的数据与已冻结的幂等键，允许运营重新点"提交上报"（PR #68 复审
      // P2——按钮不能因异常路径永久停在禁用态）。
      panel.status.textContent = '插件内部通信异常，已采集的数据仍会保留，可重新点"提交上报"';
    } finally {
      panel.collectButton.disabled = collectBlocked();
      panel.submitButton.disabled = pending.items.length === 0 || pending.capped;
    }
  };

  const handleReset = async (): Promise<void> => {
    pending = { ...EMPTY_PENDING };
    await clearPendingCapture(site, sellerId);
    // 撤销「撞上单次上报上限」的硬性阻止；读不到服务端上限时仍保持禁止
    panel.collectButton.disabled = collectBlocked();
    panel.submitButton.disabled = true;
    panel.status.textContent =
      maxReportItems === null ? CONFIG_UNAVAILABLE : describeAccumulated(0, hasNextPage(document));
  };

  panel.collectButton.addEventListener('click', () => {
    void handleCollect();
  });
  panel.submitButton.addEventListener('click', () => {
    void handleSubmit();
  });
  panel.resetLink.addEventListener('click', (event) => {
    event.preventDefault();
    void handleReset();
  });
}

void init();
