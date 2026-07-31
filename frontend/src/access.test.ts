import { describe, expect, it } from 'vitest';
import access from '@/access';

/**
 * 权限派生是菜单显隐与导出按钮显隐的唯一依据，且必须与后端 permissions.py 的权限码
 * 保持一致——码写错不会报错，只会静默少一个按钮，所以值得单测守住。
 */
describe('access 权限派生', () => {
  const build = (permissions: string[], is_super = false) =>
    access({ currentUser: { permissions, is_super } as unknown as API.CurrentUser });

  it('未登录时一切皆无', () => {
    const a = access(undefined);
    expect(a.isLogin).toBe(false);
    expect(a.canViewAnalysis).toBe(false);
    expect(a.canExport).toBe(false);
  });

  it('按权限码逐项派生，不越权', () => {
    const a = build(['analysis:view']);
    expect(a.canViewAnalysis).toBe(true);
    expect(a.canExport).toBe(false); // 看得到 ≠ 导得出
    expect(a.canUpload).toBe(false);
    expect(a.canManageUsers).toBe(false);
  });

  it('analysis:export 单独控制导出', () => {
    expect(build(['analysis:view', 'analysis:export']).canExport).toBe(true);
  });

  it('is_super 兜底拥有全部权限（与后端 require_permission 一致）', () => {
    const a = build([], true);
    expect(a.canViewAnalysis).toBe(true);
    expect(a.canExport).toBe(true);
    expect(a.canUpload).toBe(true);
    expect(a.canManageUsers).toBe(true);
  });
});
