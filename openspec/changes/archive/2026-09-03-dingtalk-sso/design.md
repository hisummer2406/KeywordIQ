## Context

现有鉴权是自建 JWT + RBAC：`UserCenter`（`user_center` 表）+ passlib bcrypt 本地密码 +
`AuthService`（PyJWT/HS256）+ `simple_auth` 的 `get_current_user` / `require_permission` /
`require_super`。JWT 只承载 `user_id / username / is_super / role`，权限码不入令牌、每请求
实时解析——因此**只要 SSO 能落到一条 `user_center` 记录上，下游鉴权一行都不必改**，
issue #36「不引入第二套权限判定路径」这条是天然满足的。

选型经过一次修正，记录在案以免后人重走：最初按 IDMesh（钉钉的企业身份平台）+ 标准 OIDC
设计，后因 IDMesh 的收费情况无法确认（官网 `idmesh.cn` 证书过期打不开）而改为直连钉钉
开放平台——企业内部应用的 OAuth2 登录与通讯录读取在钉钉免费版即可用，零额外费用。

**钉钉这套不是 OIDC，尽管 scope 叫 `openid`。** 它换回来的是 `accessToken`，没有 id_token、
没有 JWKS、没有 discovery 端点，因此不能用任何 OIDC 依赖方库直接接。并且
`GET /v1.0/contact/users/me` 只给**个人**身份（unionId、nick、头像），文档明写「无手机号和
企业相关信息」——要拿企业内 userid、姓名与在职状态，必须再用**企业** accessToken 调
`topapi/user/getbyunionid` 与 `topapi/v2/user/get`。所以本设计要处理**两套凭证**。

另需注意：钉钉文档中 `oapi.dingtalk.com/connect/oauth2/sns_authorize` + `sns/getuserinfo_bycode`
那一套已于 2021-12 迁入历史文档区，MUST NOT 采用。

## Goals / Non-Goals

**Goals:**

- 员工用钉钉扫码即可登录，不需要在本系统单独设密码
- 离职/移出通讯录的人自然失去登录能力，不引入定时任务
- SSO 成功后走的仍是现有那套 JWT 与 RBAC，鉴权判定路径不分叉
- 钉钉侧不可用时仍有逃生通道
- 外部身份源在任何情况下都不能带来超级管理员权限

**Non-Goals:**

- 钉钉客户端内的 H5 免登（本系统是桌面端宽表 BI 屏，扫码更贴合实际使用）
- 已有本地账号与钉钉身份的合并绑定（有意不做，见决策 3）
- 从钉钉部门/职位映射角色
- 定时全量核对通讯录
- 改用 httpOnly cookie 会话（波及每个受保护端点与每个前端请求，远超本变更范围）

## Decisions

### 决策 1：后端出授权 URL，SPA 承接回调，令牌走普通 JSON 响应

流程：

```
①  GET  /api/auth/sso/authorize-url        → { url }
②  前端 window.location.href = url          → 钉钉扫码页
③  钉钉 302 回 SPA 路由 /sso/callback?authCode=&state=
④  POST /api/auth/sso/callback {auth_code, state}
⑤  后端：验 state → userAccessToken → contact/users/me → 企业 accessToken
         → getbyunionid（查不到即拒登）→ v2/user/get → 找账号/建档 → 签 JWT
    ← { token, user }        ← 与 POST /api/auth/token 完全同形
⑥  前端 setToken → refresh() → 跳转        ← 现有代码原样复用
```

**备选 A：后端全托管重定向 + 一次性 ticket。** 钉钉直接回调到后端端点，后端签好 JWT 后
302 回前端。问题是 JWT 不能放进 URL（会进浏览器历史、Referer、日志），于是必须凭空引入
「一次性 ticket」这第二种凭证，连带签发、存储、过期、核销四件事。为了不把令牌放 URL 而造
一个新令牌，划不来。

**备选 B：改用 httpOnly cookie 会话。** 最抗 XSS，但要把 `HTTPBearer` 换成 cookie 并补
CSRF 防护，波及每一个受保护端点。见 Non-Goals。

选定方案的好处是**令牌交付路径完全没变**——还是一次普通 JSON 响应，前端的错误呈现与
跳转逻辑原样复用；client_id / scope 等全在后端配置，前端不持有任何 OAuth 知识，只负责
「跳过去」和「把 authCode 递回来」。代价是 nginx 需让 `/sso/callback` 走 SPA history
fallback——现有配置已经是这样，实际零改动。

### 决策 2：state 是无状态的短命签名令牌

