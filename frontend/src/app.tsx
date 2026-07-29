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

/**
 * 统一请求配置：
 * - baseURL 走相对路径（生产 nginx 同源反代 /api；开发由 devServer proxy）。
 * - 请求拦截器注入 Bearer token。
 * - 401 清 token 并跳登录。
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
      // FastAPI 错误体是 {detail: ...}；兼容 message
      const detail = error?.response?.data?.detail || error?.response?.data?.message;

      // 非登录页的 401 才是"会话过期"→ 清 token 跳登录；登录页的 401 是凭证错误，走下方提示
      if (status === 401 && history.location.pathname !== LOGIN_PATH) {
        clearToken();
        message.warning('登录已过期，请重新登录');
        history.push(LOGIN_PATH);
        return;
      }

      if (error?.response) {
        message.error(detail || `请求失败（${status}）`);
      } else if (error?.request) {
        message.error('服务器无响应，请稍后重试');
      }
      throw error;
    },
  },
};
