/**
 * 由 ASIN 推导商品缩略图地址。
 *
 * 报表不含图片地址，缩略图是辅助识别信息——为它引入抓取、存储与失效策略不成比例，
 * 所以走亚马逊图床热链，由用户浏览器直接加载。
 *
 * 地址形如 `https://m.media-amazon.com/images/P/<ASIN>.01._SL<边长>_.jpg`。
 *
 * 关于那个 `.01.`（实测结论，2026-08-04，勿凭直觉改动）：
 *
 * - 它**不是**站点/市场码。`.01.` / `.02.` / `.03.` 对同一 ASIN 返回**完全相同**的字节，
 *   连只在德国站销售的商品（ASIN 即 ISBN）也一样——图床按 ASIN 全局解析，取值被忽略。
 *   因此这里不按站点分支：那样的映射不产生任何效果，却会让未登记的站点直接不出图。
 * - 但它**不能省略**：少了这一段，`_SL112_` 不再被当作尺寸指令，返回的是全尺寸原图
 *   （实测 29KB vs 2.9KB）。一页 50 行 × 3 张图就是 4.3MB 与 435KB 的差别。
 *
 * 另一条实测结论影响调用方：**不存在的 ASIN 返回 HTTP 200 + 43 字节的 1×1 透明 GIF，
 * 而不是 404**，所以 `<img onError>` 永远不会触发，降级必须靠加载后检查图片尺寸。
 * 见 TopProductCards 的 Thumbnail。
 */

/** 图床固定前缀段。取值无关紧要，但省略会让尺寸指令失效——详见上方注释。 */
const IMAGE_VARIANT = '01';

/** 缩略图边长（像素）。卡牌里显示 72px，取 2 倍以适配高分屏。 */
const THUMBNAIL_SIZE = 144;

/** 图床返回的占位透明像素边长。真实图片远大于此，据此判定"这个 ASIN 没有图"。 */
export const PLACEHOLDER_PIXEL_SIZE = 1;

/**
 * 返回这个 ASIN 的缩略图地址；ASIN 为空时返回 null
 * （调用方据此直接渲染占位，不要拼出畸形地址再等它返回透明像素）。
 */
export function productImageUrl(asin: string | null | undefined): string | null {
  const id = (asin || '').trim();
  if (!id) return null;

  return `https://m.media-amazon.com/images/P/${id}.${IMAGE_VARIANT}._SL${THUMBNAIL_SIZE}_.jpg`;
}
