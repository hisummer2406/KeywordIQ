import { request } from '@umijs/max';

/**
 * 鉴权相关接口占位。真实契约在阶段二（自建用户/角色/JWT）落地，
 * 届时对齐后端登录/令牌/当前用户接口。此处签名先按最终形态给出。
 */

export async function getCurrentUser() {
  return request<API.CurrentUser>('/api/auth/me', { method: 'GET' });
}

export async function login(body: API.LoginParams) {
  // 干净 REST 端点（后端 auth_api：POST /api/auth/token → {token, user}）
  //
  // skipErrorHandler：凭证错误由登录表单自己呈现，不弹全局提示。这是**显式声明**的
  // 静默，而不是页面层 catch 后不作为——后者会把真正的失败一起吞掉。
  return request<API.LoginResult>('/api/auth/token', {
    method: 'POST',
    data: body,
    skipErrorHandler: true,
  });
}

export async function logout() {
  return request<void>('/api/auth/logout', { method: 'POST' });
}

// ---------------- 钉钉单点登录 ----------------
//
// 三个端点都免鉴权（登录页此时还没有令牌）。

/**
 * SSO 是否可用，决定登录页要不要渲染钉钉按钮。
 *
 * skipErrorHandler：这是一次「探测」，失败的正确表现是**按钮不出现**，而不是弹一个
 * 让用户不知所措的红条——他要的只是用密码登录。静默在这里是显式声明的结果，
 * 调用方自己兜底（catch 后按 disabled 处理）。
 */
export async function ssoStatus() {
  return request<API.SsoStatus>('/api/auth/sso/status', {
    method: 'GET',
    skipErrorHandler: true,
  });
}

/** 取钉钉扫码授权页地址。**不传任何参数**——redirect_uri 一律由后端按配置决定。 */
export async function ssoAuthorizeUrl() {
  return request<{ url: string }>('/api/auth/sso/authorize-url', { method: 'GET' });
}

/**
 * 用钉钉授权码换本系统令牌。响应形状与 `login()` 完全一致（`{token, user}`），
 * 因此回调屏能复用登录屏那套令牌落地与跳转逻辑。
 */
export async function ssoCallback(body: { auth_code: string; state: string }) {
  return request<API.LoginResult>('/api/auth/sso/callback', {
    method: 'POST',
    data: body,
  });
}
