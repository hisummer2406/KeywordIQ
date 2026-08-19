## ADDED Requirements

### Requirement: 钉钉扫码登录直连开放平台

系统 SHALL 通过钉钉开放平台的企业内部应用 OAuth2 授权码流程实现浏览器扫码登录，
MUST NOT 依赖 IDMesh 等第三方身份中台。授权页地址为
`https://login.dingtalk.com/oauth2/auth`，回调参数名为 `authCode`（非 `code`）。

登录成功后 SHALL 由现有 `AuthService` 签发**同一套 JWT**，响应体形状与
`POST /api/auth/token` 完全一致（`{token, user}`）。MUST NOT 为 SSO 引入第二套令牌、
第二套会话机制或第二条权限判定路径。

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

#### Scenario: 授权被拒绝

- **WHEN** 钉钉回调至 `/sso/callback?error=<reason>`
- **THEN** 前端 MUST NOT 调用换取端点，直接呈现失败并提供回到登录页的入口

### Requirement: state 为无状态短命凭证

`state` SHALL 是后端签发的短命签名令牌（含随机 nonce 与不超过 10 分钟的过期时间，
以 `ADMIN_SECRET_KEY` 签名），MUST NOT 依赖任何进程内存或共享存储来校验。

理由是 `start.sh` 固定起 4 个 uvicorn worker，签发 `state` 与校验 `state` 的两次请求
落在同一进程纯属偶然；存进程内存会产生**时好时坏**的登录失败，且症状与钉钉侧故障无从区分。

#### Scenario: 跨进程校验成立

- **WHEN** 签发 `state` 的请求与回传 `state` 的请求由不同 worker 处理
- **THEN** 校验照常通过，登录成功

#### Scenario: state 过期或被篡改

- **WHEN** 回传的 `state` 签名不符或已过期
- **THEN** 返回 400、错误码 `sso.state_invalid`，message 为「登录已超时，请重新发起」

### Requirement: 账号匹配只认外部身份标识

系统 SHALL 只按 `(external_idp, external_id)` 定位 SSO 账号，其中 `external_id` 为钉钉
`unionId`。MUST NOT 按用户名、姓名、手机号或任何可由身份源一侧修改的字段自动绑定到已有账号。

这是本功能唯一实质的攻击面：若按用户名自动绑定，任何能修改自己钉钉 userid 的人都可以把它
改成 `admin` 从而接管超级管理员账号。

`external_id` 选用 `unionId` 而非企业内 `userid`，因为后者可被管理员修改、且员工离职再入职
会变——一旦变化，原账号成为孤儿、当事人被当作新人重新开号，已配置的角色全部丢失。

#### Scenario: 同一人再次扫码复用同一账号

- **WHEN** 某 unionId 已建过档，该员工再次扫码登录
- **THEN** 命中同一条 `user_center` 记录，MUST NOT 新建账号

#### Scenario: 企业内 userid 变更不影响账号归属

- **WHEN** 某员工的钉钉 userid 被管理员改动，unionId 不变
- **THEN** 仍命中原有账号，角色与状态保持不变

#### Scenario: 用户名相同不构成绑定

- **WHEN** 扫码用户的钉钉 userid 与某个已有**本地**账号的用户名相同
- **THEN** MUST NOT 登录进该本地账号；系统另建 SSO 账号，并对用户名追加数字后缀以满足唯一约束

### Requirement: 首次扫码自动建档且不得提权

首次扫码的钉钉员工 SHALL 被自动建档，字段取值如下：

- `user_name`：由钉钉企业内 userid 规范化得到（转小写、剥除非字母数字字符），
  与已有用户名冲突时追加数字后缀。规范化规则 SHALL 与管理员建号所用的用户名规则一致，
  MUST NOT 在库中形成两套用户名规则
- `display_name`：钉钉侧姓名
- `hashed_pwd`：NULL
- `is_active`：true
- `is_super`：**恒为 false，无例外**
- `role_id`：配置项 `SSO_DEFAULT_ROLE_CODE` 对应的角色（默认 `analyst`）
- `external_idp` / `external_id`：`'dingtalk'` / unionId

