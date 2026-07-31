## 0. 止血：校验错误回显明文密码（可独立提交与发布）

> 本组不依赖后续任何一组，约 15 行、无破坏性、前端无需同步改动。先做、先发。

- [x] 0.1 注册 `RequestValidationError` handler：把 Pydantic 错误**白名单重建**为
  `fields: [{field, msg}]`（`field` 取 `loc[1:]` 拼接），**丢弃 `input` 与 `url`**；
  不要用过滤 `exc.errors()` 键的黑名单写法（见 design 决策 9）
- [x] 0.2 回归测试：`POST /api/auth/token` 提交 `{"password": "..."}`（缺 `username`），
  断言响应体中**不含**该密码字符串、状态码仍为 422、且指出 `username` 缺失
  （`uv run pytest test/` 可验证）
- [x] 0.3 顺带扫描其余端点的请求模型，确认没有别的凭证类字段会经同一路径回显

## 1. 错误基础设施（后端，无行为变更）

- [x] 1.1 新建 `app/common/errors.py`：`APIError` 基类（`code` / `message` / `http_status`）与子类
  （`BadRequestError` 400、`UnauthorizedError` 401、`ForbiddenError` 403、`NotFoundError` 404、
  `ConflictError` 409、`PayloadTooLargeError` 413、`UnprocessableError` 422），错误码常量按
  `<域>.<原因>` 集中声明
- [x] 1.2 新建 request id 中间件：生成短 id（uuid4 前 8 位），沿用入站 `X-Request-Id`，
  写入响应头，并放进 `contextvars.ContextVar`
- [x] 1.3 日志格式接入 request id（从 contextvar 读取，缺省为 `-`），确认线程池执行的
  同步端点也能读到
- [x] 1.4 新建 `app/common/error_handlers.py`：注册 `APIError`、`RequestValidationError`（并入
  0.1 的实现）、`StarletteHTTPException`（兜底框架与依赖里抛出的，如 `simple_auth` 的 401/403）、
  `SQLAlchemyError`、`Exception` 五类处理器，统一产出 `{success,code,message,request_id}`；
  `internal.unhandled` 用固定文案，原始异常只进日志
- [x] 1.5 日志分级落到 handler 分工：`APIError` handler 用 `logger.warning`（4xx，不告警），
  兜底 `Exception` handler 用 `logger.exception`（5xx，带完整栈进 `logs/app.log`）；
  不做集中的状态码判级逻辑
- [x] 1.6 数据库异常单独归类：`OperationalError` → `db.unavailable` + 503，
  其余 `SQLAlchemyError` → `db.error` + 500，使连接超时/池耗尽可与业务 500 分开告警
- [x] 1.7 `main.py` 注册中间件与处理器；在 `FastAPI(...)` 构造处加注释**明确禁止**传
  `debug=settings.DEBUG`（该模式会把 traceback 写进 500 响应体）
- [x] 1.8 单元测试：五类处理器的状态码与响应体形状、request id 沿用与生成、
  兜底错误不透传异常文本、`OperationalError` 得到 503、未捕获异常在应用日志中留下带栈记录
  （`uv run pytest test/` 可验证）

## 2. 服务层异常收编（后端）

- [x] 2.1 `analysis_service.ScopeError` → 继承 `UnprocessableError`，码 `analysis.scope_invalid`；
  `ExportTooLargeError` → `PayloadTooLargeError`，码 `analysis.export_too_large`
- [x] 2.2 `import_service.ImportRejected` → `ConflictError`，码 `upload.import_rejected`；
  `oss_client.OSSNotConfigured` → `UnprocessableError`，码 `upload.oss_not_ready`；
  `OSSObjectNotFound` → `NotFoundError`，码 `upload.object_not_found`
- [x] 2.3 用户域新增业务异常：用户名重复 → `ConflictError`（`user.duplicate_name`）、
  用户不存在 → `NotFoundError`（`user.not_found`）
- [x] 2.4 回归测试：现有 `test/test_analysis_export_unit.py` 的 413/422 断言改为校验统一错误体，
  确认状态码语义不变

## 3. API 层去手工错误拼装（后端，BREAKING）

