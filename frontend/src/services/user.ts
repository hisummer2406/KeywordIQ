import { request } from '@umijs/max';

/** 用户管理接口。列表走 ProTable 契约(/table);增改删直接返数据。
 * 失败一律是非 2xx 的统一错误契约，由 app.tsx 的 errorHandler 唯一出口提示。 */

export interface UserRow {
  id: number;
  user_name: string;
  /** 展示名。SSO 账号取自身份源姓名；本地账号通常为空。 */
  display_name: string | null;
  /** 账号来源。null = 本地账号（可用密码登录）；'dingtalk' = 钉钉扫码自动建档。 */
  external_idp: string | null;
  is_active: boolean;
  is_super: boolean;
  role_id: number | null;
  role_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface RoleOption {
  id: number;
  code: string;
  name: string;
}

export interface UserCreateBody {
  user_name: string;
  password: string;
  role_id?: number;
  is_active?: boolean;
}

export interface UserUpdateBody {
  user_name?: string;
  password?: string;
  role_id?: number;
  is_active?: boolean;
}

/** ProTable request：拉分页用户列表({data,total,success})。 */
export async function fetchUsers(params: {
  current?: number;
  pageSize?: number;
  user_name?: string;
}) {
  const res = await request<{ data: UserRow[]; total: number; success: boolean }>(
    '/api/user/table',
    {
      method: 'GET',
      params: {
        page: params.current ?? 1,
        per_page: params.pageSize ?? 20,
        user_name: params.user_name,
      },
    },
  );
  return { data: res.data ?? [], total: res.total ?? 0, success: res.success !== false };
}

export async function getRoles() {
  return request<RoleOption[]>('/api/user/roles', { method: 'GET' });
}

export async function createUser(body: UserCreateBody) {
  return request<UserRow>('/api/user/create', { method: 'POST', data: body });
}

export async function updateUser(id: number, body: UserUpdateBody) {
  return request<UserRow>(`/api/user/update/${id}`, { method: 'PUT', data: body });
}

export async function toggleUserStatus(id: number) {
  return request<{ id: number; is_active: boolean }>(`/api/user/toggle-status/${id}`, {
    method: 'POST',
  });
}
