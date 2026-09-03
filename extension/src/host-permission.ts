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
 * 确保已获得 baseUrl 对应来源的 host permission：未授权时弹出 Chrome 的权限
 * 申请对话框，已授权时 Chrome 保证 `request()` 直接返回 true、不弹窗——因此
 * 这里不做"先 `contains()` 查一下再决定要不要 `request()`"的短路。
 *
 * **这不是疏漏，是本次修复的核心**（PR #68 五次复审 P1）：初版在 `request()`
 * 之前 `await` 了一次 `contains()`，这会丢失调用者的 user gesture 上下文——
 * `permissions.request()` 要求必须在用户手势的调用链里同步发起，
 * https://developer.chrome.com/docs/extensions/reference/api/permissions
 * 明确写了这条要求；Chromium 团队在
 * https://groups.google.com/a/chromium.org/g/chromium-extensions/c/u4txOB87_fo
 * 确认过"`request()` 前先 `await` 任意异步调用（哪怕只是 `contains()`）都可能
 * 导致这个上下文丢失"，表现为真机上弹不出权限对话框、`request()` 直接报错
 * "must be called during a user gesture"——本地单测测不出来，因为 stub 的
 * `contains()` 是立即 resolve 的内存操作，不会触发真实 Chrome IPC 的这个坑。
 *
 * 调用方（`options.ts` 的保存按钮 click handler）必须保证：从用户点击到这里
 * 之间不能有任何 `await`——这个函数本身就是那条链上的第一个（也是唯一一个）
 * 异步调用。
 *
 * 抛出异常的情况只有一种：baseUrl 不是合法 URL（`new URL()` 抛错，同步执行，
 * 不消耗 gesture），调用方按"地址格式不对"处理，不归入"权限被拒绝"。
 */
export async function ensureHostPermission(baseUrl: string): Promise<boolean> {
  const origin = toOriginPattern(baseUrl);
  return chrome.permissions.request({ origins: [origin] });
}
