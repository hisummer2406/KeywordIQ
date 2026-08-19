## 1. 配置与数据模型

- [ ] 1.1 `config.py` 新增 `SSO_ENABLED` / `DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET` /
      `SSO_REDIRECT_URI` / `SSO_DEFAULT_ROLE_CODE`，并写 `model_validator`：
      开启时前三项缺一即启动失败
- [ ] 1.2 `.env.example` 同步这五项，含注释、单位与安全的本地默认值（`SSO_ENABLED=false`，
      密钥留空）
- [ ] 1.3 `user_model.py` 新增 `external_idp` / `external_id` / `display_name` 三列并放宽
      `hashed_pwd` 为可空；列注释文本一次写定（后续迁移必须逐字一致）
- [ ] 1.4 手写 Alembic 迁移：加三列、加唯一约束 `uq_user_center_external`、
      `alter_column` 放宽 `hashed_pwd`；`downgrade` 中显式删除 `external_idp IS NOT NULL`
      的行并注明原因
- [ ] 1.5 用 `uv run alembic upgrade head --sql` 核对生成的 DDL 落在 `analysis` schema，
      再对迁移后的库跑一次 autogenerate 确认输出为空（列注释若不一致会在这里暴露）
- [ ] 1.6 单元测试：`SSO_ENABLED=true` 缺 `DINGTALK_APP_SECRET` 时配置校验抛错；
      关闭时不校验（`uv run pytest test/`）

## 2. 钉钉客户端（对外 HTTP）

- [ ] 2.1 新建 `app/auth/dingtalk_client.py`：异步 `httpx`，封装
      `oauth2/userAccessToken`、`contact/users/me`、`oauth2/accessToken`、
      `topapi/user/getbyunionid`、`topapi/v2/user/get` 五个调用
- [ ] 2.2 企业 accessToken 进程内缓存（7200 秒有效、提前 300 秒续期）+ `asyncio.Lock`
      防并发重复换取；凭证不落库、不入响应体
- [ ] 2.3 钉钉侧错误映射为 `sso.*` 错误：授权码失效 → `sso.code_invalid`，
      网络超时/不可达 → `sso.provider_unavailable`；钉钉 errcode/errmsg 只进 `log_detail`
- [ ] 2.4 单元测试（mock httpx，不打真网）：并发取企业 accessToken 只换取一次；
      缓存在过期前复用；超时映射为 `sso.provider_unavailable`

## 3. 错误契约

- [ ] 3.1 `app/common/errors.py` 新增 `sso.*` 五个错误类：`state_invalid`(400) /
      `code_invalid`(400) / `not_employee`(403) / `account_disabled`(403) /
      `provider_unavailable`(503)，message 为可直接展示的中文
- [ ] 3.2 单元测试：五个错误类的状态码、code 与 message 符合规格；
      `log_detail` 不出现在序列化结果中

## 4. SSO 服务与端点

- [ ] 4.1 `app/auth/sso_service.py`：state 的签发与校验（`ADMIN_SECRET_KEY` 签名、
      随机 nonce、≤10 分钟过期、无状态）
- [ ] 4.2 用户名规范化函数（转小写、剥除非字母数字、冲突追加数字后缀），
      与 `UserCenterCreate` 的用户名规则保持一致
- [ ] 4.3 `user_crud.py` 新增按 `(external_idp, external_id)` 查找与 SSO 建档方法；
      建档时 `is_super` 恒 false、`hashed_pwd` 为 NULL、角色取 `SSO_DEFAULT_ROLE_CODE`
- [ ] 4.4 `sso_service` 主流程：验 state → 换用户凭证 → 取 unionId → 反查 userid
      （查不到抛 `sso.not_employee`）→ 取姓名 → 找账号或建档 → 停用则抛
      `sso.account_disabled` → 签 JWT。**已建档账号不回写任何属性**
- [ ] 4.5 `auth_api.py` 新增三个端点：`GET /sso/status`、`GET /sso/authorize-url`、
      `POST /sso/callback`，均免鉴权；`SSO_ENABLED=false` 时 status 返回
      `{enabled:false}`，另两个不可用
- [ ] 4.6 单元测试：state 签发/校验/过期/篡改；用户名规范化与冲突后缀；
      建档字段映射（断言 `is_super` 恒假）；授权 URL 的 query 参数齐全且
      `redirect_uri` 取自配置

