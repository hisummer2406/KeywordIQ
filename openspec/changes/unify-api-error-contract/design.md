## Context

后端经历 amis → SPA 的迁移后，响应形状层层叠加而从未收口。当前实测状态：

| 形状 | 端点 |
|---|---|
| `{data,total,success}` ProTable 契约 | `/user/table`、`/analysis/search`（变体，多带作用域回显） |
| `{status,msg,data}` amis 信封 | `/upload/*` 全部、`/user/{create,update,toggle-status}` |
| `{detail}` FastAPI 默认 | `/analysis/{scope,categories,export}` 的 422/413、`/user/*` 的 500 |
| 裸 dict | `/auth/token` → `{token,user}`、`/auth/logout` → `{success}`、`/upload/sites` → `{sites}` |

错误路径同时存在三套：业务错误返 200 + `status:1`、业务错误抛 `HTTPException`、意外错误抛 500。
`main.py` 没有任何 `@app.exception_handler`（全仓 grep 零命中）。

2026-08-01 的专项审计补充了几项实测结论，它们改变了本变更的优先级与范围：

- **422 校验错误会把用户提交的请求体回显给客户端**。实测：`POST /api/auth/token` 提交
  `{"password": "..."}`（缺 `username`）时，响应体含 `"input": {"password": "<明文>"}`。
  根因是无 `RequestValidationError` handler，FastAPI 默认把 `exc.errors()` 整个透传，而
  Pydantic v2 的错误对象在 `missing` 类型下把**整个父对象**作为 `input` 带出。该响应还会进
  nginx access log。**这是本变更中风险最高的一项，且可脱离整体重构单独修复。**
- **`detail` 字段同名不同类型**：业务错误里是字符串（`analysis_api.py:63` 等），校验错误里是
  数组。这正是 `frontend/src/app.tsx:72` 的 `detail || message` 取法对数组形态会渲染出
  `[object Object]` 的原因。
- **500 目前不泄漏 traceback**，但靠的是 `main.py` 的 `FastAPI(...)` 恰好没传 `debug=`，
  Starlette 在 `debug=False` 下返回固定的 `text/plain` 文案。这是默认值的侥幸而非设计。
- **未捕获异常不产生任何应用日志**。`main.py:21` 只有 `basicConfig(level=INFO)`，没有记录
  HTTP 错误的钩子，栈只由 uvicorn 打到 stderr，`logs/app.log` 里没有痕迹。
- **数据库异常无分类**：`SQLAlchemyError` / `OperationalError` 全仓零捕获，连接超时与池耗尽
  会落到与业务 500 无法区分的兜底路径。
- **后台任务的兜底丢栈**：`upload_api.py:41` 与 `:58` 的 `except Exception` 写的是
  `logger.error(..., exc)`，没有 `exc_info`，栈永久丢失。

约束与前提：

- **没有外部 API 消费方**。前端 SPA 与后端由 `deploy.sh` 一次发布、nginx 同源托管，
  因此前后端契约可以协同切换，不需要长期兼容期。
- amis 后台已在前端迁移阶段七删除，`{status,msg,data}` 信封已无任何服务端消费方。
- 前端刚落地 vitest 基建（26 条用例），契约改动有回归网。
- **导出端点是流式响应**：`StreamingResponse` 一旦开始产出，HTTP 状态码与响应头已经发出，
  无法再改成错误响应。这对错误契约构成硬约束（见决策 6）。

## Goals / Non-Goals

**Goals:**

- 全后端**一种**失败响应形状、**一条**失败路径（抛异常），HTTP 状态码承载语义。
- 每个失败响应带 `request_id`，能把用户口述的「刚才报错了」对回日志里的具体请求。
- 前端的错误提示收敛到**唯一出口**，页面层不写 try/catch 也不会吞掉错误。
- 修掉当前已存在的吞错误行为（用户屏创建重名、分析屏作用域错误）。

**Non-Goals:**

- 不引入国际化 / 多语言错误消息（当前后台只有中文）。
- 不改鉴权语义（令牌来源、校验方式、401/403 的判定条件全部不动）。
- 不改导入编排、分区写入、查询作用域规则——只改这些失败**如何被表达**。
- 不做错误上报 / APM 接入（request id 是其前置，接入与否另议）。
- 不改数据库：**本变更无 Alembic 迁移，不触碰 `analysis` schema 的任何对象。**

## Decisions

### 决策 1：业务错误一律「抛」，不「返」

service 层抛 `APIError` 子类，API 层不再手工拼错误体。理由是当前的返值式错误必须靠每个调用点
自觉检查，而漏检不会报错、只会静默——这正是用户屏吞掉「用户名已存在」的机制。异常无法被
无声忽略。

