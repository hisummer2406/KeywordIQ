import '@umijs/max/typings';

/**
 * 全局 API 类型。字段随后端契约在各阶段细化，这里先给鉴权与列表响应的基础形状。
 * 用 declare global 暴露为全局命名空间（本文件含 import 已是模块，否则 namespace 会被局部化）。
 */
declare global {
  namespace API {
    type CurrentUser = {
      id: number;
      username: string;
      role: string | null;
      /** 角色展示名（与后端 role 表同源，前端不自建 code → 中文名映射） */
      role_name?: string | null;
      permissions: string[];
      is_active?: boolean;
      is_super?: boolean;
      nickname?: string;
      avatar?: string;
      /** 展示名。SSO 账号取自身份源姓名；本地账号可能为空。 */
      display_name?: string | null;
      /** 外部身份源；null 表示本地账号（可用密码登录）。 */
      external_idp?: string | null;
    };

    /** SSO 可用性探测。关闭时登录页不渲染钉钉按钮。 */
    type SsoStatus = {
      enabled: boolean;
      provider_name: string;
    };

    type LoginParams = {
      username: string;
      password: string;
    };

    type LoginResult = {
      token: string;
      user: CurrentUser;
    };

    /** ProTable 统一列表契约（后端响应适配层输出，阶段三落地） */
    type TableResponse<T> = {
      data: T[];
      total: number;
      success: boolean;
    };
  }
}
