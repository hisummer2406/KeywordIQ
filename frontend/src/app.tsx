import type { RequestConfig, RunTimeLayoutConfig } from '@umijs/max';
import { history } from '@umijs/max';
import { message } from 'antd';
import { getCurrentUser } from '@/services/auth';

export const TOKEN_KEY = 'keywordiq_token';
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

  // 登录页不预取用户信息
  if (history.location.pathname !== LOGIN_PATH) {
    const currentUser = await fetchUserInfo();
    return { currentUser, fetchUserInfo };
  }
  return { fetchUserInfo };
}

export const layout: RunTimeLayoutConfig = ({ initialState }) => {
  return {
    title: 'KeywordIQ',
    logo: false,
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
      const token = localStorage.getItem(TOKEN_KEY);
      if (token) {
        config.headers = { ...config.headers, Authorization: `Bearer ${token}` };
      }
      return config;
    },
  ],
  errorConfig: {
    errorHandler(error: any) {
      const status = error?.response?.status;
      if (status === 401) {
        localStorage.removeItem(TOKEN_KEY);
        if (history.location.pathname !== LOGIN_PATH) {
          message.warning('登录已过期，请重新登录');
          history.push(LOGIN_PATH);
        }
        return;
      }
      if (error?.response) {
        message.error(error.response.data?.message || `请求失败（${status}）`);
      } else if (error?.request) {
        message.error('服务器无响应，请稍后重试');
      }
      throw error;
    },
  },
};
