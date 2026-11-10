## REMOVED Requirements

### Requirement: 风险核查菜单容器不设权限点

**Reason**: 外观专利核查与核查记录接入了真实后端端点（#24），按原条款「有了真实端点再新增权限点」，菜单改由 `risk:view` 把守。原条款描述的「无权限点、`/ip` 重定向到外观专利」与当时代码（`canViewUnreleased`、重定向到 `/ip/review`）也已不一致。

**Migration**: 由本变更新增的「风险核查菜单由 risk:view 把守」取代；权限点见 `patent-risk-check` 规格。

## ADDED Requirements

### Requirement: 风险核查菜单由 risk:view 把守

界面 SHALL 提供「风险核查」一级菜单（`/ip`），按任务组织，左侧二级菜单依次为：外观专利核查（`/ip/patent`，新建核查）、核查记录（`/ip/records`，列表）。访问 `/ip` SHALL 重定向到外观专利核查。核查详情 `/ip/records/:id` 是不出现在菜单中的独立路由。旧地址 `/ip/review` 与 `/ip/review/:id` SHALL 分别重定向到 `/ip/records` 与 `/ip/records/:id`（带上记录 ID），且不出现在菜单中。

一级菜单 SHALL 由 `risk:view` 把守，二级菜单继承，不另挂权限点。没有数据源的功能（商标查询、风险案例）MUST NOT 以空占位屏出现在菜单里（用户 2026-11-11 决定），真正接入时再加入菜单。

#### Scenario: 运营被授予 risk:view

- **WHEN** 一个拥有 `risk:view` 的非超管用户登录
- **THEN** 顶栏出现「风险核查」，左侧依次是外观专利核查、核查记录

#### Scenario: 没有 risk:view

- **WHEN** 一个只有 `analysis:view` 的用户登录
- **THEN** 顶栏不出现「风险核查」，直接访问 `/ip/patent` 显示无权限

#### Scenario: 超管

- **WHEN** 超管登录
- **THEN** 左侧同样只有外观专利核查、核查记录两项，没有商标查询、风险案例占位

#### Scenario: 旧收藏链接

- **WHEN** 用户打开 #179 上线时收藏的 `/ip/review/42`
- **THEN** 跳转到 `/ip/records/42`，看到同一条核查记录
