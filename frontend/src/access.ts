/**
 * 权限工厂：由 initialState.currentUser 的角色/权限码派生路由与菜单权限。
 * 权限码与后端 app/auth/permissions.py 对齐（analysis:view/export、upload:import、user:manage、role:manage）。
 * is_super 视为拥有全部权限（与后端 require_permission 的兜底一致）。
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
    canManageUsers: has('user:manage'),
    canAdmin: isSuper || currentUser?.role === 'admin',
  };
}
