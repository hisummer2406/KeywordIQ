/**
 * 「安装为网站应用」的可检测状态（issue #92）。
 *
 * 只做一件事：判断**此刻该不该给用户一个安装入口**，以及点了之后调什么。不碰 Service
 * Worker、不缓存任何东西——本产品必须联网，安装只是把入口放到桌面/程序坞。
 *
 * **必须在应用启动的最早期（`app.tsx` 顶部 import）就开始监听。** `beforeinstallprompt`
 * 每次页面加载只触发一次，晚于它订阅的组件永远收不到；而错误表现只是「入口不出现」，
 * 没有任何报错。所以监听挂在模块加载时，而不是菜单组件挂载时。
 *
 * 不假装能检测「是否已安装」：浏览器没有跨平台可靠的 API（`getInstalledRelatedApps`
 * 只覆盖一部分）。我们只依据三件能检测的事——事件到没到、是不是独立窗口、是不是 Mac Safari。
 */

/** 非标准事件，lib.dom 里没有。 */
export interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

export type InstallMode =
  /** 不显示入口 */
  | 'hidden'
  /** 浏览器给了程序化安装：点击即调用 prompt() */
  | 'prompt'
  /** Mac Safari 没有程序化入口：点击后给「添加到程序坞」的指引 */
  | 'safari-guide';

export interface InstallEnv {
  /** 当前就运行在已安装应用的独立窗口里 */
  standalone: boolean;
  /** 已捕获到、尚未用掉的 beforeinstallprompt 事件 */
  hasPrompt: boolean;
  /** 支持「添加到程序坞」的 Mac Safari（macOS Sonoma / Safari 17+） */
  safariAddToDock: boolean;
}

/**
 * 优先级：独立窗口 > 程序化安装 > Safari 指引。
 *
 * 独立窗口内必须一律隐藏——已经装了还提示安装，是这个入口最容易犯的错。
 * `prompt` 排在 Safari 指引前面：Safari 不会触发 beforeinstallprompt，两者其实互斥，
 * 这个顺序只是把「有更直接的办法就用它」写明白。
 */
export function resolveInstallMode(env: InstallEnv): InstallMode {
  if (env.standalone) return 'hidden';
  if (env.hasPrompt) return 'prompt';
  if (env.safariAddToDock) return 'safari-guide';
  return 'hidden';
}

/** 运行在独立窗口。`navigator.standalone` 是 iOS 的老接口，一并认。 */
export function isStandalone(win: Window = window): boolean {
  try {
    if (win.matchMedia?.('(display-mode: standalone)').matches) return true;
  } catch {
    // matchMedia 在极少数嵌入环境里会抛，视为「不是」，退到下一个判据
  }
  return (win.navigator as Navigator & { standalone?: boolean }).standalone === true;
}

/**
 * 能用「文件 → 添加到程序坞」的 Safari：桌面 Mac、真 Safari、版本 ≥ 17。
 *
 * 版本门槛不是洁癖：Safari 16 及以下没有这个菜单项，照样显示入口，用户按指引找不到
 * 菜单，比没有入口更糟。Chrome/Edge/Firefox 的 UA 里也带 `Safari/`，所以要排除；
 * iPadOS 的 Safari 自称 Macintosh 但有触点，它的路径是「共享 → 添加到主屏幕」，不在本入口范围。
 */
export function isSafariWithAddToDock(win: Window = window): boolean {
  const ua = win.navigator.userAgent;
  if (!/Macintosh/.test(ua) || win.navigator.maxTouchPoints > 1) return false;
  if (!/Safari\//.test(ua) || /Chrome|Chromium|CriOS|FxiOS|Edg|OPR/.test(ua)) return false;
  const version = /Version\/(\d+)/.exec(ua);
  return version !== null && Number(version[1]) >= 17;
}

// ---- 事件捕获（模块级状态；订阅接口给 useSyncExternalStore 用） ----

let deferred: BeforeInstallPromptEvent | null = null;
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((fn) => fn());
}

function setDeferred(next: BeforeInstallPromptEvent | null) {
  if (deferred === next) return;
  deferred = next;
  notify();
}

export function subscribeInstallPrompt(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function hasInstallPrompt(): boolean {
  return deferred !== null;
}

/**
 * 弹出浏览器自己的安装确认。**安装必须由用户确认**，所以只能由用户点击触发，
 * 也没有任何代码路径会自动调用它。
 *
 * `prompt()` 每个事件只能调用一次：无论用户接受还是拒绝，事件都作废了，所以用完立刻清掉，
 * 入口随之消失，直到浏览器再次触发事件。这不是丢功能——浏览器自己的地址栏/菜单里的
 * 安装入口一直在。
 */
export async function promptInstall(): Promise<'accepted' | 'dismissed' | 'unavailable'> {
  const event = deferred;
  if (!event) return 'unavailable';
  setDeferred(null);
  await event.prompt();
  const { outcome } = await event.userChoice;
  return outcome;
}

function startCapture() {
  if (typeof window === 'undefined') return;
  window.addEventListener('beforeinstallprompt', (e) => {
    // 阻止浏览器自己的迷你信息条，改由用户菜单里的入口触发
    e.preventDefault();
    setDeferred(e as BeforeInstallPromptEvent);
  });
  // 装完了（无论从哪个入口装的）：这个事件不会再被用到
  window.addEventListener('appinstalled', () => setDeferred(null));
}

startCapture();
