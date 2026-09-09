## Why

现有 JWT 无会话唯一性校验，同一账号可在任意数量的浏览器/设备上同时持有有效令牌。issue #89
要求补一条规则：**每个账号只认可最后一次成功登录的会话，之前的会话全部失效**——本地密码
登录与钉钉扫码登录（#36）均适用。这是一个安全边界变更，不影响导入路径（日报/周报均不涉及）。

## What Changes

- `user_center` 新增可空字段 `active_session_id`（String(64)），记录当前有效会话编号。
- 登录成功（本地密码 / 钉钉扫码，两者共用同一段逻辑，不各自实现一遍）后：生成安全随机
  `session_id`（`secrets.token_urlsafe(32)`），写入 `active_session_id` 并提交，成功后才
  签发把 `session_id` 作为声明之一的 JWT；提交失败 MUST NOT 返回可用登录结果。
- `get_current_user` 在查用户的同时校验 JWT 的 `session_id` 与库中 `active_session_id`：
  缺失、空串、不匹配三种情况均拒绝鉴权，且返回**区别于**普通令牌失效的错误码，供前端呈现
  "账号已在其他浏览器登录"。
- 主动退出改为条件更新：仅当 `active_session_id` 仍等于本次令牌的 `session_id` 时才清空，
  避免旧会话的退出请求误删新会话。
- 并发登录的"最后一次"由数据库写入顺序（同一行的串行更新）定义，不依赖客户端时间或响应
  到达顺序。
- 前端：区分"会话被顶替"与其他 401，据此提示并清理登录态；处理延迟到达的旧请求 401 时，
  须确认该响应对应的仍是当前持有的令牌，不得清除同一浏览器后续登录已取得的新会话。
- **BREAKING**：上线后不兼容缺少 `session_id` 声明的旧 JWT，全体在线用户需重新登录一次。

## Capabilities

### New Capabilities
- `single-session-login`：账号级会话唯一性——登录时的会话生成与替换、鉴权时的会话校验、
  主动退出的条件清除、并发登录的串行化语义。

### Modified Capabilities
- `dingtalk-sso`：登录成功签发 JWT 那一步现在携带 `session_id` 声明并触发会话替换，
  "同一套 JWT、不引入第二套会话机制"这条既有约束延伸为"包含 SSO 在内只有一套会话状态"。
- `admin-spa`：「前端统一鉴权与失效处理」一节的 401 处理从单一分支（收到 401 即清凭证跳
  登录）拆成两支——会话被顶替（专属错误码，明确提示原因）与其他鉴权失败（沿用原提示）；
  且新增对延迟到达的旧请求响应的一致性校验，避免误清已经登录成功的新会话。

## Impact

- 后端：`app/user/user_model.py`（新增字段）、新增 Alembic 迁移、`app/user/user_crud.py`
  （落会话 id 与条件清除的 CRUD 方法）、`app/auth/login_auth.py`（`create_access_token` 签名
  变化，接收 `session_id`）、`app/auth/simple_auth.py`（`login`/`get_current_user`/新登出校验）、
  `app/auth/sso_service.py`（复用共用的会话签发逻辑）、`app/auth/auth_api.py`（`logout` 端点）、
  `app/common/errors.py`（新增错误码常量与 `APIError` 子类）。
- 前端：`frontend/src/app.tsx`（`errorHandler`、`doLogout`）、`frontend/src/utils/token.ts`（如需）、
  相关测试。
- 数据库：一条 Alembic 迁移（`user_center` 加列），不涉及分区表，`user_center` 已登记在
  `migration_scope.py` 的 `V2_TABLES`。
- 不涉及日报/周报导入路径。
- 文档：`CLAUDE.md` 中"JWT 无状态"相关表述需要更新为"JWT 携带会话编号，服务端做单会话校验"。
