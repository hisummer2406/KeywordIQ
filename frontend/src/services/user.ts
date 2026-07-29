import { request } from '@umijs/max';

/** 用户管理接口。列表走 ProTable 契约(/table);增改删走 amis 信封(迁移期后端未换),此处解包判 status。 */

export interface UserRow {
  id: number;
  user_name: string;
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

interface AmisEnvelope<T = unknown> {
  status: number;
  msg?: string;
  data?: T;
}

function unwrap<T>(resp: AmisEnvelope<T>): T {
  if (resp.status !== 0) throw new Error(resp.msg || '操作失败');
  return resp.data as T;
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
  const resp = await request<AmisEnvelope>('/api/user/create', { method: 'POST', data: body });
  return unwrap(resp);
}

export async function updateUser(id: number, body: UserUpdateBody) {
  const resp = await request<AmisEnvelope>(`/api/user/update/${id}`, { method: 'PUT', data: body });
  return unwrap(resp);
}

export async function toggleUserStatus(id: number) {
  const resp = await request<AmisEnvelope>(`/api/user/toggle-status/${id}`, { method: 'POST' });
  return unwrap(resp);
}