## 5. 本地密码登录的边界

- [ ] 5.1 确认 `authenticate_user` 对 `hashed_pwd IS NULL` 走既有 `_DUMMY_PASSWORD_HASH`
      路径；**不得新增提前返回分支**（会重开时序侧信道）
- [ ] 5.2 单元测试：SSO 账号走 `POST /api/auth/token` 返回 401；
      其耗时与正常密码校验同阶（断言两条路径都实际调用了密码校验）

## 6. 集成测试（`TEST_DATABASE_URL` gate）

- [ ] 6.1 首次扫码建档 → 二次扫码复用同一行，不重复建账号
- [ ] 6.2 unionId 不变而企业 userid 变更时仍命中原账号，角色不变
- [ ] 6.3 钉钉 userid 与某本地账号用户名相同时，MUST NOT 登录进该本地账号，
      而是另建账号并加数字后缀
- [ ] 6.4 通讯录查不到 unionId → 403 `sso.not_employee`；账号 `is_active=false` →
      403 `sso.account_disabled` 且不签发令牌
- [ ] 6.5 钉钉 HTTP 一律 mock；测试计数带作用域，不用裸 `count(*)`（共享测试库）

## 7. 前端

- [ ] 7.1 `services/auth.ts` 新增 `ssoStatus` / `ssoAuthorizeUrl` / `ssoCallback`
- [ ] 7.2 从 `pages/Login/index.tsx` 抽出 `useLoginRedirect`（等待 `currentUser` 落位
      再跳转的那段竞态修复），登录页改为使用它。**不得复制成两份**
- [ ] 7.3 登录页加「钉钉登录」按钮，按 `ssoStatus` 显隐；关闭时不发授权地址请求
- [ ] 7.4 新增 `pages/SsoCallback/` 与路由 `/sso/callback`（`layout: false`、不挂 access
      守卫），处理三条路径：`?error=` / `?authCode=` / 参数缺失
- [ ] 7.5 `pages/User/` 增加「显示名」与「来源」（本地 / 钉钉）两列，
      `user_schemas` 与 `serialize_user` 相应透出字段
- [ ] 7.6 vitest：登录页按钮按 status 显隐；SsoCallback 三条路径各一例
      （`pnpm test`）

## 8. 运维脚本与验收

- [ ] 8.1 `scripts/check_dingtalk_sso.py`：打印授权 URL 供人工扫码，随后逐步跑通
      authCode → userAccessToken → users/me → 企业 accessToken → getbyunionid →
      v2/user/get，逐项断言，退出码非 0 即有项未通过
- [ ] 8.2 CLAUDE.md 的「Authentication & Authorization」一节补写 SSO：直连钉钉而非 OIDC、
      账号只按 unionId 匹配的理由、`is_super` 恒假、本地登录的逃生通道定位
- [ ] 8.3 全量回归：`uv run pytest test/`（含 `TEST_DATABASE_URL` 解开的集成部分）
      与 `pnpm lint && pnpm tsc && pnpm test` 全绿
- [ ] 8.4 人工验收：`SSO_ENABLED=false` 时密码登录不受影响；置 true 后真机扫码走通一次；
      用离职账号或通讯录外账号验证被拒

---

## 进度痕迹

- **2026-08-19**：规格四份产物写完并提交（`5479d90`），方案已同步到 issue #36 评论。
  **暂停，未开始写代码（0/35）。**

  **阻塞原因**：需要钉钉开发者后台的管理员权限才能建企业内部应用、取得
  AppKey/AppSecret 并配置重定向 URL 与通讯录读取权限。已向人事申请，等待授权。

  **解除阻塞后从第 1 组开始。** 第 1～5 组（配置、数据模型、迁移、错误契约、
  SSO 服务骨架、本地登录边界）其实**不依赖真实凭证**，钉钉的 HTTP 一律 mock，
  真要提前推进可以先做这五组；只有第 8 组的 `check_dingtalk_sso.py` 与真机扫码
  验收必须等凭证到位。

  **worktree 与分支有意保留**（`~/.claude-worktrees/dingtalk-sso` /
  `change/dingtalk-sso`）——按 start-change 的判据，分支还在即变更还活着，
  不是孤儿，请勿清理。
