## Context

`AuthService`（`app/auth/login_auth.py`）签发的 JWT 目前只承载 `user_id`/`username`/
`is_super`/`role`，没有会话概念——同一账号可以同时持有任意多张有效令牌。本变更给 JWT
加一个服务端可撤销的维度：`session_id`，配合 `user_center.active_session_id` 实现
"只认最后一次登录"。

两条登录路径都要接入：本地密码登录 `simple_auth.login`（`AsyncSessionFactory` 自建会话）
与钉钉扫码回调 `sso_service.login_with_auth_code`（`get_async_db` 依赖注入的会话）。两者
最终都调用同一个 `auth_service.create_access_token(...)` 签发令牌——这也是本变更收敛
"生成会话 → 落库 → 签发"逻辑的落点。

`user_center` 已登记在 `migration_scope.py` 的 `V2_TABLES`，加列走 autogenerate 即可，
不需要额外注册；该表不在 `partitioning.PARTITIONED_TABLES` 里，加列是纯 additive DDL，
不涉及分区。

## Goals / Non-Goals

**Goals:**
- 账号级会话唯一：新登录使旧会话失效，鉴权时校验，主动退出条件清除。
- 本地密码登录与钉钉扫码登录共用同一套会话签发/校验逻辑，不各自实现。
- 顶号 401 与其他鉴权失败使用不同错误码，前端据此呈现不同提示。
- 前端能区分"这次 401 确实说明当前会话失效"与"这是一个迟到的、属于已被替换令牌的响应"。

**Non-Goals（与 issue 非目标一致）:**
- 不引入 Redis / 设备指纹 / 浏览器硬件绑定 / SSE / WebSocket / 心跳轮询 / 设备管理界面。
- 不做"已登录页面无请求时主动感知顶号并跳转"——用户下一次请求自然触发。
- 不修改钉钉配置、不重做 SSO 授权流程、不扩展角色权限矩阵。
- 不撤回已经通过鉴权、正在处理中的请求。

## Decisions

### 1. 会话签发收敛到 `UserCenterCRUD` 的一个方法，两个登录入口共用

新增 `UserCenterCRUD.rotate_active_session(user: UserCenter) -> str`：

```python
async def rotate_active_session(self, user: UserCenter) -> str:
    session_id = secrets.token_urlsafe(32)
    user.active_session_id = session_id
    await self.db.commit()
    await self.db.refresh(user)
    return session_id
```

`simple_auth.login`（密码校验通过后）与 `sso_service.login_with_auth_code`（`find_or_create_sso_user`
拿到 `user` 后、`is_active` 检查通过后）都调用它，再把返回的 `session_id` 传给
`create_access_token`。**登录失败路径不得触达这个方法**——`authenticate_user` 校验不通过
或 SSO 账号被禁用/非在职时，函数在 `rotate_active_session` 之前就已经抛错返回，天然满足
"失败的登录尝试不得替换有效会话"。

`create_access_token` 签名改为 `create_access_token(user_id, username, is_super, role, session_id)`，
从"生成者"降级为"纯签发者"——不再自己决定会话编号，只负责把调用方给的值编码进 JWT。三处
调用点（`simple_auth.py`、`sso_service.py`、`test/test_rbac_auth_unit.py`）同步改签名。

**commit 落在 CRUD 层而非 service 层**：这与 `Important Constraints` 里"CRUD 层永不 commit"
的通用约定不一致，但与 `user_crud.py` 现有的 `create_user`/`update_user`/`create_sso_user`/
`sync_avatar` 四个方法的实际写法一致——这个模块的既定做法就是 CRUD 自行 commit。跟随
同文件内既有约定，不在这一个变更里单独把整个 user 模块的事务边界改成另一种风格。

*备选方案*：在 `simple_auth.login` 里手写 `user.active_session_id = ...; await db.commit()`，
在 `sso_service.login_with_auth_code` 里重复一遍。放弃——这正是仓库里反复出现过的"两处
实现各自漂移"模式（如 `dim_product` 的 `IS DISTINCT FROM`、`competitor_new` 的口径），
一旦其中一处改了阈值或异常处理，另一处不会跟着报错，只会悄悄产生不一致的行为。

