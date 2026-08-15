# toolbox-navigation Specification

## Purpose
定义「工具箱」菜单容器：父子菜单形态与父路由重定向、`toolbox:view` 这一个共用权限点的
授予与执行方式，以及尚未接入数据源的二级屏应有的行为（明确的占位说明、不发请求、不建端点）。

## Requirements
### Requirement: 工具箱菜单容器

界面 SHALL 提供「工具箱」一级菜单，下挂持续增长市场、外观专利、全球商标库三个二级项。
菜单形态 SHALL 沿用既有的父子菜单约定（与「数据上传」一致），不引入新的布局模式。

访问 `/toolbox` SHALL 重定向到持续增长市场，不停留在一个没有内容的父路由上。

#### Scenario: 有权限的用户查看菜单

- **WHEN** 具备 `toolbox:view` 的用户登录
- **THEN** 顶栏出现「工具箱」，展开后可见三个二级项

#### Scenario: 访问父路由

- **WHEN** 用户直接访问 `/toolbox`
- **THEN** 重定向到 `/toolbox/growth`

### Requirement: 工具箱权限点

系统 SHALL 新增可授予的权限点 `toolbox:view`，作为工具箱下全部屏幕的唯一权限判据，
并授予 admin / operator / analyst 三个内置角色。

系统 MUST NOT 为尚未接入数据源的外观专利、全球商标库各自新增权限点——没有端点可挂的
权限点是「勾了也不生效」的复选框，与已被移除的 `user:manage` 同类。

前端菜单显隐 SHALL 由该权限点驱动；菜单隐藏 MUST NOT 被当作访问控制，后端端点 SHALL
各自挂权限依赖。

#### Scenario: 无权限用户

- **WHEN** 不具备 `toolbox:view` 的用户登录
- **THEN** 顶栏不出现「工具箱」；直接访问 `/toolbox/growth` 被路由守卫挡回；
  直接请求榜单端点返回 403

#### Scenario: 内置角色

- **WHEN** 迁移应用到已有数据库
- **THEN** admin / operator / analyst 三个角色都获得 `toolbox:view`，无需人工补授

### Requirement: 未接入数据源的占位屏

外观专利与全球商标库 SHALL 各自渲染一个明确说明「数据源尚未接入」的占位屏。占位屏
MUST NOT 发起任何后端请求，系统 MUST NOT 为它们新增端点。

#### Scenario: 打开占位屏

- **WHEN** 用户点击「外观专利」
- **THEN** 页面显示空状态与「数据源尚未接入」的说明，网络面板中没有该屏发出的请求