`state` 为后端以 `ADMIN_SECRET_KEY` 签发的 JWT，含随机 nonce 与 ≤10 分钟的 `exp`，
不落任何存储。

`start.sh` 固定起 4 个 uvicorn worker，签发与校验落在同一进程纯属偶然。若把 state 存进程
内存，失败率大致是 3/4，且**时好时坏**——这种症状与「钉钉侧偶发故障」在现象上无从区分，
排查成本极高。存 Redis 要引入新组件，存数据库要为一个 10 分钟生命周期的对象建表并清理。
签名令牌三者都不要。

不上 PKCE：本应用是持有 client_secret 的 confidential client，而 PKCE 的 code_verifier
必须交给发起方（前端）保管，前端可读即失去意义。state + 一次性 authCode 已足够。

### 决策 3：账号只按 `(external_idp, external_id)` 匹配，`external_id` 存 unionId

**这是本功能唯一实质的攻击面。** 若按用户名自动绑定已有账号，任何能修改自己钉钉 userid
的人都能把它改成 `admin`，扫一次码就接管超级管理员。因此匹配 MUST NOT 使用任何可由身份源
一侧修改的字段。

`external_id` 选 unionId 而非企业内 userid：

| | 稳定性 | 选它的后果 |
|---|---|---|
| `unionId` | 钉钉账号级，长期不变 | ✅ |
| 企业内 `userid` | 管理员可改；离职再入职会换 | 变一次即老账号成孤儿、当事人被当新人重新开号、角色全丢 |

已知代价：同一个人的既有本地账号与钉钉身份**不会自动合并**，会并存两个账号。这是有意的。
将来若要合并，走管理员在后台发起的显式绑定操作，本次不做。

### 决策 4：`hashed_pwd` 放宽为可空，本地密码登录的拒绝由既有哑哈希路径承担

SSO 账号存 `hashed_pwd = NULL`，而不是塞一个「永不匹配」的哨兵串。哨兵串的正确性依赖
「bcrypt 不会碰巧匹配」这条隐含约定，NULL 不需要任何约定。

`user_crud.authenticate_user` 已有 `_DUMMY_PASSWORD_HASH`：查不到用户时也跑一遍 bcrypt，
使两条路径耗时同阶，堵住「用响应时间枚举有效用户名」的侧信道。`hashed_pwd IS NULL` 复用
同一条路径即可——**MUST NOT 新增提前返回的分支**，否则就在刚堵好的侧信道上重新开一个口，
泄露的是「哪些用户名是 SSO 账号」。

### 决策 5：离职同步靠登录时反查，不做定时任务

`topapi/user/getbyunionid` 查不到 unionId，即判定非本企业在职员工并拒登。这条查询本来就在
链路上（要拿 userid 才能取姓名），所以离职同步是**白送的**，不额外增加一次调用。

已知代价：离职前签发的 JWT 在过期前（当前 24 小时）仍然有效。需求方已接受。缩短令牌有效期
是独立决定，会连带影响本地账号与正在跑的导入，不在本变更内改。

### 决策 6：企业 accessToken 进程内缓存 + `asyncio.Lock`

有效期 7200 秒，提前 300 秒续期，每 worker 各缓存一份（4 worker × 每 2 小时 1 次 = 可忽略）。
换取过程加 `asyncio.Lock`，否则并发登录会各自换取并相互覆盖。

MUST NOT 落库：它是可随时重新换取的短命凭证，落库只是给自己增加一份需要保护的秘密。

### 决策 7：用户名规范化沿用管理员建号那一套

`UserCenterCreate` 的校验要求用户名 `isalnum()` 并转小写，而钉钉 userid 可能含 `-`/`_`。
SSO 建档不走那个 Pydantic 模型，本可以绕过——但那会让库里出现两套用户名规则。
因此 SSO 建档时显式套用同一套规范化（转小写、剥除非字母数字），冲突时追加数字后缀。

同时新增 `display_name` 列存钉钉姓名。不加的话界面上全是英文工号，实际很难用；而
`user_name` 现在同时承担「登录名」与「显示名」两个职责，拆开是迟早要做的，越晚越贵。

### 决策 8：登录不回写已建档账号的属性

属性只在建档那一次写入。回写会撞上 `user_name` 唯一约束（需处理冲突与重命名的连锁影响），
而收益仅是显示名跟随钉钉侧改名。issue #36 未要求，YAGNI。

### 数据库改动与 Alembic 迁移方案

一条手写迁移，作用于 `analysis` schema（`user_center` 已登记在 `migration_scope.py` 的
`V2_TABLES` 中，autogenerate 能看到这些列变更；但仍手工核对生成结果）：

