> 只改权限码 `toolbox:view` → `keyword:view`（随「工具箱」容器退休而改名，见
> `top-nav-structure`）。其余约定一字未动，但 MODIFIED 是整条替换，故三条 Scenario
> 全部照录。

## MODIFIED Requirements

### Requirement: 榜单查询

系统 SHALL 提供按 (站点, 锚定日) 分页读取榜单的端点，遵循 ProTable 契约
`{data, total, success}` 并回显作用域。

- 每行 SHALL 携带：关键词、窗口首期排名、锚定期排名、累计净增名次、窗口内最低日增长率。
- 每行 SHALL 附带该窗口 7 期的排名走势与各点日期，供界面画小图。走势 SHALL 在分页之后
  对当页行批量补齐，MUST NOT 持久化为榜单表的冗余字段。
- 排序白名单 SHALL 限于累计净增名次、最低日增长率、锚定期排名三列，默认按累计净增降序。
  白名单之外的排序字段 SHALL 被拒绝。
- 分页总数 SHALL 由该切片的实际行数得出。
- 端点 SHALL 挂 `keyword:view` 权限依赖；未认证请求 SHALL 被拒绝。

#### Scenario: 读取一页榜单

- **WHEN** 已认证且具备 `keyword:view` 的用户请求站点 US、锚定日 2026-08-15 的第 1 页
- **THEN** 返回 `{data, total, success}`，`data` 按累计净增降序，每行含七点走势与其日期

#### Scenario: 未认证访问

- **WHEN** 不带令牌请求榜单端点
- **THEN** 返回 401，响应体是统一错误契约，不是 `success:false` 的 200

#### Scenario: 非法排序字段

- **WHEN** 请求按 `keyword` 排序
- **THEN** 按参数校验失败拒绝（422，与全站参数校验一致），不静默回退到默认排序
