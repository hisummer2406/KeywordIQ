/**
 * 关键词 → Meta 广告库（Ad Library）搜索结果页，与 `googleTrendsUrl` 同一类外链：
 * 查竞品是否在该词/品牌上投放 Facebook/Instagram 广告，属于"站外投放证据"，不是"站内
 * 需求验证"——两者都只做外链、不抓取不存储数据，公开页面无需登录或 API 授权。
 *
 * `country` 与 `googleTrendsUrl` 的 `geo` 同理：Amazon 站点码本就是 ISO 3166-1
 * alpha-2，与 Meta 广告库的 `country` 参数直接同构，不建映射表。
 *
 * 默认只看 `active_status=active`（正在投放的广告）——这是用户在广告库页面手动搜索
 * 关键词时的默认视图，切到"全部广告"是用户自己在页面上做的选择，不是这条外链要预判的事。
 *
 * 拿不到关键词或站点码时返回 `null`，原则同 `googleTrendsUrl`：宁可不给入口，
 * 也不要拼出一个查不到任何东西的 URL。
 */
export function facebookAdLibraryUrl(keyword: string, siteCode?: string): string | null {
  const k = keyword?.trim();
  const country = siteCode?.trim().toUpperCase();
  if (!k || !country) return null;

  const params = new URLSearchParams({
    active_status: 'active',
    ad_type: 'all',
    country,
    q: k,
    search_type: 'keyword_unordered',
  });
  return `https://www.facebook.com/ads/library/?${params.toString()}`;
}
