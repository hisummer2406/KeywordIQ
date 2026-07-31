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
