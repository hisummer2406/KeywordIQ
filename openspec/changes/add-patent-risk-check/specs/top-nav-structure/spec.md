## REMOVED Requirements

### Requirement: 风险核查菜单容器不设权限点

**Reason**: 外观专利和审核待办接入了真实后端端点（#24），按原条款「有了真实端点再新增权限点」，菜单改由 `risk:view` 把守。原条款描述的「无权限点、`/ip` 重定向到外观专利」与当时代码（`canViewUnreleased`、重定向到审核待办）也已不一致。

**Migration**: 由本变更新增的「风险核查菜单由 risk:view 把守」取代；权限点见 `patent-risk-check` 规格。

## ADDED Requirements

### Requirement: 风险核查菜单由 risk:view 把守

界面 SHALL 提供「风险核查」一级菜单（`/ip`），左侧二级菜单依次为：审核待办（`/ip/review`）、外观专利（`/ip/patent`）、商标查询（`/ip/trademark`）、风险案例（`/ip/cases`）。访问 `/ip` SHALL 重定向到审核待办。核查详情 `/ip/review/:id` 是不出现在菜单中的独立路由。

一级菜单 SHALL 由 `risk:view` 把守。商标查询与风险案例仍是占位屏，SHALL 只对 `is_super` 显示（沿用占位屏守卫），MUST NOT 因父菜单开放而让普通用户以为已经接入。

#### Scenario: 运营被授予 risk:view

- **WHEN** 一个拥有 `risk:view` 的非超管用户登录
- **THEN** 顶栏出现「风险核查」，左侧可见审核待办和外观专利，看不到商标查询、风险案例

#### Scenario: 没有 risk:view

- **WHEN** 一个只有 `analysis:view` 的用户登录
- **THEN** 顶栏不出现「风险核查」，直接访问 `/ip/patent` 显示无权限

#### Scenario: 超管

- **WHEN** 超管登录
- **THEN** 四个二级菜单全部可见，商标查询与风险案例仍显示「数据源尚未接入」
