## 1. 脚手架与 nginx 托管（阶段一）

- [x] 1.1 建 `frontend/` 工程：Umi Max + Ant Design Pro + TypeScript，pnpm 管理
- [x] 1.2 约定式路由与基础布局（ProLayout），环境变量区分本地/生产 API base；本地 devServer proxy 到后端
- [x] 1.3 nginx 托管 SPA 产物（gzip/长缓存哈希文件名）+ 非资源路径回退 `index.html`（history）+ 反代 `/api` 到 uvicorn —— **部署形态已定为宿主机 nginx + uvicorn，不走容器**（`docker-compose.yml`/`Dockerfile` 早在 `9c0a31d` 即已删除）。落在 `etc/keywordiq-nginx.conf`：root 指向 SPA 产物目录、history 回退、`/api` 与 `/health` 反代 `127.0.0.1:8000`、哈希资源长缓存、gzip；`client_max_body_size` 由 3G 收到 100M（GB 级文件直传 OSS 不经 nginx）
- [x] 1.4 CI 增前端构建阶段 —— `.github/workflows/ci.yml` 的 `frontend` job：pnpm 9 + Node 20 + lockfile 缓存，跑 `pnpm lint` / `pnpm tsc` / `pnpm build`，三项均已本地验证通过。**注**：容器多阶段构建（`frontend/Dockerfile`、`frontend/nginx.conf`）随宿主机路线定案后已删除，避免与 `etc/keywordiq-nginx.conf` 双份维护

## 2. 鉴权替换：后端模型 + 前端接管（阶段二）

> **范围修订（2026-07-29）**：核查代码后确认鉴权早已完全自建（非 fastapi-user-auth），故本阶段
> 为"在自建 `UserCenter` 上扩出 RBAC"，非"替换 fastapi-user-auth"。角色/权限矩阵见 design 决策 4/4b。

- [x] 2.1 RBAC 模型 + Alembic 迁移（`analysis` schema）：新增 `rbac_model.py`（Role/Permission/role_permission），`user_center` 加 `role_id` FK；迁移 `c2d3e4f5a6b7` 建表 + 种子（5 权限 + admin/operator/analyst 3 角色 + 映射）+ 回填（is_super→admin / 否则→operator）。已 `alembic upgrade --sql` 离线核对 DDL。**注意：尚未对任何库执行 `alembic upgrade head`——运行时生效前必须先迁移**
- [x] 2.2 登录与令牌接口（干净 REST）：`POST /api/auth/token`→`{token,user}`、`GET /api/auth/me`、`POST /api/auth/logout`；JWT 承载 role；amis 信封 `/login`+`/profile` 迁移期保留。密码沿用 `passlib` bcrypt，既有用户零迁移。修正 `login_auth.py` 的 `jwt.JWTError`→`jwt.PyJWTError`（PyJWT 无前者，畸形令牌会崩）
- [x] 2.3 权限判定：`simple_auth.require_permission(code)` 依赖工厂（is_super 兜底），已挂到用户端点（`user:manage`）与上传导入端点（`upload:import`）；`AdminAuthMiddleware` 做 authN 不变，逐路由 authZ 用权限依赖。9 个单元测试覆盖判权/序列化/JWT/回归，全绿
- [x] 2.4 登录并入 SPA 路由（`/login`）；`request` 拦截器注入 `Authorization`、401 清 token 跳登录（阶段一已搭）；service 指向 `/api/auth/token`
- [x] 2.5 `getInitialState` 拉 `/api/auth/me`；`access.ts` 按权限码派生（canViewAnalysis/canUpload/canManageUsers/canAdmin），菜单按权限显隐

## 3. 后端响应适配层（阶段三）

- [x] 3.1 统一列表响应适配为 ProTable 契约 `{data,total,success}`：新增 `app/common/responses.py`（`ProTableResponse[T]` 泛型模型 + `protable(items,total)` helper）；已落地到 `GET /api/user/table`（复用现成 `get_users_paginated`，`user:manage` 授权）。阶段四/六的 SPA 列表端点直接 `return protable(...)`，amis 旧端点留阶段七清理
- [x] 3.2 失败响应统一形状：`protable_error(msg)` → `{success:false, data:[], total:0, message}`；已用于 `/api/user/table` 异常分支
- [x] 3.3 契约测试：`test/test_protable_contract.py`（5 项，无需 DB）——适配层成功/失败形状 + 端点 TestClient 契约（断言 `{data,total,success}`、不残留 `items`/`status`）。为跑 TestClient 新增 dev 依赖 `httpx<0.28`（钉版本以兼容仓库内 starlette 0.27）

