/**
 * ASIN → 该站点亚马逊的商品详情页。
 *
 * 与 `amazonSearchUrl` 并列，两者复用同一个域名来源（`site_registry` 经
 * `/api/upload/sites` 下发），不在前端硬编码——`GB` 对应 `amazon.co.uk` 这种映射靠猜是
 * 猜不出来的，多存一份就会漂移。
 *
 * 存在的理由是**图床覆盖率低**：实测随机 12 个 ASIN 仅 2 个有图，其余返回 1×1 透明像素
 * （见 `productImage.ts`）。占位方块因此是常态而非例外，若它同时还是个死胡同，用户就没有
 * 任何途径确认这到底是什么商品。链接挂在整张卡牌上而非图片上，正是为了让无图的卡牌也能点。
 *
 * 拿不到域名或 ASIN 为空时返回 `null`，由调用方降级为不可点击——宁可不给链接，也不要拼出
 * `https://undefined/dp/...` 让用户点进错误页面。站点列表尚未加载完时就会走到这条路径。
 */
export function amazonProductUrl(asin: string | null | undefined, domain?: string): string | null {
  const id = (asin || '').trim();
  if (!id || !domain) return null;
  return `https://www.${domain}/dp/${encodeURIComponent(id)}`;
}
