## Why

后端现在并存 **4 种返回形状**与 **3 套错误路径**，同一个 `user_api.py` 内部就不自洽：`create`
的业务错误返 HTTP 200 + `status:1`、意外错误抛 500，而 `update` 连意外错误也返 200。

这已经不是整洁问题，它在**吞用户能看见的错误**：前端 `unwrap` 是在 HTTP 200 的响应体里判
`status` 后 `throw`，而 `app.tsx` 的 `errorConfig` 只接管 HTTP 层错误——这个 throw 无人接管，
而 `User/index.tsx` 的 `handleSubmit` / `handleToggle` 都没有 try/catch。结果是：创建重名用户时
后端明明返回了「用户名已存在」，用户看到的却是弹窗不关、没有任何提示；**反倒是走 500 分支时能
看到错误提示**，行为完全反直觉。`/analysis/search` 的 `ScopeError`（如「日期已移出保留窗口」）
返 200 + `success:false`，同样只表现为一个没有解释的空表格。

另外 `main.py` 里没有任何 `@app.exception_handler`，未捕获异常落到 FastAPI 默认的
`{"detail":"Internal Server Error"}`，没有 request id——用户报「刚才报错了」时，无法把这句话
对回日志里的某一次请求。

现在做的时机理由：amis 后台已于前端迁移阶段七删除，`{status,msg,data}` 信封失去了全部存在
理由；前端测试基建刚刚落地（8.1），契约改动有回归网兜底；而上传屏与用户屏的功能仍在演进，
越晚统一，需要改写的调用点越多。

## What Changes

- **新增统一错误出口**：定义 `APIError` 异常基类与错误码体系，注册全局 `exception_handler`，
  所有失败响应统一为 `{success: false, code, message, request_id}`，HTTP 状态码按语义给出
  （400/403/404/409/413/422/500），不再出现「业务失败却返 200」。
- **新增 request id**：中间件为每个请求生成 id，写入响应头与错误体，并进入日志上下文，
  使用户报障可直接定位到具体请求。
- **业务错误一律抛不返**：service 层抛 `APIError` 子类，API 层不再手工拼 `{"status": 1, ...}`。
  现有的 `ScopeError` / `ExportTooLargeError` / `ImportRejected` / `OSSNotConfigured` 收编为
  `APIError` 子类。
- **BREAKING｜删除 amis 信封**：`/api/upload/*` 与 `/api/user/{create,update,toggle-status}`
  不再返回 `{status,msg,data}`，改为成功返数据、失败抛异常。前端 `services/user.ts` 与
  `services/upload.ts` 中两份重复的 `unwrap` 一并删除。
- **BREAKING｜列表失败路径改走异常**：ProTable 契约 `{data,total,success}` 作为**成功**形状
  保留（框架要求），但失败不再以 `success:false` 混在 200 里返回。`protable_error` 移除。
- **前端错误提示收敛到唯一出口**：`errorConfig.errorHandler` 成为唯一提示点，页面层不再需要
  逐个 try/catch 才能让用户看见错误；顺带修掉 `User` 屏与 `Analysis` 屏当前吞错误的行为。

对导入路径的影响：**日报与周报两条路径同等适用**。本变更只改错误与响应的表达形式，
不触碰导入编排、分区写入与查询作用域的任何语义。

## Capabilities

### New Capabilities
- `api-error-contract`: 全后端统一的错误分类、错误码、HTTP 状态码映射、失败响应形状与
  request id 追踪；以及前端「唯一提示出口」的消费约定。

### Modified Capabilities
<!-- 无。`admin-spa` 的「失败响应可被前端统一识别」场景会被本变更收窄（失败不再走
     200 + success:false），但该 capability 尚未 sync 到 openspec/specs/，仍是
     migrate-frontend-to-antd-pro 的 delta。两者在该变更归档时一并对齐，见 design.md。 -->

## Impact

**后端**
- 新增 `app/common/errors.py`（`APIError` 基类与子类、错误码常量）、`app/common/error_handlers.py`
  （全局 handler 注册）、request id 中间件
- `main.py`：注册 handler 与中间件
- `app/common/responses.py`：移除 `protable_error`
- `app/table/analysis/analysis_api.py`、`app/table/upload/upload_api.py`、`app/user/user_api.py`、
  `app/auth/auth_api.py`：去掉手工错误拼装与 `raise HTTPException`
- `app/table/analysis/analysis_service.py`、`app/table/upload/import_service.py`、
  `app/table/upload/oss_client.py`：现有异常改继承 `APIError`

**前端**
- `src/app.tsx`：`errorConfig` 按新错误体取 `message` 与 `code`
- `src/services/{user,upload,analysis}.ts`：删除两份 `unwrap`，返回类型收紧
- `src/pages/{User,Analysis}/index.tsx`：移除对 `unwrap` 抛错的隐式依赖，失败反馈交给全局出口

**测试**
- `test/test_protable_contract.py` 需随 `protable_error` 移除而调整
- 新增错误契约的后端用例与前端用例（前端已有 vitest 基建）

**不影响**：数据库结构（无迁移）、鉴权语义、导入编排与分区写入、查询作用域规则。
