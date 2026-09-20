import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  isSafariWithAddToDock,
  isStandalone,
  resolveInstallMode,
  type InstallEnv,
} from '@/utils/pwaInstall';

const base: InstallEnv = { standalone: false, hasPrompt: false, safariAddToDock: false };

describe('resolveInstallMode', () => {
  it('普通标签页 + 浏览器给了安装事件 → prompt', () => {
    expect(resolveInstallMode({ ...base, hasPrompt: true })).toBe('prompt');
  });

  it('Mac Safari（无事件）→ 指引', () => {
    expect(resolveInstallMode({ ...base, safariAddToDock: true })).toBe('safari-guide');
  });

  it('独立窗口内一律隐藏，不论事件或 Safari', () => {
    expect(resolveInstallMode({ standalone: true, hasPrompt: true, safariAddToDock: true })).toBe(
      'hidden',
    );
  });

  it('不支持的环境 → 隐藏，不显示无效入口', () => {
    expect(resolveInstallMode(base)).toBe('hidden');
  });

  it('有安装事件时优先程序化安装，而不是 Safari 指引', () => {
    expect(resolveInstallMode({ ...base, hasPrompt: true, safariAddToDock: true })).toBe('prompt');
  });
});

/** 只填被读取的字段的假 window，避免依赖 jsdom 的 UA。 */
function fakeWindow(opts: {
  ua?: string;
  touch?: number;
  standaloneMedia?: boolean;
  navStandalone?: boolean;
  matchMediaThrows?: boolean;
}): Window {
  return {
    matchMedia: opts.matchMediaThrows
      ? () => {
          throw new Error('unsupported');
        }
      : () => ({ matches: opts.standaloneMedia ?? false }),
    navigator: {
      userAgent: opts.ua ?? '',
      maxTouchPoints: opts.touch ?? 0,
      standalone: opts.navStandalone,
    },
  } as unknown as Window;
}

describe('isStandalone', () => {
  it('display-mode: standalone 命中', () => {
    expect(isStandalone(fakeWindow({ standaloneMedia: true }))).toBe(true);
  });

  it('iOS 的 navigator.standalone 也认', () => {
    expect(isStandalone(fakeWindow({ navStandalone: true }))).toBe(true);
  });

  it('普通标签页 → false', () => {
    expect(isStandalone(fakeWindow({}))).toBe(false);
  });

  it('matchMedia 抛错时不崩溃，退到下一个判据', () => {
    expect(isStandalone(fakeWindow({ matchMediaThrows: true }))).toBe(false);
    expect(isStandalone(fakeWindow({ matchMediaThrows: true, navStandalone: true }))).toBe(true);
  });
});

const MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)';

describe('isSafariWithAddToDock', () => {
  it('Mac Safari 17 → true', () => {
    expect(isSafariWithAddToDock(fakeWindow({ ua: `${MAC} Version/17.4 Safari/605.1.15` }))).toBe(
      true,
    );
  });

  it('Safari 16 没有「添加到程序坞」菜单 → false（按指引找不到菜单比没有入口更糟）', () => {
    expect(isSafariWithAddToDock(fakeWindow({ ua: `${MAC} Version/16.6 Safari/605.1.15` }))).toBe(
      false,
    );
  });

  it('Mac 上的 Chrome / Edge 的 UA 也带 Safari/，必须排除', () => {
    const chrome = 'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36';
    expect(isSafariWithAddToDock(fakeWindow({ ua: `${MAC} ${chrome}` }))).toBe(false);
    expect(isSafariWithAddToDock(fakeWindow({ ua: `${MAC} ${chrome} Edg/125.0.0.0` }))).toBe(false);
  });

  it('iPadOS Safari 自称 Macintosh 但有触点，走的是另一条路径 → false', () => {
    expect(
      isSafariWithAddToDock(fakeWindow({ ua: `${MAC} Version/17.4 Safari/605.1.15`, touch: 5 })),
    ).toBe(false);
  });

  it('Windows Chrome → false', () => {
    const win = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36';
    expect(isSafariWithAddToDock(fakeWindow({ ua: win }))).toBe(false);
  });
});

/**
 * 事件捕获用的是模块级状态，每个用例都要一份全新的模块，否则上一个用例遗留的事件会
 * 让下一个用例的「初始没有事件」断言莫名其妙失败。
 */
describe('beforeinstallprompt 捕获', () => {
  type Mod = typeof import('@/utils/pwaInstall');
  let mod: Mod;

  beforeEach(async () => {
    vi.resetModules();
    mod = await import('@/utils/pwaInstall');
  });

  function fireInstallPrompt(outcome: 'accepted' | 'dismissed' = 'accepted') {
    const event = new Event('beforeinstallprompt', { cancelable: true }) as Event & {
      prompt: ReturnType<typeof vi.fn>;
      userChoice: Promise<{ outcome: string }>;
    };
    event.prompt = vi.fn().mockResolvedValue(undefined);
    event.userChoice = Promise.resolve({ outcome });
    window.dispatchEvent(event);
    return event;
  }

  it('模块加载时就已在监听：事件到达后 hasInstallPrompt 为真，并阻止浏览器自带信息条', () => {
    expect(mod.hasInstallPrompt()).toBe(false);
    const event = fireInstallPrompt();
    expect(mod.hasInstallPrompt()).toBe(true);
    expect(event.defaultPrevented).toBe(true);
  });

  it('订阅者在事件到达时被通知', () => {
    const fn = vi.fn();
    const unsubscribe = mod.subscribeInstallPrompt(fn);
    fireInstallPrompt();
    expect(fn).toHaveBeenCalledTimes(1);
    unsubscribe();
    fireInstallPrompt();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('promptInstall 调用一次 prompt() 并返回用户的选择', async () => {
    const event = fireInstallPrompt('accepted');
    await expect(mod.promptInstall()).resolves.toBe('accepted');
    expect(event.prompt).toHaveBeenCalledTimes(1);
  });

  it('用户拒绝后事件作废：入口消失，且不会对同一事件再次 prompt()', async () => {
    const event = fireInstallPrompt('dismissed');
    await expect(mod.promptInstall()).resolves.toBe('dismissed');
    expect(mod.hasInstallPrompt()).toBe(false);
    await expect(mod.promptInstall()).resolves.toBe('unavailable');
    expect(event.prompt).toHaveBeenCalledTimes(1);
  });

  it('没有事件时 promptInstall 是空操作，不抛错', async () => {
    await expect(mod.promptInstall()).resolves.toBe('unavailable');
  });

  it('appinstalled（从别的入口装完）清掉事件', () => {
    fireInstallPrompt();
    expect(mod.hasInstallPrompt()).toBe(true);
    window.dispatchEvent(new Event('appinstalled'));
    expect(mod.hasInstallPrompt()).toBe(false);
  });
});
