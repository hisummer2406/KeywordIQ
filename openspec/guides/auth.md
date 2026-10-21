# 认证、权限与钉钉登录

本指南是根目录 [CLAUDE.md](../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。
文中代码与命令路径均相对仓库根目录；主文件规定跨域红线，本指南保留该域的细则、理由和历史证据。
历史测量、上线状态与人工验证记录只证明记录时状态，不替代本次验证。

### Authentication & Authorization

- **自建 JWT + RBAC**（`fastapi-user-auth`/`fastapi-amis-admin` 已随 amis 于阶段七移除）。
  用户模型 `UserCenter`（`user_center` 表）、`passlib` bcrypt 密码、`AuthService`（PyJWT/HS256/
  `ADMIN_SECRET_KEY`）、`simple_auth`（登录、`get_current_user`、`require_permission`、
  `require_super`）。
- **RBAC**：`role`/`permission`/`role_permission`（M2M），`user_center.role_id`（每用户一角色）。
  权限点有六个：`analysis:view`/`analysis:export`/`upload:import`/`keyword:view`/
  `competitor:view`/`opportunity:view`；内置角色
  admin/operator/analyst（见 `app/auth/permissions.py`）。逐路由 `require_permission(code)` 授权，
  is_super 兜底。前端在 `access.ts` 按权限码显隐菜单。**`opportunity:view` 是例外**：默认不
  授予任何内置角色（含 admin），只有 `is_super=true` 能用，`permissions.py` 的
  `SUPER_ONLY_PERMISSIONS` 集合把它排除在"admin 隐含全部权限"这条默认规则之外
  （见 [前端指南](frontend.md) 的顶栏菜单部分）。
- **用户管理是「不可授予」的能力，不走权限点**：`/api/user/*` 五个端点一律
  `Depends(simple_auth.require_super())`，判据只看 `is_super`，**不看角色码**。前端入口在
  右上角头像下拉里（`components/RightContent`），顶栏不出现（`/user` 路由 `hideInMenu`，
  但 access 守卫照常生效——菜单隐藏不是访问控制）。

  两条历史教训写在这里，别再走回去：**（1）** 曾有 `user:manage` 权限点，收紧后它成了
  「勾了也不生效」的复选框——管理员以为把它授予了运营，运营却仍然 403；可授予但无效的
  权限点比没有更糟，故连同从未被任何代码读过的 `role:manage` 一并从矩阵中删除
  （迁移 `d0e1f2a3b4c5`）。**（2）** 曾有一个 `get_super_user`，判据是「is_super **或**
  role_code == 'admin'」——那等于让「谁能管用户」变成改角色配置就能改变的事，已被
  `require_super` 取代（它声明后从未被使用）。回归在 `test/test_rbac_auth_unit.py`：
  admin 角色 + 全部权限点、只要 `is_super` 为假就必须 403。

  代价是**能管用户的唯一判据就是 `is_super`**：若唯一超管的该位被误改为 false，界面上
  没有自救入口，只能靠 `scripts/seed_admin.py` 或直接改库恢复。
- **端点**：`POST /api/auth/token`（登录发令牌，免鉴权）、`GET /api/auth/me`、
  `POST /api/auth/logout`（干净 REST）。
- **账号级会话唯一（issue #89）：JWT 不再是纯粹无状态的。** 每次登录成功（本地密码/钉钉
  扫码共用同一段逻辑，`UserCenterCRUD.rotate_active_session`）生成新的 `session_id`，覆盖
  `user_center.active_session_id` 并编码进新签发的 JWT；`get_current_user` 在查用户之外
  额外比对这一项，缺失、为空串或与库中不一致均判定为会话失效，抛
  `SessionInvalidError`（`auth.session_invalid`，与其余 401 场景的 `auth.unauthorized`
  区分，供前端呈现"账号已在其他浏览器登录"）。主动退出（`clear_session_if_current`）是
  条件更新——只清与本次令牌一致的记录，旧会话的退出请求不会误删更新的登录。**`/logout`
  的鉴权依赖是专门的 `get_current_user_for_logout`，不是 `get_current_user`**——后者会
  在会话已不匹配时提前抛 `SessionInvalidError`，条件更新永远执行不到，只在同一请求内部
  两次数据库往返之间的极窄窗口才真正生效（PR #91 复审第六轮）。并发登录的
  "最后一次"由数据库对同一行的写入天然串行化决定，不额外加锁。**上线时不兼容缺少
  `session_id` 的旧令牌**，全体在线用户需重新登录一次；这是刻意的 BREAKING 行为，不做
  兼容分支。前端 `errorHandler`（`frontend/src/app.tsx`）按错误码区分提示文案，并在清理前
  比对这次失败请求发起时用的令牌是否仍是当前令牌——不一致（本浏览器已经历过重新登录）时
  忽略这个迟到的响应，不清理刚取得的新会话。详见
  `openspec/changes/archive/2026-09-11-add-single-session-login/design.md`。
- **鉴权显式落在每个端点的依赖上**（`Depends(require_permission(code))` 或 `get_current_user`），
  没有全局鉴权中间件——旧的 `AdminAuthMiddleware` 因 `exclude_paths` 含 `"/"` 形同虚设，已删除。
  新增受保护端点务必挂鉴权依赖（无全局兜底）。`test/test_protable_contract.py` 有"无 token 必拒"回归。
- 初始管理员：`scripts/seed_admin.py`（默认 `admin / pwd123`；生产改 `ADMIN_SECRET_KEY`）。

**钉钉单点登录（`/api/auth/sso/*`，全部免鉴权）**：员工扫码即可登录，不必在本系统单设
密码。走**钉钉开放平台的企业内部应用**，OAuth2 授权码流程 + 浏览器扫码；**不经 IDMesh
等身份中台**（开放平台的登录与通讯录能力在钉钉免费版即可用，零额外费用）。六条要点：

- **这套不是 OIDC，尽管授权参数里的 scope 叫 `openid`。** 钉钉换回来的是 accessToken，
  没有 id_token、没有 JWKS、没有 discovery 端点——**接不上任何 OIDC 依赖方库**。而且
  `/v1.0/contact/users/me` 只给个人身份（unionId、nick，文档明写「无手机号和企业相关
  信息」），拿企业内 userid、姓名与在职状态要**另一套企业 accessToken** 调通讯录接口。
  所以 `dingtalk_client.py` 里是**两套凭证**，企业那套带进程内缓存 + `asyncio.Lock`
  （没有锁，并发登录各换各的、互相覆盖，钉钉侧还会限流，表现为随机登录失败）。
  另注：`oapi.dingtalk.com/connect/oauth2/sns_authorize` + `sns/getuserinfo_bycode`
  那一套已于 2021-12 迁入钉钉的历史文档区，**不要采用**。
- **账号只按 `(external_idp, external_id)` 匹配，`external_id` 存 unionId。**
  这是本功能唯一实质的攻击面：若按用户名自动绑定已有账号，任何能改自己钉钉 userid 的人
  都能把它改成 `admin`，扫一次码就接管超管。用户名撞了只说明重名、**不说明是同一个人**，
  撞了就加数字后缀。存 unionId 而非企业内 userid，是因为后者管理员可改、离职再入职会换
  ——变一次即老账号成孤儿、当事人被当新人重新开号、角色全丢，且不会有任何报错。
  代价是同一个人的本地账号与钉钉身份**不会自动合并**，会并存两个账号；这是有意的。
- **`is_super` 恒假，无例外。** 用户管理是不可授予的能力（`require_super` 把守），
  不能由外部身份源决定谁拥有它。超管仍只能由 `scripts/seed_admin.py` 产生。
  首次扫码自动建档，角色取 `SSO_DEFAULT_ROLE_CODE`（默认 `analyst`），**不从钉钉部门/
  职位映射**——那会让「谁能导入数据」取决于通讯录怎么编排。**登录不回写已建档账号的
  属性**（回写要处理用户名唯一约束的连锁影响，收益只是显示名跟随改名）。
- **离职同步靠登录时反查，没有定时任务**：`topapi/user/getbyunionid` 查不到 unionId 即
  拒登。这条查询本来就在链路上（要拿 userid 才能取姓名），所以离职同步是白送的。
  已知代价：**离职前签发的 JWT 在过期前仍然有效**（当前 24 小时）。
- **本地密码登录保留，但只对本地账号有效。** SSO 账号 `hashed_pwd` 存 NULL，
  `authenticate_user` 复用既有的 `_DUMMY_PASSWORD_HASH` 路径把它挡掉——**不要新增
  「`hashed_pwd is None` 就提前返回」的分支**：提前返回把这条路径压到毫秒级，
  「这个用户名是不是 SSO 账号」就能靠响应耗时逐个试出来，正是那个哑哈希当初要堵的侧信道。
  保留它是钉钉侧不可用时的唯一逃生通道。
- **`SSO_ENABLED=false` 时端点一律 404（`sso.disabled`），路由仍然注册。** 按开关条件
  注册路由并不能让它变得可热切换（配置是启动期读入的），只会让这段行为测不到。前端靠
  免鉴权的 `GET /sso/status` 决定要不要渲染钉钉按钮——不这么做，每次进登录页都要发一个
  注定失败的请求。开启时凭证缺任一项**启动失败**；`SSO_DEFAULT_ROLE_CODE` 不在角色表里
  同样启动失败（启动期检查，需连库），但**数据库不可达不中断启动**——为一个配置校验让
  应用在数据库抖动时起不来，比这个配置错本身更糟。

前端入口在登录页（`pages/Login`）与过场屏 `pages/SsoCallback`（路由 `/sso/callback`，
`layout: false`、**不挂 access 守卫**——此刻还没有令牌）。登录跳转那段等 `currentUser`
落位再 push 的竞态修复抽在 `hooks/useLoginRedirect.ts`，**两屏共用一份**：它的正确性全在
那段注释的推理里，复制出去的副本会各自漂移，而出错表现是偶发的闪回登录页或 403。
外域跳转走 `utils/redirect.ts` 的 `hardRedirect` 而非 `history.push`（后者在自己的路由表
里找不到钉钉地址，会落到 404 屏）。

**回调域名配置与真机扫码无法靠 code review 确认**，用 `uv run python scripts/check_dingtalk_sso.py`
实测——它按真实登录顺序跑一遍并逐项断言，退出码非 0 即有项未通过。两类问题只有它能发现：
重定向 URL 与开发者后台登记的不一致（表现是扫码后停在钉钉页面，本系统这边**一条日志都
没有**），以及应用缺通讯录读权限（前面几步全过、直到反查 userid 才失败，而那个失败长得
跟「这个人离职了」一模一样）。