**备选（已否决）**：保留返值式，靠 Pydantic 响应模型强制形状。否决理由是它管不住调用点是否
检查，缺陷的根因不在形状而在「失败可以被忽略」。

### 决策 2：错误码用 `<域>.<原因>` 的语义化字符串

如 `analysis.scope_invalid`、`analysis.export_too_large`、`upload.import_rejected`、
`upload.oss_not_ready`、`user.duplicate_name`、`auth.invalid_credentials`、`internal.unhandled`。

理由：日志里可读、无需维护中央数字表、前端要分支处理时可直接比对。域前缀避免不同模块撞名。

**备选（已否决）**：数字错误码（如 10001）。否决理由是需要额外维护对照表，且日志里不可读。

### 决策 3：统一失败响应体

```json
{
  "success": false,
  "code": "user.duplicate_name",
  "message": "用户名已存在",
  "request_id": "0f3a…"
}
```

`message` 是**面向用户**的中文，可直接展示；内部细节（异常栈、SQL、对象键）只进日志，不进响应。
保留 `success: false` 字段虽然与非 2xx 状态码有冗余，但让响应体自解释，前端与排查者拿到孤立的
一段 JSON 也能判断成败。

HTTP 状态码映射：

| 状态码 | 含义 | 示例 |
|---|---|---|
| 400 | 请求参数不合法 | 文件名解析不出报告日期 |
| 401 | 未认证 | 令牌缺失/过期 |
| 403 | 已认证但无权限 | 缺少 `analysis:export` |
| 404 | 目标不存在 | 用户 id 不存在、OSS 对象不存在 |
| 409 | 与当前状态冲突 | 用户名已存在、导入已在运行被拒 |
| 413 | 请求/结果超出上限 | 导出超过 `EXPORT_ROW_CAP` |
| 422 | 语义合法但业务不接受 | 作用域非法、日期已移出保留窗口 |
| 500 | 未预期错误 | 兜底 |

### 决策 4：ProTable 契约只表达成功

`{data,total,success}` 作为**成功**形状保留（ProTable 框架要求），`protable_error` 移除。
列表失败走非 2xx + 统一错误体，由前端 `errorConfig` 提示，ProTable 进入自身的错误态。

这**收窄了** `admin-spa` spec 的「失败响应可被前端统一识别」场景——原文允许失败以
`success:false` 表达。两者在 `migrate-frontend-to-antd-pro` 归档时对齐；本变更的 delta spec
显式记录这一收窄，避免归档时把旧语义又 sync 回主 specs。

### 决策 5：request id 由中间件生成，经 contextvar 进日志

中间件为每个请求生成短 id（uuid4 前 8 位足够人工比对），写入响应头 `X-Request-Id` 与错误体，
同时放进 `contextvars.ContextVar` 供日志格式化读取。若入站请求已带 `X-Request-Id`（nginx 或
上游注入），沿用而不重新生成，保证跨层可串联。

`contextvars` 而非中间件传参：错误发生在 service 层深处，逐层透传 request id 会污染所有函数签名。
`contextvars` 在 FastAPI 的 async 与线程池两种执行上下文下都能正确隔离。

### 决策 6：流式端点的错误必须在开流前抛出

`StreamingResponse` 一旦开始产出，状态码与响应头已发出，之后再抛异常只会得到一个**截断的
200 响应**——对导出而言就是一份被当成完整数据使用的 CSV。

因此约定：流式端点 MUST 在返回 `StreamingResponse` 之前完成全部可预见的校验（作用域、权限、
规模上限）。导出端点已按此实现（上限检查在 `resolve_export_scope` 里、开流之前）；本变更把它
从「实现细节」提升为**契约层面的规则**并写进 spec，防止后续新增流式端点时重蹈。

生成器内部的意外异常无法转成错误响应，只能记日志 + 断流，客户端表现为下载不完整。这是流式响应
的固有代价，接受。

### 决策 7：一刀切，不设兼容期

前后端同发布、无外部消费方，因此不做「两种形状并存一段时间」。理由是兼容期需要双份代码与双份
测试，而它要防的风险（消费方未跟上）在本项目不存在。

回滚以**整体部署回退**为单位（与 `migrate-frontend-to-antd-pro` 任务 9.1 的口径一致），
不做页面级或端点级切回。

### 决策 8：前端保留「静默失败」的显式开口

统一到唯一出口后，个别场景仍需要不弹提示（如登录页的 401 是凭证错误、由表单自己呈现）。
在请求层提供显式选项（如 `skipErrorHandler`）而非让页面层 try/catch 吞掉——**静默必须是
显式声明的，不能是忘记处理的副产品**。这是本变更的核心意图，不能在实现时被绕过。

### 决策 9：校验错误只透 `loc` 与 `msg`，丢弃 `input` 与 `url`

