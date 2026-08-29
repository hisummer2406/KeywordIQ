> 标题与正文里的「工具箱」「`toolbox:view`」在本变更后都不存在了。约束本身
> （竞对监控用独立权限点）完全不变，只是措辞不能再引用一个已退休的名字。

## RENAMED Requirements

- FROM: `### Requirement: 权限点独立于工具箱`
- TO: `### Requirement: 权限点独立，不与关键词工具共用`

## MODIFIED Requirements

### Requirement: 权限点独立，不与关键词工具共用

竞对监控功能 SHALL 使用独立权限点 `competitor:view`，MUST NOT 复用 `keyword:view`
或任何其他一级菜单的权限点——每个用户的关注池互相独立，不是「全体共用一个开关」的
授权粒度。

`competitor:view` SHALL 授予内置角色 admin、operator、analyst。

#### Scenario: 未授权用户访问

- **WHEN** 一个只有 `analysis:view` 权限的用户请求竞对监控的任意端点
- **THEN** 返回 403