`is_super` 恒假是硬约束：用户管理是不可授予的能力（由 `require_super` 把守），
MUST NOT 由外部身份源决定谁拥有它。超级管理员仍只能由 `scripts/seed_admin.py` 产生。

#### Scenario: 新员工首次登录

- **WHEN** 一个从未登录过的在职钉钉员工扫码成功
- **THEN** 建立账号并赋予默认角色，登录成功
- **AND** 该账号的 `is_super` 为 false

#### Scenario: 身份源不能带来超管权限

- **WHEN** 钉钉侧返回的任何字段（角色、部门、职位、userid）暗示管理员身份
- **THEN** 建档结果的 `is_super` 仍为 false

### Requirement: 登录时校验在职状态，不做属性回写

离职同步 SHALL 由登录时的通讯录反查承担：以 unionId 调用 `topapi/user/getbyunionid`，
查不到即判定为非本企业在职员工并拒登。MUST NOT 为此引入定时任务或后台作业。

已知代价 SHALL 被接受：离职前已签发的 JWT 在过期前仍然有效（当前 24 小时）。

系统 MUST NOT 在每次登录时回写已建档账号的属性（用户名、显示名、角色）。属性只在建档那一次
写入。回写会与用户名唯一约束冲突，而收益仅是显示名跟随钉钉侧改名。

#### Scenario: 离职员工被拒

- **WHEN** 扫码用户的 unionId 在企业通讯录中查不到
- **THEN** 返回 403、错误码 `sso.not_employee`，
  message 为「你的钉钉账号不在本企业通讯录内，请联系管理员」

#### Scenario: 本系统内被停用的账号被拒

- **WHEN** 扫码成功但对应账号的 `is_active` 为 false
- **THEN** 返回 403、错误码 `sso.account_disabled`，且 MUST NOT 签发令牌

#### Scenario: 钉钉侧改名不改动本地记录

- **WHEN** 某已建档员工在钉钉侧改了姓名后再次登录
- **THEN** 登录成功，本地 `display_name` 保持建档时的值

### Requirement: 本地密码登录保留但仅对本地账号有效

`POST /api/auth/token` SHALL 继续可用，作为钉钉侧不可用时的逃生通道。SSO 账号
（`hashed_pwd IS NULL`）走该端点 MUST 失败。

该判定 SHALL 复用 `authenticate_user` 既有的哑哈希路径（空密码散列走与不存在用户相同的
固定哈希校验），MUST NOT 新增提前返回的分支——提前返回会让「该用户名是 SSO 账号」
通过响应耗时泄露出去，正是既有哑哈希机制要堵的那条侧信道。

#### Scenario: SSO 账号无法用密码登录

- **WHEN** 以某 SSO 账号的用户名与任意密码调用 `POST /api/auth/token`
- **THEN** 返回 401
- **AND** 该请求的耗时与正常密码校验同阶

#### Scenario: 本地账号照常登录

- **WHEN** 以 `scripts/seed_admin.py` 建立的本地账号与正确密码登录
- **THEN** 登录成功，不受 SSO 是否启用影响

### Requirement: SSO 未启用时不暴露入口

`SSO_ENABLED=false` 时，登录页 MUST NOT 显示钉钉登录按钮。前端 SHALL 通过免鉴权的
`GET /api/auth/sso/status` 取得 `{enabled, provider_name}` 决定显隐，MUST NOT 靠
「请求授权地址拿到失败响应」来推断——那会让每次进入登录页都发出一个注定失败的请求。

SSO 路由 SHALL **始终注册**，关闭时由端点自身答复 `sso.disabled`（404）。按开关条件注册
路由并不能让它变得可热切换（配置是启动期读入的），只会让这段行为无法被测试覆盖。

