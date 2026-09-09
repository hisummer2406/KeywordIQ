## MODIFIED Requirements

### Requirement: 钉钉扫码登录直连开放平台

系统 SHALL 通过钉钉开放平台的企业内部应用 OAuth2 授权码流程实现浏览器扫码登录，
MUST NOT 依赖 IDMesh 等第三方身份中台。授权页地址为
`https://login.dingtalk.com/oauth2/auth`，回调参数名为 `authCode`（非 `code`）。

登录成功后 SHALL 由现有 `AuthService` 签发**同一套 JWT**，响应体形状与
`POST /api/auth/token` 完全一致（`{token, user}`）。MUST NOT 为 SSO 引入第二套令牌、
第二套会话机制或第二条权限判定路径——包括账号级会话唯一性校验（见
`single-session-login`）：钉钉扫码登录 SHALL 与本地密码登录共用同一段"生成会话编号→
落库→签发"逻辑，MUST NOT 另行实现一套会话签发。

#### Scenario: 前端取得授权地址

- **WHEN** 未登录用户点击「钉钉登录」，前端请求 `GET /api/auth/sso/authorize-url`
- **THEN** 响应体含 `url`，其 query 至少包含 `client_id`、`redirect_uri`、
  `response_type=code`、`scope=openid`、`prompt=consent`、`state`
- **AND** `redirect_uri` 取自配置 `SSO_REDIRECT_URI`，MUST NOT 由请求方传入

#### Scenario: 回调换取本系统令牌

- **WHEN** 钉钉回调至 `/sso/callback?authCode=<code>&state=<state>`，前端以
  `POST /api/auth/sso/callback {auth_code, state}` 回传
- **THEN** 后端依次调用 `oauth2/userAccessToken`、`contact/users/me`、
  `topapi/user/getbyunionid`、`topapi/v2/user/get`，成功后返回 `{token, user}`
- **AND** 该响应体的形状与 `POST /api/auth/token` 一致，前端复用同一套令牌落地与跳转逻辑
- **AND** 该令牌携带新生成的会话编号，并已替换该账号此前的有效会话（见
  `single-session-login`「登录成功即生成新会话并替换旧会话」）

#### Scenario: 授权被拒绝

- **WHEN** 钉钉回调至 `/sso/callback?error=<reason>`
- **THEN** 前端 MUST NOT 调用换取端点，直接呈现失败并提供回到登录页的入口
