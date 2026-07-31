import type { RequestConfig, RunTimeLayoutConfig } from '@umijs/max';
import { history } from '@umijs/max';
import { message } from 'antd';
import { getCurrentUser } from '@/services/auth';
import { clearToken, getToken } from '@/utils/token';
import logoIcon from '@/assets/logo-icon.png';

const LOGIN_PATH = '/login';

/**
 * 全局初始状态：拉当前登录用户，供 layout 菜单与 access 权限判定使用。
 * 鉴权后端接口在阶段二落地；此处已按最终形态接好，接口未就绪时静默降级为未登录。
 */
export async function getInitialState(): Promise<{
  currentUser?: API.CurrentUser;
  fetchUserInfo?: () => Promise<API.CurrentUser | undefined>;
}> {
  const fetchUserInfo = async () => {
    try {
      return await getCurrentUser();
    } catch {
      return undefined;
    }
  };

  // 按 token 决定是否拉用户（不看路径）：这样登录成功后 refresh() 仍在 /login 也会拉到 user，
  // 避免 currentUser 为空被 onPageChange 踢回登录页；无 token 时跳过，不触发 401。
  if (getToken()) {
    const currentUser = await fetchUserInfo();
    return { currentUser, fetchUserInfo };
  }
  return { fetchUserInfo };
}

export const layout: RunTimeLayoutConfig = ({ initialState }) => {
  return {
    title: '海眸系统',
    logo: logoIcon,
    menu: { locale: false },
    // 未登录访问受保护页 → 跳登录
    onPageChange: () => {
      const { currentUser } = initialState ?? {};
      if (!currentUser && history.location.pathname !== LOGIN_PATH) {
        history.push(LOGIN_PATH);
      }
    },
  };
};

/** 后端统一错误契约（app/common/error_handlers.py）。 */
export interface ApiErrorBody {
  success: false;
  code: string;
  message: string;
  request_id: string | null;
  /** 仅参数校验失败（code === 'request.invalid'）时出现 */
  fields?: { field: string; msg: string }[];
}

/** 把统一错误体拼成一句可展示的话；校验错误附上首个出错字段。 */
function describe(body: ApiErrorBody | undefined, status?: number): string {
  if (!body?.message) return `请求失败（${status ?? '网络错误'}）`;
  const first = body.fields?.[0];
  return first ? `${body.message}：${first.field} ${first.msg}` : body.message;
}

/**
 * 统一请求配置：
 * - baseURL 走相对路径（生产 nginx 同源反代 /api；开发由 devServer proxy）。
 * - 请求拦截器注入 Bearer token。
 * - **errorHandler 是错误提示的唯一出口**：页面层不写 try/catch 也能让用户看见失败。
 *
 * 确需不提示的场景（如登录页的凭证错误由表单自己呈现），在发请求时显式声明
 * `skipErrorHandler: true`——静默必须是显式声明的结果，而不是页面层捕获后不作为。
 * 后者正是此前用户屏吞掉「用户名已存在」的机制。
 */
export const request: RequestConfig = {
  baseURL: process.env.UMI_APP_API_BASE || '',
  timeout: 300_000,
  requestInterceptors: [
    (config: any) => {
      const token = getToken();
      if (token) {
        config.headers = { ...config.headers, Authorization: `Bearer ${token}` };
      }
      return config;
    },
  ],
  errorConfig: {
    errorHandler(error: any) {
      const status = error?.response?.status;
      const body: ApiErrorBody | undefined = error?.response?.data;

      // 非登录页的 401 才是"会话过期"→ 清 token 跳登录；登录页的 401 是凭证错误
      if (status === 401 && history.location.pathname !== LOGIN_PATH) {
        clearToken();
        message.warning('登录已过期，请重新登录');
        history.push(LOGIN_PATH);
        return;
      }

      // 显式声明跳过的请求不弹提示，但仍把错误抛给调用方自行处理
      if (error?.config?.skipErrorHandler) {
        throw error;
      }

      if (error?.response) {
        message.error(describe(body, status));
      } else if (error?.request) {
        message.error('服务器无响应，请稍后重试');
      }
      throw error;
    },
  },
};
