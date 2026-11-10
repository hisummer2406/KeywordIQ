import { describe, expect, it } from 'vitest';

import { routes as configuredRoutes } from '@/routes';

/**
 * 顶栏结构的回归。
 *
 * 菜单结构没有别的地方会报错：改错了顶栏顺序、给某项加了不该有的 access、把单页顶层
 * 变成带子项的分组，跑起来都是"能用"的，只是形态不对——而形态正是这一屏的全部内容。
 *
 * 尤其是「单页顶层不得长出子项」那条：layout 是 mix + splitMenus，顶层节点一有子项就
 * 长出约 208px 的左侧栏。分析查询屏是全产品最宽的表，那 208px 是每次查询都在付的。
 */

type Route = {
  name?: string;
  path?: string;
  redirect?: string;
  access?: string;
  hideInMenu?: boolean;
  layout?: boolean;
  routes?: Route[];
};

const routes: Route[] = configuredRoutes;

/** 顶栏里真正出现的一级项：有 name、不隐藏、不是登录那类脱离 ProLayout 的屏。 */
const topLevel = routes.filter((r) => r.name && !r.hideInMenu && r.layout !== false);

describe('顶栏一级菜单', () => {
  it('八项四字，顺序固定：机会中心排最前，其余按既有相对顺序，喂数据/管数据的在最后', () => {
    expect(topLevel.map((r) => r.name)).toEqual([
      '机会中心',
      '市场分析',
      '词库工具',
      '竞对监控',
      '产品档案',
      '风险核查',
      '定价核算',
      '数据管理',
    ]);
  });

  it('市场分析与机会中心是单页顶层，没有子项', () => {
    // 有子项就长出左侧栏，见本文件头部注释与 design.md 决策 2。竞对监控是 #166 定下的例外，见下一条
    for (const name of ['机会中心', '市场分析', '产品档案', '定价核算']) {
      const node = topLevel.find((r) => r.name === name)!;
      expect(node.routes).toBeUndefined();
    }
  });

  it('分组菜单各自 redirect 到第一个子屏，不停在空的父路由上', () => {
    expect(
      topLevel
        .filter((r) => r.routes)
        .map((r) => [r.path, r.routes!.find((c) => c.redirect)?.redirect]),
    ).toEqual([
      ['/keyword', '/keyword/growth'],
      ['/competitor', '/competitor/discovery'],
      ['/ip', '/ip/review'],
      ['/upload', '/upload/file'],
    ]);
  });
});

describe('竞对监控任务菜单（#166）', () => {
  it('只有商品发现、店铺管理两项，按任务分而不是按来源分', () => {
    const node = topLevel.find((r) => r.name === '竞对监控')!;
    expect(node.routes!.filter((c) => c.name).map((c) => [c.name, c.path])).toEqual([
      ['商品发现', '/competitor/discovery'],
      ['店铺管理', '/competitor/stores'],
    ]);
  });

  it('挂 canViewCompetitor，子屏继承不再各挂一个', () => {
    const node = topLevel.find((r) => r.name === '竞对监控')!;
    expect(node.access).toBe('canViewCompetitor');
    expect(node.routes!.every((c) => c.access === undefined)).toBe(true);
  });
});

describe('权限', () => {
  it('机会中心挂 canViewOpportunity——当前唯一默认不授予任何内置角色的权限点', () => {
    const node = topLevel.find((r) => r.name === '机会中心')!;
    expect(node.access).toBe('canViewOpportunity');
  });

  it('词库工具挂 canViewKeyword，两个子屏继承父级不再各挂一个', () => {
    const node = topLevel.find((r) => r.name === '词库工具')!;
    expect(node.access).toBe('canViewKeyword');
    expect(node.routes!.every((c) => c.access === undefined)).toBe(true);
  });

  it('产品档案、风险核查、定价核算是占位屏，挂 canViewUnreleased（仅超管）', () => {
    // 没有后端端点，所以挂的是占位屏守卫而不是权限点——权限矩阵里没有对应项，
    // 就不会有「勾了也不生效」的复选框。有了真实端点再按数据域加权限点。
    for (const name of ['产品档案', '风险核查', '定价核算']) {
      const node = topLevel.find((r) => r.name === name)!;
      expect(node.access).toBe('canViewUnreleased');
    }
  });

  it('风险核查的子屏继承父级，不再各挂一个', () => {
    const node = topLevel.find((r) => r.name === '风险核查')!;
    expect(node.routes!.every((c) => c.access === undefined)).toBe(true);
  });
});

describe('旧工具箱地址', () => {
  it('有真实数据的两屏留重定向，且都不进菜单', () => {
    const legacy = routes.filter((r) => r.path?.startsWith('/toolbox'));
    expect(legacy.map((r) => [r.path, r.redirect])).toEqual([
      ['/toolbox', '/keyword/growth'],
      ['/toolbox/growth', '/keyword/growth'],
      ['/toolbox/library', '/keyword/library'],
    ]);
    expect(legacy.every((r) => r.hideInMenu)).toBe(true);
  });

  it('两个占位屏的旧地址不留重定向', () => {
    // 没有人会去书签一个写着「数据源尚未接入」的屏，逐条保留只会让路由表越来越长
    const paths = routes.map((r) => r.path);
    expect(paths).not.toContain('/toolbox/patent');
    expect(paths).not.toContain('/toolbox/trademark');
  });
});
