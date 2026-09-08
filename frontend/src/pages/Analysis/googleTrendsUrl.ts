/**
 * 关键词 → 该站点对应地区的 Google Trends 查询页（issue #78 V0：站外需求验证证据）。
 *
 * Amazon 站点码本就是 ISO 3166-1 alpha-2（见 `app/site_registry.py`），与 Google Trends
 * 的 `geo` 参数直接同构，不再建一张站点→地区映射表——多一份映射就多一处可能与
 * `site_registry` 分叉的地方，而 `GB`/`UK` 那类问题正是这么来的。
 *
 * 默认时间窗口取近 12 个月（`date=today 12-m`）：这条外链要辅助判断的正是季节性，
 * 窗口太短看不出来。V0 不提供自定义窗口——那是"内嵌趋势图"的范畴，依赖正式 API 授权。
 *
 * 拿不到关键词或站点码时返回 `null`，由调用方降级为不渲染入口——原则同 `amazonSearchUrl`：
 * 宁可不给链接，也不要拼出一个查询不到任何东西的 URL。
 */
export function googleTrendsUrl(keyword: string, siteCode?: string): string | null {
  const k = keyword?.trim();
  const geo = siteCode?.trim().toUpperCase();
  if (!k || !geo) return null;

  const params = new URLSearchParams({ q: k, geo, date: 'today 12-m' });
  return `https://trends.google.com/trends/explore?${params.toString()}`;
}
