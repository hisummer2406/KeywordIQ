---
paths:
  - "app/auth/**/*.py"
  - "app/user/**/*.py"
  - "frontend/src/app.tsx"
  - "frontend/src/hooks/useLoginRedirect.ts"
  - "frontend/src/pages/Login/**/*"
  - "frontend/src/pages/SsoCallback/**/*"
  - "frontend/src/services/**/*"
  - "frontend/src/access.ts"
  - "test/test*auth*.py"
  - "test/test*sso*.py"
  - "test/test*dingtalk*.py"
  - "test/test*rbac*.py"
  - "test/test*session*.py"
---

# 认证与会话实现约束

- 改动前读 [认证规格导航](../../openspec/guides/auth.md)；端点逐个挂服务端鉴权依赖，不把前端隐藏当授权。
- 用户管理只看 is_super；角色 admin 不等于超管，默认权限不能动态包含 SUPER_ONLY_PERMISSIONS。
- 本地/SSO 登录共用 rotate_active_session；返回本地产生的 session_id，不读 commit/refresh 后字段，防并发串号。
- 会话缺失/空/不匹配抛 SessionInvalidError，不用裸 HTTPException 丢失错误码区别。
- logout 用 get_current_user_for_logout 识别身份，清理保持条件更新；旧会话不能删新会话，也不能提前拒绝绕过条件更新。
- 所有失败后清本地令牌的入口在真正清理前比对请求令牌仍为当前令牌；比对后如有 await，清理前重判。
  覆盖 app.tsx、fetchUserInfo 和裸 fetch 导出，不能只修全局 handler。
- 跨域任一权限只用于不含私有数据的共用端点；读取另一域私有数据的筛选须在各端点按需叠加该域权限（来源 PR #66；原例 competitor_new 已于 #166 下架）。
- 钉钉只按外部身份和 unionId 匹配，禁止用户名自动合并/按可变 userid 绑定或从部门授超管。
- 企业/个人 token 分开，企业缓存并发加锁；SSO 无密码仍走哑哈希，不提前返回泄露账号类型。
- 保留本地密码逃生入口；不宣称离职登录反查能实时吊销已有 JWT，不擅自回写现有账号属性。
- Login/SsoCallback 复用 useLoginRedirect，先落 currentUser 再跳转，外域用 hardRedirect；回调页无 token 不挂普通 access。
- 关闭 SSO 仍注册路由并按规格返回 disabled；缺凭证/角色与库不可达的启动边界以规格为准。
- 回调域名、通讯录权限和真机扫码用 check_dingtalk_sso 验证，不用代码评审代替。
