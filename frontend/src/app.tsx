import type { RequestConfig, RunTimeLayoutConfig } from '@umijs/max';
import { history } from '@umijs/max';
import { message } from 'antd';
import { getCurrentUser, logout } from '@/services/auth';
import { clearToken, getToken } from '@/utils/token';
import logoIcon from '@/assets/logo-icon.png';
import RightContent from '@/components/RightContent';

const LOGIN_PATH = '/login';

/**
 * 全局初始状态：拉当前登录用户，供 layout 菜单与 access 权限判定使用。
 * 鉴权后端接口在阶段二落地；此处已按最终形态接好，接口未就绪时静默降级为未登录。
 */
export async function getInitialState(): Promise<{
  currentUser?: API.CurrentUser;
  fetchUserInfo?: () => Promise<API.CurrentUser | undefined>;
  /** 右上角账号区读的是这两个键（plugin-layout 的 rightRender 约定），不读 currentUser。 */
  name?: string;
  avatar?: string | false;
}> {
  const fetchUserInfo = async () => {
    try {
      return await getCurrentUser();
    } catch {
      // 拉不到用户说明这张 token 已经不作数了，就地清掉。留着它会让下面的路由守卫
      // （判据是 token）继续放行，用户带着废令牌停在受保护页，既进不去也回不到登录页。
      clearToken();
      return undefined;
    }
  };

  // 按 token 决定是否拉用户（不看路径）：这样登录成功后 refresh() 仍在 /login 也会拉到 user，
  // 避免 currentUser 为空被 onPageChange 踢回登录页；无 token 时跳过，不触发 401。
  if (getToken()) {
    const currentUser = await fetchUserInfo();
    // name 供右上角账号区显示；avatar: false 表示不渲染头像图片——plugin-layout 的默认
    // 头像指向一个外部 CDN，内部系统不该为一张占位图依赖外网。
    return { currentUser, fetchUserInfo, name: currentUser?.username, avatar: false };
  }
  return { fetchUserInfo };
}

export const layout: RunTimeLayoutConfig = ({ initialState, setInitialState }) => {
  /**
   * 登出，与登录严格对称：登录是「发令牌 → 刷新 initialState」，登出就是
   * 「清令牌 → 清 initialState → 回登录页」。三步缺一不可——
   *
   * - 只清 token 不清 initialState：currentUser 还在，onPageChange 与 access 判定
   *   都认为已登录，人留在受保护页却带着一张作废的令牌，每个请求 401。
   * - 只清 initialState 不清 token：下次刷新页面 getInitialState 又拿旧 token 去
   *   换用户，等于没退。
   *
   * 后端登出失败也要把本地清干净：令牌在客户端，服务端那次调用失败不该让用户卡在
   * 一个「以为退了其实没退」的中间态。故异常吞掉但记一笔。
   */
  const doLogout = async () => {
    try {
      await logout();
    } catch (e) {
      console.warn('服务端登出失败，仍继续清理本地会话', e);
    }
    clearToken();
    await setInitialState((s) => ({ ...s, currentUser: undefined, name: undefined }));
    history.push(LOGIN_PATH);
  };

  return {
    title: '海眸系统',
    logo: logoIcon,
    menu: { locale: false },

    // 顶部菜单而非侧边菜单：本系统是数据展示服务，主屏是一张宽表（分析查询有 8 列
    // 定宽、合计约 790px，外加关键词与商品标题两列自适应）。常驻侧边栏吃掉约 208px
    // 横向空间，直接换成表格的横向滚动条。
    //
    // 这三项必须写在**运行时配置**（本文件）而非 .umirc.ts：Umi Max 生成的
    // plugin-layout 只从 .umirc.ts 的 layout 里取 title / locale / formatMessage，
    // 其余键不会传给 ProLayout，写在那边是死配置——不报错，也不生效。
    layout: 'top',
    // 内容区铺满宽度。top 布局下若收成定宽居中，对宽表是反效果：菜单不占地方了，
    // 表格却被夹在中间，横向空间反而更少。
    contentWidth: 'Fluid',
    // 表格长、滚动多，顶栏固定住，切菜单不用滚回顶部。
    fixedHeader: true,
    // 未登录访问受保护页 → 跳登录。
    //
    // 判据是 currentUser 而非 token：access.ts 里的权限判定读的也是 currentUser，
    // 两者必须同源，否则会出现"守卫放行、权限判定不认"的错位——表现为登录后落到 403。
    // 代价是这个回调读到的是上一次渲染的闭包值，所以**跳转方必须等 initialState 提交
    // 之后再跳**（见 pages/Login/index.tsx 的 useEffect），不能刷新完就立即 push。
    onPageChange: () => {
      const { currentUser } = initialState ?? {};
      if (!currentUser && history.location.pathname !== LOGIN_PATH) {
        history.push(LOGIN_PATH);
      }
    },

    /**
     * 右上角账号区。用自定义组件而非 plugin-layout 内置的 rightRender——内置的只有
     * 「头像 + 用户名 + 退出登录」，不显示角色，而本系统是 RBAC，看不到自己的身份时
     * 某个按钮灰着就分不清是没权限还是坏了。
     */
    rightContentRender: () => (
      <RightContent currentUser={initialState?.currentUser} onLogout={doLogout} />
    ),
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