```
op.add_column("user_center", external_idp  VARCHAR(32)  NULL, schema="analysis")
op.add_column("user_center", external_id   VARCHAR(128) NULL, schema="analysis")
op.add_column("user_center", display_name  VARCHAR(100) NULL, schema="analysis")
op.create_unique_constraint("uq_user_center_external", "user_center",
                            ["external_idp", "external_id"], schema="analysis")
op.alter_column("user_center", "hashed_pwd", nullable=True, schema="analysis")
```

三点注意：

1. **列注释参与 autogenerate 比对**，模型里的注释文本必须与迁移一字不差，否则会在
   autogenerate 输出里留下常驻假差异（历史上约 40 条纯注释差异曾把一条真差异整个盖住）
2. `user_center` 是普通表、无分区，不涉及分区表/物化视图那类 autogenerate 盲区
3. 回滚方向：`hashed_pwd` 收回 NOT NULL 前必须先删除或填充 SSO 账号，否则 downgrade 会失败。
   迁移的 `downgrade` 中 MUST 显式删除 `external_idp IS NOT NULL` 的行并说明这一点

**唯一约束在 NULL 上的语义**：PostgreSQL 默认 `NULLS DISTINCT`，本地账号两列均为 NULL
因而互不冲突，正是所需行为，不要改成 `NULLS NOT DISTINCT`。

### 对连接池、内存与执行计划的影响

- **连接池**：登录是短事务，异步引擎按请求取还，新增一条按 `(external_idp, external_id)`
  的点查。唯一约束自带索引，走索引唯一扫描。相对现有登录路径（一次用户名点查 + bcrypt）
  净增一次点查，可忽略
- **一个真实风险是线程池而非连接池**：本地密码登录的 bcrypt 走 `run_in_threadpool`
  （每次约 247 ms），共用 `THREAD_POOL_LIMIT`。SSO 路径**不跑 bcrypt**，因此 SSO 铺开后
  这项占用是**下降**的
- **对钉钉的 HTTP 调用必须用异步 `httpx`**，MUST NOT 在事件循环上做同步网络 IO
  （ruff 的 `ASYNC` 组会拦截）。单次登录对钉钉最多 4 次调用，其中企业 accessToken 那次命中缓存
- **内存**：无批量数据，可忽略
- **不触碰导入与查询路径**，事实表、分区、保留窗口一概不受影响

## Risks / Trade-offs

- **[离职后 JWT 残留最长 24 小时]** → 接受。需要压缩就调短令牌有效期，那是独立决定
- **[钉钉开放平台故障即无法 SSO 登录]** → 保留本地密码登录作逃生通道；失败呈现明确提示
  「可稍后重试或改用密码登录」；503 与业务 500 分开以便单独告警
- **[同一人可能同时拥有本地账号与 SSO 账号]** → 决策 3 的有意代价。用户管理屏增加「来源」
  列使其可见；管理员可停用其中一个
- **[回调域名配错，扫码后回不来]** → 无法靠 code review 发现，只能实测。提供
  `scripts/check_dingtalk_sso.py` 把整条链路跑通并逐项断言，对标 `scripts/check_oss.py`
- **[AppSecret 泄露即可冒充本应用换取用户身份]** → 只存服务端 `.env`，MUST NOT 出现在前端
  产物、响应体或日志中；`.env.example` 只留空值
- **[唯一超管的 `is_super` 被误改为 false 时界面上无自救入口]** → 这是既有风险，本变更
  未加剧（SSO 账号 `is_super` 恒假，不构成新的提权路径）；恢复手段仍是
  `scripts/seed_admin.py` 或直接改库

## Migration Plan

1. 钉钉开发者后台建企业内部应用，取得 AppKey/AppSecret，配置重定向 URL 与通讯录读取权限
2. 部署时 `SSO_ENABLED=false` 上线，先验证现有密码登录不受影响（新列全为 NULL，
   `hashed_pwd` 放宽不改变既有行为）
3. 跑 `scripts/check_dingtalk_sso.py` 在生产配置下实测整条链路
4. 置 `SSO_ENABLED=true`，登录页出现钉钉按钮
5. **回滚**：置 `SSO_ENABLED=false` 即可让入口消失，无需回滚数据库。数据库
   downgrade 只在确需时执行，且会删除全部 SSO 账号（见迁移方案第 3 点）

## Open Questions

- 无。IdP 选型、开号策略、本地登录去留、离职同步方式四项已确认，
  显示名与不回写两个取舍亦已确认。