`RequestValidationError` handler 把 Pydantic 的错误列表**重建**为 `fields: [{field, msg}]`，
而不是过滤 `exc.errors()` 的键。

理由：过滤是黑名单思路，Pydantic 版本升级新增字段时会重新泄漏；重建是白名单，只有显式列出的
才会出去。`url`（pydantic 文档链接）对用户无意义，一并丢弃。

```python
fields = [{"field": ".".join(str(x) for x in e["loc"][1:]), "msg": e["msg"]}
          for e in exc.errors()]
```

`loc[1:]` 去掉首段的 `body` / `query` 前缀，只留字段路径。

**备选（已否决）**：`exc.errors(include_input=False)`。Pydantic 提供了该开关，但它不去掉 `url`，
且把「是否泄漏」的责任交给了一个容易在重构中被漏掉的参数；白名单重建不依赖调用方记得传参。

### 决策 10：基础设施故障单独归类为 503

数据库连接失败、超时、连接池耗尽映射为 `db.unavailable` + **503**，与业务侧 500 分开。

理由有二：一是语义正确——服务暂时不可用、客户端重试有意义，而业务 500 重试无用；二是告警策略
必须能分别设定，混在一起就只能整体调高或调低阈值。

判据用 `isinstance(exc, OperationalError)`（SQLAlchemy 的连接/超时类基类），其余 `SQLAlchemyError`
归 `db.error` + 500。

这条对本仓库有具体意义：已知的「导入期半开连接把 worker 挂死」正属此类，现在它在日志与告警上
与普通 500 无差别，等于失去了最该被盯住的信号。

### 决策 11：日志分级由 handler 分工自然完成，不做集中判断

`APIError`（4xx）在其 handler 里走 `logger.warning`，兜底 `Exception`（5xx）走
`logger.exception`（自带栈）。不引入「先判状态码再决定级别」的集中逻辑——handler 已经按类别
分好，再判一次是重复。

后台任务不经过 handler，因此其 `except Exception` 分支必须自己用 `logger.exception`
（决策见 spec 的「后台任务的失败有独立处理路径」）。

告警渠道本身不在本变更范围内（见 Non-Goals），但 5xx 的 ERROR 级 + request id + 完整栈是接入
任何告警系统的前置，本变更负责把这个前置做齐。

### 决策 12：显式禁止 `FastAPI(debug=...)`

注册裸 `Exception` handler 后，500 不再经 Starlette 的 `ServerErrorMiddleware`，`debug` 对
响应体的影响自然消失。但仍在 `main.py` 的 app 构造处留注释明确禁止传 `debug=settings.DEBUG`
——仓库里已有 `settings.DEBUG` 在控制 `docs_url`，顺手加一个 `debug=` 是很自然的动作，而生产
若 `DEBUG=True` 会立刻把完整 traceback 写进 500 响应体。

## Risks / Trade-offs

- **[密码回显在整个重构落地前持续存在]** 明文凭证会一直写进响应体与 nginx access log →
  该修复（`RequestValidationError` handler + 白名单重建）**不依赖本变更的其余部分**，
  约 15 行、无破坏性、前端无需同步改动。任务组 1 已把它排为最先完成项，可独立提交与发布，
  不必等整套契约切换。

- **[前后端版本错配]** 一刀切期间若只发布了一侧，错误提示会失效或变成兜底文案 →
  `deploy.sh` 已是前后端一次发布；发布顺序上后端先行（新前端能读旧形状的兜底路径，
  反之不行），回滚以整体部署为单位。

- **[提示噪声增加]** 原本被静默吞掉的失败现在都会弹出提示，用户可能感到「突然变吵」 →
  这是修复而非退化（用户此前看到的是无解释的空表格与不关闭的弹窗）；确需静默的场景走
  决策 8 的显式开口。

- **[`message` 措辞直接暴露给用户]** 服务层抛错时若把内部信息写进 `message`，会泄漏到界面 →
  spec 明确 `message` 面向用户、内部细节只进日志；`internal.unhandled` 的 `message` 固定为
  通用文案，不透传异常字符串。

- **[流式端点仍有不可转换的失败]** 开流后的异常只能断流 → 决策 6 已把可预见校验前移；
  剩余的是固有代价，通过日志与 `request_id` 保证可追查。

- **[测试改动面]** `test/test_protable_contract.py` 依赖 `protable_error`，需随之调整 →
  该文件的核心断言（无 token 必拒、成功形状是 `{data,total,success}`）不受影响，
  只需替换失败形状的用例。

**对连接池 / 内存 / 索引与执行计划的影响：无。** 本变更不新增数据库查询、不改动任何 SQL、
不改变会话生命周期与事务边界；request id 中间件与异常处理器都在应用层，不持有数据库连接。
导出端点的自建会话与流式游标行为保持不变。