- [x] 3.1 `analysis_api.py`：移除三处 `raise HTTPException`，`/search` 的
  `except ScopeError → success:false` 分支删除（改为向上抛）
- [ ] 3.2 `upload_api.py`：删除全部 `return {"status": 1, "msg": ...}`（约 12 处），
  成功路径去掉 `{"status": 0, "data": ...}` 信封直接返数据
- [ ] 3.3 `user_api.py`：`create` / `update` / `toggle-status` 去信封、去 `HTTPException`；
  `table` 保留 `protable()` 成功路径，删除 `protable_error` 分支
- [ ] 3.4 `auth_api.py`：确认裸 dict 返回符合新契约（成功形状不变，失败走异常）
- [ ] 3.5 `app/common/responses.py` 移除 `protable_error`
- [ ] 3.6 调整 `test/test_protable_contract.py`：失败形状用例改为断言非 2xx + 统一错误体，
  保留「无 token 必拒」与成功契约断言
- [ ] 3.7 新增后端契约测试：逐端点覆盖典型失败（重名用户 409、非法站点 422、
  导出超限 413、无权限 403），断言响应体形状一致（`uv run pytest test/` 可验证）

## 4. 流式端点契约固化（后端）

- [ ] 4.1 确认导出端点的全部可预见校验都在返回 `StreamingResponse` 之前完成（现状已满足，
  本任务是加断言而非改实现）
- [ ] 4.2 生成器内部异常补日志（含 request id），确保断流不被误认为正常结束
- [ ] 4.3 测试：开流前失败返回统一错误体且未产出任何 CSV 字节
- [ ] 4.4 后台任务补栈：`upload_api.py` 的 `_run_import_bg` 与 `_run_oss_import_bg` 两处
  `except Exception` 改为 `logger.exception(...)`（现状是 `logger.error(..., exc)`，
  丢失 traceback，而全局 handler 不拦截 BackgroundTasks，这是该路径唯一的线索来源）
- [ ] 4.5 测试：后台任务抛出异常时日志含完整栈，且异常不逃逸出任务

## 5. 前端契约对齐（BREAKING）

- [ ] 5.1 `src/app.tsx` 的 `errorConfig` 按新错误体取 `message` / `code`；保留 401 清 token
  跳登录；新增显式的 `skipErrorHandler` 选项支持
- [ ] 5.2 删除 `services/user.ts` 与 `services/upload.ts` 中两份 `unwrap` 及 `AmisEnvelope` 类型，
  返回类型收紧为真实数据形状
- [ ] 5.3 `services/analysis.ts` 的 `exportAnalysis` 改为解析统一错误体（当前是解析 `{detail}`），
  401 分支与全局出口对齐
- [ ] 5.4 登录页请求显式声明跳过统一提示（凭证错误由表单呈现），验证不回归到"静默吞掉"
- [ ] 5.5 `pages/User/index.tsx`、`pages/Analysis/index.tsx` 移除对 `unwrap` 抛错的隐式依赖；
  分析屏的作用域错误不再表现为无解释的空表格
- [ ] 5.6 前端测试：`pnpm test` 覆盖统一错误体的提示路径、`skipErrorHandler` 生效、
  用户屏失败有可见反馈（补充现有 26 条用例）

## 6. 文档与收尾

- [ ] 6.1 CLAUDE.md 增补「错误契约」小节：统一形状、状态码映射、流式端点规则、
  「静默必须显式」的约定
- [ ] 6.2 更新 `migrate-frontend-to-antd-pro` 的 `admin-spa` delta spec，把「失败响应可被
  前端统一识别」场景收窄为本变更的口径，避免归档时把旧语义 sync 回主 specs
- [ ] 6.3 全量验证：`uv run pytest test/`（含 `TEST_DATABASE_URL` 集成）+ 前端
  lint / tsc / test / build 四关
- [ ] 6.4 人工走查五条失败路径在界面上的真实表现：创建重名用户、导出超限、
  非法站点查询、令牌过期、表单必填校验失败（确认不回显所填内容）
- [ ] 6.5 复核审计清单逐项转为 Pass：异常基类、五类 handler 覆盖、四类错误形状一致、
  request id 贯穿、4xx/5xx 分级、数据库异常独立分类、后台任务带栈、无 `debug=` 传入
