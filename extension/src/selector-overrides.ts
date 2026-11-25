// 服务端下发的页面选择器覆盖（extension-remote-selectors）。只是数据：CSS 选择器与纯文本，
// 绝不当成正则或代码执行（MV3 也不允许远程代码）。下发值排在内置默认之前，写错时内置仍有机会命中。

export const SELECTOR_KEYS = [
  'result_card',
  'next_page',
  'seller_name',
  'delivery',
  'blocked_selector',
  'blocked_text',
] as const;

export type SelectorKey = (typeof SELECTOR_KEYS)[number];
export type SelectorOverrides = Partial<Record<SelectorKey, string[]>>;

const MAX_PER_KEY = 10;
const MAX_LEN = 200;

/**
 * 校验服务端返回的覆盖形状；不合法（未知键、非字符串、超长）整体丢弃返回 undefined，
 * 与服务端「不部分生效」一致。CSS 语法只能在页面里校验，见 usableSelectors。
 */
export function normalizeOverrides(raw: unknown): SelectorOverrides | undefined {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined;
  const out: SelectorOverrides = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!(SELECTOR_KEYS as readonly string[]).includes(key)) return undefined;
    if (!Array.isArray(value) || value.length === 0 || value.length > MAX_PER_KEY) return undefined;
    if (!value.every((v) => typeof v === 'string' && v.trim() !== '' && v.length <= MAX_LEN)) return undefined;
    out[key as SelectorKey] = value as string[];
  }
  return out;
}

/** 下发值在前、内置在后，去重；`doc` 给出时跳过 CSS 语法不合法的选择器。 */
export function usableSelectors(remote: string[] | undefined, builtIn: readonly string[], doc?: Document): string[] {
  const merged = [...new Set([...(remote ?? []), ...builtIn])];
  if (!doc) return merged;
  return merged.filter((selector) => {
    try {
      doc.querySelector(selector);
      return true;
    } catch {
      return false;
    }
  });
}
