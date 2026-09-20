import type { RequestConfig, RunTimeLayoutConfig } from '@umijs/max';
import { history } from '@umijs/max';
import { message } from 'antd';
import { getCurrentUser, logout } from '@/services/auth';
import { clearSnapshot } from '@/pages/Analysis/stateCache';
import { clearToken, getToken, isStaleAuthResponse } from '@/utils/token';
import logoIcon from '@/assets/logo-icon.png';
import RightContent from '@/components/RightContent';
// 仅为副作用：beforeinstallprompt 每次加载只触发一次，必须在应用最早期就开始监听，
// 等菜单组件挂载再订阅就收不到了（见 utils/pwaInstall.ts 头部）。
import '@/utils/pwaInstall';

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
  const fetchUserInfo = async (): Promise<API.CurrentUser | undefined> => {
    const requestToken = getToken();
    try {
      return await getCurrentUser();
    } catch {
      // 这次 /me 请求发起时用的令牌，若已经不是当前本地持有的令牌，说明本浏览器在它
      // 发出之后又完成过一次新登录（issue #89）——不能因为这个迟到的失败就把刚取得的
      // 新令牌清掉。errorHandler 里"忽略陈旧响应"的处理只管住它自己会不会清理/提示，
      // 挡不住这里：umi 的 request 包装无论 errorHandler 是否再抛都会让原 Promise 照常
      // reject（`src/.umi/plugin-request/request.ts` 的 catch 分支），这个 catch 必须
      // 独立重复一次同样的判断（PR #91 复审 P1）。
      //
      // 光是"不清掉新令牌"还不够：直接 return undefined 会让 getInitialState 把
      // currentUser 发布成空，onPageChange 的路由守卫据此把这个标签页弹回登录页，
      // 而它其实已经拿着一张有效令牌——且 useLoginRedirect 只在 currentUser 落位后
      // 才会离开登录页，这个标签页会卡死在那里出不来。真正要做的是用现在这张新令牌
      // 重新拉一次，让状态追上本地实际持有的会话（PR #91 复审第五轮 P2）。
      if (requestToken !== getToken()) {
        return getToken() ? fetchUserInfo() : undefined;
      }
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
    // 分析屏的会话缓存与令牌同生共死：同一标签页内换账号登录，绝不能看到上一个
    // 用户的查询条件与结果行。
    //
    // **用户保存的默认条件（savedMode.ts）不在这里清，这不是漏掉的。** 两者面对同一个
    // 串账号风险，但解法不同：会话快照是**无主的单份**，只能靠清；保存的条件按 userId
    // 分键，乙登录读的是乙的键，天然看不见甲的。而清掉它会让「下次登录还是这组」当场
    // 不成立——那正是那个功能的全部意义。
    clearSnapshot();
    await setInitialState((s) => ({ ...s, currentUser: undefined, name: undefined }));
    history.push(LOGIN_PATH);
  };

  return {
    title: '海眸系统',
    logo: logoIcon,
    menu: { locale: false },

    // 一级菜单在顶栏，二级菜单在左侧（mix + splitMenus）。
    //
    // 曾经是纯 `top`，理由是主屏那张宽表：分析查询有 8 列定宽、合计约 790px，外加
    // 关键词与商品标题两列自适应，常驻侧边栏吃掉的约 208px 会直接换成横向滚动条。
    // 这个理由现在仍然成立，`mix` 也没有推翻它——**只有带子菜单的一级项才会展开侧栏**，
    // 分析查询没有子项，进去时左侧不占地方，与从前一样宽。
    //
    // 这几项必须写在**运行时配置**（本文件）而非 .umirc.ts：Umi Max 生成的
    // plugin-layout 只从 .umirc.ts 的 layout 里取 title / locale / formatMessage，
    // 其余键不会传给 ProLayout，写在那边是死配置——不报错，也不生效。
    layout: 'mix',
    // 一级留在顶栏、二级下沉到左侧。不开这个，mix 会把整棵菜单树同时塞进顶栏和侧栏。
    splitMenus: true,
    // 内容区铺满宽度。top 布局下若收成定宽居中，对宽表是反效果：菜单不占地方了，
    // 表格却被夹在中间，横向空间反而更少。
    contentWidth: 'Fluid',
    // 表格长、滚动多，顶栏固定住，切菜单不用滚回顶部。
    fixedHeader: true,

    /**
     * 顶栏：白底专业导航，品牌蓝只服务于当前状态。
     *
     * **渐变靠 `colorBgHeader` 实现不了**：这个 token 落到 CSS 的 `background-color`，
     * 而 `linear-gradient(...)` 是 `background-image` 的值，塞进去整条声明会被丢弃、
     * 顶栏保持白色——不报错，只是没效果。当前设计有意用纯白底，若以后确有渐变需求，
     * 需在 global.less 里单独用 `background-image` 实现。
     *
     * 其余 token 是文字与选中态。ProLayout 的 `ant-pro-base-menu-horizontal-menu-item`
     * 盖掉了 antd 横向菜单默认的部分选中样式，因此这里明确给出品牌蓝文字与浅蓝底。
     *
     * 能用 token 就不写选择器：token 是 ProLayout 给的调节口，跟着组件版本走；手写
     * `ant-pro-base-*` 这类内部类名，升级即失效。渐变那一条是没有 token 才落到 CSS 的。
     */
    token: {
      header: {
        colorBgHeader: '#ffffff',
        colorHeaderTitle: '#172b4d',
        colorTextMenu: '#5b6576',
        colorTextMenuSelected: '#1677ff',
        colorTextMenuActive: '#1677ff',
        colorBgMenuItemSelected: '#eaf4ff',
        colorBgMenuItemHover: '#f4f8ff',
        colorTextRightActionsItem: '#5b6576',
        colorBgRightActionsItemHover: '#f4f8ff',
      },
    },
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

      // 这次失败请求发起时用的令牌，若"当前仍存在另一张有效令牌"且与它不同，说明
      // 本浏览器在它发出之后又完成过一次新登录（issue #89）——这是一个迟到的、属于
      // 旧令牌的响应，MUST NOT 反过来把刚取得的新登录状态清掉。**必须在按路由分支
      // 处理之前判断**：重新登录成功到跳离 /login 之间有一段窗口期（`useLoginRedirect`
      // 等 currentUser 落位才 push），此时仍在 /login，若把这个判断放进下面按路由分支
      // 的 if 块里，会连同整块一起被路由条件跳过，这类响应就会落进后面的通用错误分支
      // 弹出一条虚假提示（PR #91 审核 P2）。`requestToken` 取不到时（如某些未附带请求
      // 配置的调用方式）不认为是陈旧响应，沿用"按正常 401 处理"的安全默认。
      //
      // `isStaleAuthResponse` 同时排除了"当前令牌是 null"的情形——那是另一个标签页
      // 主动退出、本地已无令牌，不是"陈旧响应"而是真实的会话结束，必须继续往下走
      // 清理分支；否则这个标签页会带着一份服务端已经不存在的登录状态卡住，没有任何
      // storage 事件监听会替它清理（PR #91 复审第七轮）。
      if (status === 401) {
        const authHeader: string | undefined = error?.config?.headers?.Authorization;
        const requestToken = authHeader ? authHeader.replace(/^Bearer /, '') : null;
        if (requestToken && isStaleAuthResponse(requestToken)) {
          return;
        }
      }

      // 非登录页的 401 才是"会话过期"→ 清 token 跳登录；登录页的 401 是凭证错误
      if (status === 401 && history.location.pathname !== LOGIN_PATH) {
        clearToken();
        // 分析屏的会话缓存与令牌同生共死（doLogout 同一条纪律）：不清的话，甲被顶号后
        // 乙在同一浏览器登录，会先看到甲遗留的查询条件与结果行。
        clearSnapshot();
        // 会话被顶替（auth.session_invalid）呈现区别于普通过期的明确原因
        const reason =
          body?.code === 'auth.session_invalid' ? '账号已在其他浏览器登录' : '登录已过期，请重新登录';
        message.warning(reason);
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
