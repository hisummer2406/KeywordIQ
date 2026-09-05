/**
 * 关键词 → TikTok 广告库（Commercial Content Library）搜索结果页，与
 * `googleTrendsUrl` / `facebookAdLibraryUrl` 同一类"站外投放证据"外链，公开页面，
 * 无需登录或 API 授权。
 *
 * **覆盖范围只有欧洲监管区（EU 成员国 + 英国/瑞士/土耳其），不含美国**——实测该库的
 * 「Ad target country」下拉列表里没有 United States（这项透明度义务源自欧盟 DSA/
 * 英国 Online Safety Act 一类立法，覆盖范围随立法走，不是全球）。`SUPPORTED_REGIONS`
 * 因此只登记当前 `site_registry` 用得到、且已实测确认在列的两个站点码；US 恒不渲染——
 * 原则同其余两条外链：宁可空着，也不给一个选不出对应国家、打开后必然是空结果的链接。
 *
 * **该页面不支持深链接自动查询**：URL 参数能正确预填国家/关键词/日期，但结果列表不会
 * 跟着自动查询——实测同一个 URL，页面内点击 Search 能看到结果，重新整页打开这个 URL
 * 却显示 0 条，必须用户自己在落地页上再点一次 Search。**调用方必须在图标旁明确提示
 * 这一点**，不能当作与另外两条外链一样"点开即看到结果"的入口——那会让用户把"还没点
 * 搜索"误读成"这个词确实没有广告在投"。
 *
 * `start_time`/`end_time` 是毫秒时间戳，按官方页面默认行为取"当前时刻往前 31 天"这个
 * 滚动窗口（实测页面默认日期范围即为此）。
 */

/** 实测确认在覆盖范围内的站点码，仅覆盖当前 `site_registry` 会用到的两个。 */
const SUPPORTED_REGIONS = new Set(['GB', 'DE']);

const WINDOW_MS = 31 * 24 * 60 * 60 * 1000;

export function tiktokAdLibraryUrl(keyword: string, siteCode?: string): string | null {
  const k = keyword?.trim();
  const region = siteCode?.trim().toUpperCase();
  if (!k || !region || !SUPPORTED_REGIONS.has(region)) return null;

  const end = Date.now();
  const params = new URLSearchParams({
    region,
    adv_name: k,
    start_time: String(end - WINDOW_MS),
    end_time: String(end),
    query_type: '1',
    ad_type: '0',
    ad_status: '1',
    ages: 'all',
    ad_reach: 'all',
    gender: 'ALL',
    sort_type: 'create_time,desc',
  });
  return `https://library.tiktok.com/ads?${params.toString()}`;
}
