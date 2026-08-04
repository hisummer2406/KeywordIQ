/**
 * 关键词 → 该站点亚马逊搜索结果页。
 *
 * 域名取自 `site_registry`（经 `/api/upload/sites` 下发），不在前端硬编码——站点码到
 * 域名的映射多一份就会漂移，而 `GB` 对应的是 `amazon.co.uk` 这种不能靠猜的对应关系。
 *
 * 拿不到域名或关键词为空时返回 `null`，由调用方降级为纯文本：**宁可不给链接，也不要
 * 拼出一个 `https://undefined/s?k=` 让用户点进一个错误页面**——站点列表尚未加载完时
 * 就会走到这条路径。
 */
export function amazonSearchUrl(keyword: string, domain?: string): string | null {
  const k = keyword?.trim();
  if (!k || !domain) return null;
  return `https://www.${domain}/s?k=${encodeURIComponent(k)}`;
}
