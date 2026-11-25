// 自动采集（试点，#205 阶段 1）的设置存取。默认关闭：安装或升级后不会自己打开
// 任何 Amazon 页面，必须运营在设置页明确开启（delta spec「自动采集默认关闭」）。

const AUTO_CONFIG_KEY = 'keywordiq_auto_collect_config_v1';

export interface AutoConfig {
  enabled: boolean;
  /** 每日触发时刻，本地时区 "HH:MM"。 */
  dailyTime: string;
  /** 每家店采集的页数上限（按最新排序的前 N 页）。 */
  pages: number;
  /** 每轮最多处理的店铺数。 */
  maxStores: number;
  /**
   * 各站点的目标配送邮编（#205 阶段 1）。专用 Chrome Profile 里手动设好，插件只核对是否生效，
   * 不自动切换。没有目标的站点无法确认配送位置，自动采集会在该站点的店停止。
   */
  deliveryTargets: Record<string, string>;
}

export const DEFAULT_AUTO_CONFIG: AutoConfig = {
  enabled: false,
  dailyTime: '08:00',
  pages: 2,
  maxStores: 20,
  deliveryTargets: { US: '10001' },
};

/** 各站点目标邮编的合法形态；设置页只开放美国站（单站试点）。 */
export const POSTCODE_PATTERN: Record<string, RegExp> = {
  US: /^\d{5}$/,
  DE: /^\d{5}$/,
  GB: /^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$/i,
};

export function isValidPostcode(site: string, value: string): boolean {
  return POSTCODE_PATTERN[site]?.test(value.trim()) ?? false;
}

function normalizeTargets(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== 'object') return { ...DEFAULT_AUTO_CONFIG.deliveryTargets };
  const out: Record<string, string> = {};
  for (const [site, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof value === 'string' && isValidPostcode(site, value)) out[site] = value.trim().toUpperCase();
  }
  return out;
}

export const PAGES_RANGE = { min: 1, max: 5 } as const;
export const MAX_STORES_RANGE = { min: 1, max: 200 } as const;

const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;

function clampInt(value: unknown, min: number, max: number, fallback: number): number {
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.round(n)));
}

/** 把任意存储值规整成合法配置；非法字段回落默认值，开关只认严格的 true。 */
export function normalizeAutoConfig(raw: unknown): AutoConfig {
  const value = (raw && typeof raw === 'object' ? raw : {}) as Partial<Record<keyof AutoConfig, unknown>>;
  const dailyTime =
    typeof value.dailyTime === 'string' && TIME_PATTERN.test(value.dailyTime)
      ? value.dailyTime
      : DEFAULT_AUTO_CONFIG.dailyTime;
  return {
    enabled: value.enabled === true,
    dailyTime,
    pages: clampInt(value.pages, PAGES_RANGE.min, PAGES_RANGE.max, DEFAULT_AUTO_CONFIG.pages),
    maxStores: clampInt(
      value.maxStores,
      MAX_STORES_RANGE.min,
      MAX_STORES_RANGE.max,
      DEFAULT_AUTO_CONFIG.maxStores,
    ),
    deliveryTargets: normalizeTargets(value.deliveryTargets),
  };
}

export function isValidDailyTime(value: string): boolean {
  return TIME_PATTERN.test(value);
}

export async function getAutoConfig(): Promise<AutoConfig> {
  const stored = await chrome.storage.local.get(AUTO_CONFIG_KEY);
  return normalizeAutoConfig(stored[AUTO_CONFIG_KEY]);
}

export async function setAutoConfig(config: AutoConfig): Promise<AutoConfig> {
  const normalized = normalizeAutoConfig(config);
  await chrome.storage.local.set({ [AUTO_CONFIG_KEY]: normalized });
  return normalized;
}

/** 下一次每日触发的时间戳（毫秒）：今天的该时刻还没到就用今天，否则明天。 */
export function nextDailyRunAt(dailyTime: string, now: Date): number {
  const [hh, mm] = dailyTime.split(':').map(Number);
  const next = new Date(now);
  next.setHours(hh ?? 8, mm ?? 0, 0, 0);
  if (next.getTime() <= now.getTime()) next.setDate(next.getDate() + 1);
  return next.getTime();
}