### 2. 顶号 401 用一个统一的 `APIError` 子类，三种"会话不合法"状态共用同一个错误码

`app/common/errors.py` 新增：

```python
class SessionInvalidError(APIError):
    """令牌的会话编号缺失/为空/与当前有效会话不一致——账号在别处重新登录，或本令牌签发于
    本功能上线之前（不携带 session_id）。两种成因对用户而言是同一个可操作结论：
    "这张令牌不再代表当前有效会话，请重新登录"，故不再细分错误码。
    """
    http_status = 401
    code = "auth.session_invalid"
    message = "登录状态已失效，可能是账号在其他浏览器登录，请重新登录"
```

`get_current_user` 里，用户查到、`is_active` 校验通过之后，加一段：

```python
session_id = payload.get("session_id") or None   # None 与 "" 统一按缺失处理
if not session_id or session_id != user.active_session_id:
    raise SessionInvalidError()
```

**必须抛 `APIError` 子类，不能沿用现有 `raise HTTPException(401, ...)`。** 现状是
`get_current_user` 的所有失败分支都走 `HTTPException`，被 `http_exception_handler` 统一
映射成 `code: "auth.unauthorized"`——如果顶号判定也走这条路，前端拿到的错误码和"令牌
过期/畸形"完全一样，做不出"账号已在其他浏览器登录"这句区别于普通过期的提示。这是
本变更能不能满足验收条件"明确原因"的关键路口，必须显式换成 `APIError`。

*不采用*的方案：给"缺失"“为空”“不匹配"三种状态分别定义三个错误码。三者对用户来说是
同一个可操作结论（重新登录），拆开只会让前端多写三个几乎相同的分支，却没有对应的
不同处理方式。

### 3. `logout` 端点通过 `request.state` 拿到本次令牌的 session_id，不重复解码

`get_current_user` 新增 `request: Request` 参数，校验通过后写
`request.state.session_id = session_id`；`logout` 端点在 `Depends(get_current_user)` 之后
读 `request.state.session_id`，连同 `current_user.id` 一起做条件更新：

```python
await crud.clear_session_if_current(current_user.id, session_id)
```

```python
async def clear_session_if_current(self, user_id: int, session_id: str) -> None:
    await self.db.execute(
        update(UserCenter)
        .where(UserCenter.id == user_id, UserCenter.active_session_id == session_id)
        .values(active_session_id=None)
    )
    await self.db.commit()
```

不判断受影响行数——0 行（已被新登录顶替）与 1 行（确实清掉了）都是"目标状态已达成"，
不是错误，端点始终返回成功。这满足"旧会话的退出请求与新登录并发时,不能注销新会话"：
旧请求的 `WHERE active_session_id = <旧 session_id>` 在新登录已经把该列改写之后必然
匹配不到行，条件更新自然是空操作。

*不采用*把 session_id 塞进 `get_current_user` 返回值（改成返回元组或包一层对象）的方案：
`require_permission`/`require_super`/`require_any_permission` 三个依赖工厂全部依赖
`get_current_user` 的返回类型是 `UserCenter`，改返回类型要牵动这三处 + 全仓所有用
`Depends(simple_auth.get_current_user)` 的端点签名。`request.state` 是 FastAPI 惯用的
"依赖链内附加信息"方式，不改变 `get_current_user` 对外的返回契约。

### 4. 前端：请求级别比对 token，隔离"顶号"与"过期响应属于已被替换的旧令牌"

`errorHandler` 目前对非登录页的任何 401 无差别执行"清 token + 提示 + 跳登录"。改动两点：

1. **按 `code` 区分文案**：`code === "auth.session_invalid"` 显示"账号已在其他浏览器登录"；
   其他 401（`auth.unauthorized`，令牌过期/畸形）沿用现有"登录已过期，请重新登录"。
2. **响应到达时校验这次失败请求当时用的是不是当前仍在生效的 token**：

