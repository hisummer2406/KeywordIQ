/**
 * 权限工厂：由 initialState.currentUser 的角色/权限码派生路由与菜单权限。
 * 权限码与后端 app/auth/permissions.py 对齐（analysis:view/export、upload:import）。
 * is_super 视为拥有全部权限（与后端 require_permission 的兜底一致）。
 *
 * **用户管理不走权限码**：它是不可授予的能力，判据只有 is_super，与后端
 * `simple_auth.require_super()` 同源。曾经有过 `user:manage` 权限点，收紧后它变成
 * 一个勾了也不生效的复选框，已从权限矩阵中移除。
 */
export default function access(initialState: { currentUser?: API.CurrentUser } | undefined) {
  const { currentUser } = initialState ?? {};
  const perms = new Set(currentUser?.permissions ?? []);
  const isSuper = !!currentUser?.is_super;
  const has = (code: string) => isSuper || perms.has(code);

  return {
    isLogin: !!currentUser,
    hasPermission: has,
    canViewAnalysis: has('analysis:view'),
    canExport: has('analysis:export'),
    canUpload: has('upload:import'),
    // 工具箱下全部屏幕共用一个权限点。外观专利与全球商标库尚无数据源与端点，
    // 各给一个权限点就成了「勾了也不生效」的复选框。
    canViewToolbox: has('toolbox:view'),
    // 只认 is_super，不认 admin 角色码——把角色码也算进来，等于让「谁能管用户」
    // 变成一件改角色配置就能改变的事，而这正是收紧要排除的
    canManageUsers: isSuper,
    canAdmin: isSuper || currentUser?.role === 'admin',
  };
}
