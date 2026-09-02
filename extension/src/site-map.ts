// 手工镜像 app/site_registry.py 的 SITE_CATALOG。两边改动必须同步——插件是纯前端
// JS，没有办法直接 import 后端 Python 模块。新增站点时：
//   1. 改 app/site_registry.py 的 SITE_CATALOG
//   2. 改这个文件的两张映射表
//   3. 改 test/site-map.test.ts 里固定断言的站点集合
// （design.md 决策 2）

/** marketplaceID 查询参数 → 站点码。 */
export const MARKETPLACE_ID_TO_SITE: Record<string, string> = {
  ATVPDKIKX0DER: 'US',
  A1F83G8C2ARO7: 'GB', // 英国站，不是 UK——见 site_registry.py 的既有纪律
  A1PA6795UKMFR9: 'DE',
};

/** 域名 → 站点码，marketplaceID 缺失时的回落。 */
export const DOMAIN_TO_SITE: Record<string, string> = {
  'amazon.com': 'US',
  'amazon.co.uk': 'GB',
  'amazon.de': 'DE',
};

/**
 * 从店铺列表页 URL 解析站点码。优先取 marketplaceID，查不到或未登记则回落域名；
 * 两者都失败返回 null——不猜测默认站点（与 normalize_site_code 同一条纪律）。
 */
export function resolveSite(rawUrl: string): string | null {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }

  const marketplaceId = url.searchParams.get('marketplaceID');
  if (marketplaceId && MARKETPLACE_ID_TO_SITE[marketplaceId]) {
    return MARKETPLACE_ID_TO_SITE[marketplaceId];
  }

  const host = url.hostname.replace(/^www\./, '');
  return DOMAIN_TO_SITE[host] ?? null;
}