`SSO_ENABLED=true` 时，`DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`SSO_REDIRECT_URI`
缺任一项 SHALL 导致**启动失败**（配置层判定）；`SSO_DEFAULT_ROLE_CODE` 不存在于角色表
同样 SHALL 导致启动失败（启动期判定，需连库）。配了却不生效的 SSO 开关，其症状是员工
扫完码回来看到一个说不清的错误。

但角色检查 MUST NOT 因**数据库不可达**而中断启动——那种情况由既有的连接检查按记日志
处理。为一个配置校验让应用在数据库抖动时起不来，比这个配置错本身更糟。

#### Scenario: 关闭时登录页只有密码表单

- **WHEN** `SSO_ENABLED=false`，用户打开登录页
- **THEN** 页面不出现钉钉登录按钮，且不发出授权地址请求

#### Scenario: 关闭时直接调用端点得到明确答复

- **WHEN** `SSO_ENABLED=false`，直接请求 `GET /api/auth/sso/authorize-url`
- **THEN** 返回 404、错误码 `sso.disabled`，而不是「碰巧没有这条路由」的通用 404

#### Scenario: 开启但配置不全则拒绝启动

- **WHEN** `SSO_ENABLED=true` 而 `DINGTALK_APP_SECRET` 为空
- **THEN** 应用启动失败并指明缺失的配置项

### Requirement: 企业访问凭证缓存且并发安全

调用通讯录接口所需的企业 accessToken SHALL 在进程内缓存至过期前（有效期 7200 秒，
提前不少于 300 秒续期），MUST NOT 每次登录都重新换取。换取过程 SHALL 有并发保护，
避免同时到达的多个登录各自换取并相互覆盖。

该凭证 MUST NOT 落库，也 MUST NOT 出现在任何响应体中。

#### Scenario: 并发登录只换取一次

- **WHEN** 缓存为空时多个登录请求同时到达
- **THEN** 只发起一次企业 accessToken 换取，其余等待复用同一结果

### Requirement: SSO 失败遵循统一错误契约

所有 SSO 失败 SHALL 走 `app/common/errors.py` 的 `APIError` 子类由服务层**抛出**，
API 层 MUST NOT 做 try/except 转译。响应体形状为既有的
`{success:false, code, message, request_id}`。

钉钉返回的 `errcode`/`errmsg`、unionId、AppKey 等内部细节 SHALL 只经 `log_detail` 进日志，
MUST NOT 出现在响应体中。

错误码与状态码对应关系：

| code | 状态码 | 含义 |
|---|---|---|
| `sso.state_invalid` | 400 | state 过期或不合法 |
| `sso.code_invalid` | 400 | 授权码已使用或已失效 |
| `sso.not_employee` | 403 | 不在本企业通讯录内 |
| `sso.account_disabled` | 403 | 账号已被停用 |
| `sso.provider_unavailable` | 503 | 钉钉服务不可达或超时 |
| `sso.disabled` | 404 | 本部署未开启 SSO（端点被直接调用）|

`sso.provider_unavailable` 单独用 503 而非 500，是为了让「钉钉挂了」与「我们的代码炸了」
能分别设定告警策略，与既有的数据库不可用同属基础设施类。

#### Scenario: 钉钉接口不可达

- **WHEN** 换取用户凭证时对钉钉的请求超时
- **THEN** 返回 503、错误码 `sso.provider_unavailable`，
  message 提示可稍后重试或改用密码登录
- **AND** 具体的异常与钉钉 errcode 只出现在日志中

#### Scenario: 授权码重复使用

- **WHEN** 同一 `authCode` 被第二次提交
- **THEN** 返回 400、错误码 `sso.code_invalid`

### Requirement: 用户管理屏呈现账号来源

用户管理列表 SHALL 展示每个账号的「显示名」与「来源」（本地 / 钉钉），
使管理员能分辨哪些账号由 SSO 自动建立、哪些是本地账号。

用户管理端点的鉴权 MUST 保持不变（一律 `require_super`，只看 `is_super`、不看角色码）。

#### Scenario: 列表区分账号来源

- **WHEN** 超级管理员打开用户管理屏
- **THEN** 每行显示该账号的显示名与来源
- **AND** SSO 建立的账号来源显示为「钉钉」
