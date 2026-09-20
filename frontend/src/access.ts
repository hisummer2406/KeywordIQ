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
    // 「词库工具」下两屏（增长市场、我的词库）共用的容器级权限点。不给两屏各开一个
    // ——词库管理的是用户自己的私有清单，「禁止某人管理自己的收藏夹」不是真实的授权需求。
    canViewKeyword: has('keyword:view'),
    // 独立权限点,不复用 keyword:view——每个用户的关注池互相独立,不是"全体共用一个开关"
    // 的授权粒度(design.md 决策 6)。
    canViewCompetitor: has('competitor:view'),
    // 机会中心（issue #76）：当前唯一一个默认不授予任何内置角色的权限点，只有 is_super
    // 能看到——即便某个用户的角色码是 admin，只要 is_super 为假就看不到，与其余按角色
    // 矩阵授予的权限点不同档次（add-opportunity-center-nav/design.md 决策 1）。
    canViewOpportunity: has('opportunity:view'),
    // 「产品档案」「风险核查」「定价核算」：占位屏，没有后端端点，只有 is_super 能看到，
    // 免得运营/分析师点进一个写着「尚未接入」的空屏。判据是 is_super，**不是权限点**：
    // 权限矩阵里没有对应的一项，因此不存在「勾了也不生效」的复选框（`user:manage` 的教训）。
    // 有了真实端点再按数据域各开权限点、把这里的守卫换掉——那时是加法。
    canViewUnreleased: isSuper,
    // 只认 is_super，不认 admin 角色码——把角色码也算进来，等于让「谁能管用户」
    // 变成一件改角色配置就能改变的事，而这正是收紧要排除的
    canManageUsers: isSuper,
    canAdmin: isSuper || currentUser?.role === 'admin',
  };
}
