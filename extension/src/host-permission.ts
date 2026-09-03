// API 地址对应的 host permission：计算 match pattern，并在用户保存地址时动态
// 申请（PR #68 四次复审 P1）。
//
// design.md 此前记录的"CORS 依赖"推理是对 MV3 权限模型的误判：service worker
// 的跨域 fetch 在浏览器网络层就按 host_permissions 放行与否做判断，与目标服务器
// 是否返回 Access-Control-Allow-Origin 无关——没有 host_permissions，请求根本
// 发不出去，不会到达服务器，也就不存在"服务器 CORS 配置是否宽松"这回事
// （https://developer.chrome.com/docs/extensions/develop/concepts/network-requests）。
// 这意味着此前的实现在任何真机环境下都无法成功提交，只是本地单测全部走的是
// 注入的 fetch 替身，绕过了浏览器网络层，从未暴露这个问题。
//
// API 地址是用户在 options page 里任填的（本地开发、生产域名皆可能），不能在
// manifest 里固定声明某一个 host_permissions；因此 manifest 改为声明
// `optional_host_permissions: ["http://*/*", "https://*/*"]`，具体来源只在用户
// 保存地址时按需申请——用户看到的 Chrome 权限弹窗会精确到这一个域，而不是安装时
// 就要求"访问所有网站"。

export function toOriginPattern(baseUrl: string): string {
  const url = new URL(baseUrl);
  return `${url.protocol}//${url.host}/*`;
}

/**
 * 确保已获得 baseUrl 对应来源的 host permission：已授权则直接返回 true；
 * 未授权则弹出 Chrome 的权限申请对话框，返回用户的选择结果。
 *
 * 抛出异常的情况只有一种：baseUrl 不是合法 URL（`new URL()` 抛错），调用方
 * 按"地址格式不对"处理，不归入"权限被拒绝"。
 */
export async function ensureHostPermission(baseUrl: string): Promise<boolean> {
  const origin = toOriginPattern(baseUrl);
  const already = await chrome.permissions.contains({ origins: [origin] });
  if (already) return true;
  return chrome.permissions.request({ origins: [origin] });
}
