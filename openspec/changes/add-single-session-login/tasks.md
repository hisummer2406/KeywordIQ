## 1. 数据模型与迁移

- [x] 1.1 `app/user/user_model.py`：`UserCenter` 新增 `active_session_id: Mapped[str | None]`
      （`String(64)`，nullable，注释写明"唯一写入点是 `rotate_active_session` /
      `clear_session_if_current`，其余代码不得直接赋值"）
- [x] 1.2 生成 Alembic 迁移（`uv run alembic revision --autogenerate -m "..."`），
      `uv run alembic upgrade head --sql` 核对只产出单纯 `ADD COLUMN`，无索引、无默认值
- [x] 1.3 `uv run alembic upgrade head` 在本地库跑通；`uv run alembic downgrade -1` 验证可回滚

## 2. 错误契约

- [x] 2.1 `app/common/errors.py` 新增 `CODE_AUTH_SESSION_INVALID = "auth.session_invalid"`
      常量与 `SessionInvalidError(APIError)`（`http_status=401`，见 design 决策 2 的文案）

## 3. 会话签发与校验（后端核心）

- [x] 3.1 `app/user/user_crud.py` 新增 `rotate_active_session(user) -> str`：
      `secrets.token_urlsafe(32)` 生成、赋值、`commit`、`refresh`、返回
- [x] 3.2 `app/user/user_crud.py` 新增 `clear_session_if_current(user_id, session_id) -> None`：
      条件 `UPDATE`，不判断影响行数，恒视为成功
- [x] 3.3 `app/auth/login_auth.py`：`create_access_token` 签名加 `session_id: str` 必填参数，
      编码进 JWT payload
- [x] 3.4 `app/auth/simple_auth.py`：`login()` 在 `authenticate_user` 成功后调用
      `rotate_active_session`，拿到的 `session_id` 传入 `create_access_token`
- [x] 3.5 `app/auth/simple_auth.py`：`get_current_user` 加 `request: Request` 参数；
      查到用户、`is_active` 通过后，读 `payload.get("session_id") or None`，与
      `user.active_session_id` 比较，不一致（含缺失/空串）抛 `SessionInvalidError`；
      一致时把 `session_id` 写入 `request.state.session_id`
- [x] 3.6 `app/auth/sso_service.py`：`login_with_auth_code` 在 `is_active` 校验通过后调用同一个
      `rotate_active_session`，`session_id` 传入 `create_access_token`（与密码登录共用逻辑，
      不得另写一遍）
- [x] 3.7 `app/auth/auth_api.py`：`logout` 端点读 `request.state.session_id`，调用
      `clear_session_if_current(current_user.id, session_id)`

## 4. 后端测试

- [x] 4.1 更新 `test/test_rbac_auth_unit.py` 里 `create_access_token` 的调用点，补上
      `session_id` 参数；`test_jwt_roundtrip_carries_role` 一并断言 `payload["session_id"]`
- [x] 4.2 单元测试：`get_current_user` 对"会话编号缺失/空串/不匹配"三种输入均抛
      `SessionInvalidError`（不连库，构造 payload 与 mock 用户）
- [x] 4.3 集成测试（`TEST_DATABASE_URL` gate，参照 `local-db` skill 起库）：
      - 登录后旧令牌立即失效（B 登录顶替 A）
      - 登录失败不改变已有 `active_session_id`
      - 并发登录（`asyncio.gather` 两次登录同一账号）后，只有最后提交的那次对应令牌有效
      - 条件退出：正常退出后原令牌失效；旧令牌退出请求与新登录并发时不清除新会话
      - 钉钉登录路径同样触发会话替换（可直接测 `sso_service.login_with_auth_code` 或
        `find_or_create_sso_user` + 手动调用共用函数，视测试现有 fixture 而定）
- [x] 4.4 `uv run pytest test/ -q` 全量跑通（含新集成测试）

## 5. 前端

- [x] 5.1 `frontend/src/app.tsx` 的 `errorHandler`：401 分支按 `body?.code` 区分文案
      （`auth.session_invalid` → "账号已在其他浏览器登录"；其余沿用现有"登录已过期"）
- [x] 5.2 同一分支加入"请求发起时用的令牌是否仍是当前令牌"的比对（`error.config` 的
      `Authorization` 头 vs. `getToken()`），不一致时直接返回、不清理不跳转不提示
- [x] 5.3 `frontend/src/services/auth.ts` / `API.CurrentUser` 等类型如涉及错误体 `code`
      字段的强类型引用，一并检查是否需要补充类型

## 6. 前端测试

- [x] 6.1 vitest：`errorHandler` 对 `auth.session_invalid` 与普通 `auth.unauthorized` 401
      呈现不同提示文案
- [x] 6.2 vitest：模拟"请求发出后本地令牌已被新登录替换，之后收到该旧请求的 401 响应"，
      断言不清理本地凭证、不跳转、不弹提示
- [x] 6.3 `pnpm test`（或按改动文件用 `pnpm exec vitest related`）跑通

## 7. 文档

- [x] 7.1 `app/auth/auth_api.py` 的 `logout` 端点文档字符串：删除"JWT 无状态，前端清 token
      即可"这句已经不成立的描述，改为准确描述条件清除行为
- [x] 7.2 `CLAUDE.md` 的"Authentication & Authorization"一节补充：会话唯一性规则、
      `active_session_id` 字段与两处唯一写入点、`auth.session_invalid` 错误码、
      前端旧响应比对令牌的处理方式

## 8. 收尾验证

- [x] 8.1 `openspec validate add-single-session-login --strict` 通过
- [ ] 8.2 按 `start-change` skill 第 4 节提交、开 PR（`--base v2`），PR 说明附变更 id、
      `Refs #89`、spec delta 文件列表