## 4. 分析查询屏（阶段四，核心先行）

- [x] 4.1 用 ProTable 复刻分析查询（`pages/Analysis`）：作用域「站点→视图→日期」选择器 + ProTable 筛选（关键词/品牌/ASIN/标题子串、新词）/排序（排名/变化/份额）/分页，接 A 期 `POST /api/analysis/search`（作用域用 ref 承载供 request 闭包读取，切换回第 1 页）
- [x] 4.2 类目下拉/筛选：类目列 select 接 `GET /api/analysis/categories`（选项带计数），映射为 `category_exact` 精确筛选；日期选择器接 `GET /api/analysis/scope`
- [ ] 4.3 导出下载：中小导出用带 token 的 `fetch` 取 blob → `createObjectURL` 触发下载；大导出后端签发短时一次性下载令牌走 URL（阈值实施期定）—— 待做（后端 export 端点也需重建）
- [ ] 4.4 staging 用生产样本做等价性核对（行数/筛选/导出一致），通过后切换为线上前端 —— 待上线环境

## 5. 上传屏 + OSS 直传（阶段五）

- [ ] 5.1 前置核对：bucket CORS（放行后台域名 PUT/POST、expose ETag）与 STS 角色就绪（OSS 控制台侧）—— **外部**：OSS 控制台侧配置，非代码，须上线前在阿里云侧核对（区域/region 形式、CORS、RAM 角色）
- [x] 5.2 上传界面（日报/周报）：`frontend/src/pages/Upload`——站点下拉（接后端新增 `GET /api/upload/sites`，站点码源自 `site_registry`，不硬编码）+ 周期选择（按文件名推断/日报/周报）+ `Upload.Dragger` `customRequest`
- [x] 5.3 `customRequest` 流程：`frontend/src/services/ossUpload.ts`——换 STS 凭证（`/oss/upload-credentials`）→ `ali-oss` `multipartUpload` 分片直传 → 回传 object key（`/oss/import`）；`services/upload.ts` 封装接口并解包 amis 信封
- [x] 5.4 分片进度展示（`multipartUpload` progress → Upload 进度）+ 导入状态轮询（每 5s 拉 `/processing-status` 渲染最近批次表）
- [x] 5.5 移除 amis 直传注入 JS：新 SPA 为全新工程，不携带 amis 时期注入的 JS 方案；amis 上传屏本身随 amis 于阶段七整体撤下

## 6. 用户管理屏（阶段六）

- [x] 6.1 用 ProTable/ProForm 复刻用户增删改查与角色分配：前端 `pages/User`（ProTable 列表 + ModalForm 增改 + 角色下拉分配 + 启用/禁用）；后端补 `GET /api/user/roles`（角色下拉，user:manage）与 `/api/user/table` 列表带 `role_id/role_code`。端到端已验：创建/更新/改角色/禁用全通，operator 访问 `/api/user/table` 被 RBAC 拦为 403

## 7. 收尾与清理（阶段七）

- [x] 7.1 撤下 amis 挂载（main.py 移除 site.mount_app / login_admin / 根跳转），删除 `admin_site.py`、`login_admin.py`、`analysis_admin.py`、`user_admin.py`、`table_component.py`、`upload_component.py`；移除 amis 信封 `/api/auth/login`+`/profile`；中间件清 amis 路径
- [x] 7.2 `uv remove fastapi-amis-admin fastapi-user-auth`（连带 casbin/sqlmodel 等 8 个传递依赖）；app 导入 + 124 测试 + 真机启动全绿，旧 amis 端点 404
- [x] 7.3 前端工程文档 `frontend/README.md`（本地开发、脚本、目录约定、环境变量、nginx 部署）
- [x] 7.4 更新 `CLAUDE.md`：鉴权改自建 JWT+RBAC 描述、后台为独立 AntD Pro SPA、本服务只提供 JSON API

## 8. 前端测试与质量

- [ ] 8.1 前端关键流程测试（登录/鉴权跳转、分析查询表格、上传 `customRequest` 逻辑，OSS SDK 用桩）
- [x] 8.2 前端 lint/类型检查接入 CI —— `.github/workflows/ci.yml` `frontend` job 跑 `pnpm lint` + `pnpm tsc`（另有 `backend` job 跑 alembic 迁移/回滚 + pytest，起 postgres:15 service 并解开 `TEST_DATABASE_URL` gate）。**注**：backend job 的建库/迁移路径本地无 PG 客户端无法预验，待首次 CI 运行确认

## 9. 发布与回滚

- [ ] 9.1 每阶段发布保留上一个可部署版本与回退窗口（直接重构无页面级切回，回滚以整体部署回退为单位）