```ts
if (status === 401 && history.location.pathname !== LOGIN_PATH) {
  const requestToken = error?.config?.headers?.Authorization;
  const currentToken = getToken();
  if (requestToken !== `Bearer ${currentToken}`) {
    // 这个 401 属于一张已经不再是"当前令牌"的旧请求（本浏览器同时又登录过一次），
    // 忽略——不清理、不提示、不跳转，避免把用户刚取得的新会话踢掉。
    return;
  }
  clearToken();
  message.warning(describe(body, status));  // 按 code 呈现对应文案
  history.push(LOGIN_PATH);
  return;
}
```

`requestInterceptors` 已经把 token 写进 `config.headers.Authorization`（`app.tsx:183-190`），
`error.config` 是 axios 错误对象自带的原始请求配置，不需要额外埋点记录"发起时用的 token"。

*备选方案*：给每次登录生成一个前端本地"会话代号"（如时间戳），随请求携带在自定义头里，
响应时比对代号。放弃——`Authorization` 头本身已经是这张请求当时用的令牌，直接比对更
简单，不需要新协议。

### 5. 并发登录的顺序由单行 `UPDATE` 的行锁定义，不加显式锁

`rotate_active_session` 就是一条 `UPDATE user_center SET active_session_id = ... WHERE id = ...`
（经 `db.commit()` 触发）。两个并发登录请求对同一行的更新，PostgreSQL 在 READ COMMITTED
下天然把第二个写者阻塞到第一个提交为止，最后提交的那次即最终生效值——这正是 issue 要的
"以数据库串行完成会话替换的顺序定义'最后一次'"，不需要 `pg_try_advisory_lock` 或应用层锁
（那是导入路径为限制并发度用的，这里目的不同：这里允许两个请求都跑完，只要求最终状态
一致）。

### 6. 字段定义

`active_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)`。
`secrets.token_urlsafe(32)` 产出 43 个字符，64 留出余量。不建索引——查询路径永远是
"先按主键 `id` 查到用户行，再比较该行的 `active_session_id`"，不存在按 `active_session_id`
反查用户的场景。

## Risks / Trade-offs

- **[风险] 上线瞬间所有在线用户的旧 JWT 因缺少 `session_id` 集体失效** → 已在 issue 非目标
  /行为边界中确认为预期行为（BREAKING，需重新登录一次），不做兼容分支。
- **[风险] 已通过鉴权、正在处理中的长请求不会被这次变更撤回** → issue 明确接受
  （"已通过鉴权的在途请求不承诺撤回"），与现有"离职员工在 JWT 过期前仍有效"的既有代价
  同一类型，不新增机制去堵。
- **[风险] `active_session_id` 一旦被本变更之外的代码路径写入（如未来某个批量运维脚本
  误操作），会把在线用户顶下线** → 约定 `rotate_active_session` /
  `clear_session_if_current` 是这一列**唯一**的写入点，其余代码不得直接赋值，参照仓库里
  "唯一事实来源"惯例在字段注释中写明。
- **[权衡] 错误码不区分"缺失 session_id 的旧令牌" vs "被新登录顶替"** → 二者对用户来说
  都是"重新登录"这一个操作，拆分错误码没有对应的不同前端行为，故合并（决策 2）。

## Migration Plan

1. Alembic 迁移：`user_center` 新增 `active_session_id VARCHAR(64) NULL`，无默认值、无索引、
   无数据回填（全体现有用户的该列天然为 NULL，等价于"当前没有有效会话"，与"需要重新登录
   一次"的预期行为一致）。用 `alembic upgrade head --sql` 核对生成的是单纯 `ADD COLUMN`。
2. 部署顺序：迁移先行（加列不影响未升级代码——旧代码不读这一列），应用代码随后发布。
   两者之间的窗口期，旧代码继续正常工作（不校验会话），无中间不可用状态。
3. 代码发布后：所有已持有旧 JWT（不含 `session_id`）的用户，下一次请求收到
   `auth.session_invalid`，前端提示并跳登录——这就是"现有用户需要重新登录一次"的落地方式，
   不需要额外的强制登出批处理。
4. **回滚**：应用代码回滚到上一版本即可（旧代码不读新列，忽略它）；数据库列是否一并
   `downgrade` 无所谓，加列本身对旧代码无副作用，不必与代码回滚同步执行。

## Open Questions

无——方案已在 issue 中确认，上述决策覆盖了实现阶段会遇到的分叉点。
