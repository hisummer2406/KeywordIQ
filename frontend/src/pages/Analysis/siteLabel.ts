import type { SiteOption } from '@/services/upload';

const REGIONAL_INDICATOR_A = 0x1f1e6;

export function siteCodeToFlag(code: string): string {
  const normalized = code.trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(normalized)) return '';

  return [...normalized]
    .map((letter) => String.fromCodePoint(REGIONAL_INDICATOR_A + letter.charCodeAt(0) - 65))
    .join('');
}

export function formatSiteLabel(site: SiteOption): string {
  const code = site.code.trim().toUpperCase();
  const flag = siteCodeToFlag(code);
  return `${flag ? `${flag} ` : ''}${site.name_zh}站（${code}）`;
}
