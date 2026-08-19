## Why

现在登录只有一条路：管理员在后台建号、员工各自记一套本系统专用的密码。人一多，这套账号
就开始漂移——新人入职要等管理员建号，离职的人账号留在库里没人记得停用，密码找回全靠找管理员。
公司已有钉钉作为统一身份来源，让员工用钉钉扫码进系统，既省掉这套并行维护的密码，也让
「谁还能登录」这件事跟着通讯录走。

**本变更与日报/周报两条数据路径都无关**，不触碰导入、查询、分区与保留逻辑，只改鉴权入口
与用户表。

## What Changes

- 新增钉钉扫码登录入口：登录页出现「钉钉登录」按钮，员工扫码后直接进入系统，不需要本系统密码
- 直连钉钉开放平台（企业内部应用的 OAuth2 授权码流程），**不经 IDMesh 等第三方身份中台**——
  开放平台的登录与通讯录能力在钉钉免费版即可用，零额外费用
- `user_center` 新增外部身份标识（`external_idp` / `external_id`）与显示名（`display_name`）
  三列；`hashed_pwd` 放宽为可空，用于表达「SSO 账号没有密码」这一事实
- 首次扫码自动建档，赋予可配置的默认角色（默认 `analyst`：能查询导出、不能导入）；
  `is_super` 恒为假，超管仍只能由 `scripts/seed_admin.py` 产生
- 离职同步走登录时校验：扫码后用 unionId 反查企业通讯录，查不到即拒登，不引入定时任务
- **保留本地密码登录**，但仅对本地账号有效；SSO 账号（`hashed_pwd IS NULL`）走密码登录必然失败。
  这是钉钉侧不可用时的逃生通道
- 用户管理屏增加「显示名」与「来源」（本地 / 钉钉）两列
- 新增 `scripts/check_dingtalk_sso.py`：把回调域名配置与整条换取链路跑一遍并逐项断言，
  覆盖 code review 无法确认的部分（对标 `scripts/check_oss.py`）

**非目标（本次不做）**：钉钉客户端内的 H5 免登；把已有本地账号与钉钉身份合并绑定；
从钉钉部门映射角色；定时全量核对通讯录。

## Capabilities

### New Capabilities

- `dingtalk-sso`: 钉钉扫码单点登录的完整契约——授权流程与端点形状、账号匹配与自动建档规则、
  离职判定、本地密码登录的边界、失败呈现，以及外部身份不得提权的安全边界

### Modified Capabilities

无。SSO 成功后签发的是同一套 JWT，下游 `require_permission` / `require_super` 的判定路径
一行不改，`admin-spa` 的「前端统一鉴权与失效处理」与 `api-error-contract` 的既有要求继续成立。

## Impact

**后端**

- `app/auth/`：新增 SSO 服务与路由（授权 URL 签发、回调换令牌、状态查询），复用现有
  `AuthService` 签发 JWT
- `app/user/user_model.py`、`user_crud.py`、`user_schemas.py`：新增三列与按外部身份查找/建档
- `app/common/errors.py`：新增 `sso.*` 错误码域
- `config.py` 与 `.env.example`：新增 `SSO_ENABLED` / `DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET`
  / `SSO_REDIRECT_URI` / `SSO_DEFAULT_ROLE_CODE`，并在开启时做启动期校验
- Alembic：一条迁移（加三列、加唯一约束、放宽 `hashed_pwd`）

**前端**

- `pages/Login/`：钉钉登录按钮（按后端 status 显隐）
- 新增 `pages/SsoCallback/` 与路由 `/sso/callback`（`layout: false`，不挂 access 守卫）
- 抽出 `useLoginRedirect`，供登录页与回调页共用（现有那段等待 `currentUser` 到位再跳转的
  竞态修复不能复制成两份）
- `pages/User/`：新增两列

**外部依赖**

- 钉钉开放平台：企业内部应用一个（AppKey/AppSecret），需配置重定向 URL 与通讯录读取权限
- 无新增 Python 包（`httpx`、`PyJWT` 均已在依赖内）
