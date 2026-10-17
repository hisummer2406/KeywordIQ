/**
 * 关键词 → 该站点对应地区的 Google Trends 查询页（issue #78 V0：站外需求验证证据）。
 *
 * Amazon 站点码本就是 ISO 3166-1 alpha-2（见 `app/site_registry.py`），与 Google Trends
 * 的 `geo` 参数直接同构，不再建一张站点→地区映射表——多一份映射就多一处可能与
 * `site_registry` 分叉的地方，而 `GB`/`UK` 那类问题正是这么来的。
 *
 * 时间窗口固定为近 7 天（`date=now 7-d`，issue #125）：这条外链用来看热点词的当下热度，
 * 而不是季节性；要看更长窗口，用户在 Google Trends 页面上自行切换，本系统不提供窗口选择器。
 * 窗口与报表期次无关——查看历史 ABA 报表时它同样是"此刻的最近 7 天"，入口的 title 里点明。
 *
 * 拿不到关键词或站点码时返回 `null`，由调用方降级为不渲染入口——原则同 `amazonSearchUrl`：
 * 宁可不给链接，也不要拼出一个查询不到任何东西的 URL。
 */
export function googleTrendsUrl(keyword: string, siteCode?: string): string | null {
  const k = keyword?.trim();
  const geo = siteCode?.trim().toUpperCase();
  if (!k || !geo) return null;

  const params = new URLSearchParams({ q: k, geo, date: 'now 7-d' });
  return `https://trends.google.com/trends/explore?${params.toString()}`;
}
