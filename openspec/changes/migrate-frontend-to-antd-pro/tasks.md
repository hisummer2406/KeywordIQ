## 1. 脚手架与 nginx 托管（阶段一）

- [x] 1.1 建 `frontend/` 工程：Umi Max + Ant Design Pro + TypeScript，pnpm 管理
- [x] 1.2 约定式路由与基础布局（ProLayout），环境变量区分本地/生产 API base；本地 devServer proxy 到后端
- [ ] 1.3 docker-compose 新增 nginx 服务：服务 SPA 产物（gzip/长缓存哈希文件名）+ 非资源路径回退 `index.html`（history）+ 反代 `/api` 到 uvicorn —— **部分**：SPA nginx 配置 `frontend/nginx.conf` 已产出（history 回退 + /api 反代 + 长缓存 + gzip）；compose 服务接线未做——仓库内无 `docker-compose.yml`（deploy.sh 引用但文件不在树里），且当前生产是宿主机 nginx（`etc/keywordiq-nginx.conf`）。待确认部署形态（容器化 compose vs 宿主 nginx）后接线
- [ ] 1.4 docker 多阶段构建（node 阶段产出静态 → 由 nginx 镜像/卷承载）；CI 增前端构建阶段与产物缓存 —— **部分**：多阶段 `frontend/Dockerfile`（node 构建 → nginx 承载）已产出并本地验证构建通过；CI 阶段未做——仓库无 `.github/workflows` 等 CI 配置。待确认 CI 平台后加前端构建阶段

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

- [ ] 4.1 用 ProTable 复刻分析查询：筛选、分页、排序，接适配后的 `/api/analysis/search`
- [ ] 4.2 类目统计下拉/筛选接 `/api/analysis/category-stats`
- [ ] 4.3 导出下载：中小导出用带 token 的 `fetch` 取 blob → `createObjectURL` 触发下载；大导出后端签发短时一次性下载令牌走 URL（阈值实施期定）
- [ ] 4.4 staging 用生产样本做等价性核对（行数/筛选/导出一致），通过后切换为线上前端

## 5. 上传屏 + OSS 直传（阶段五）

- [ ] 5.1 前置核对：bucket CORS（放行后台域名 PUT/POST、expose ETag）与 STS 角色就绪（OSS 控制台侧）
- [ ] 5.2 上传界面（日报/周报）：AntD `Upload` + `customRequest`
- [ ] 5.3 `customRequest` 流程：换 STS 凭证（`/oss/upload-credentials`）→ `ali-oss` 分片直传 → 回传 key（`/oss/import`）
- [ ] 5.4 分片进度展示 + 导入状态轮询（复用 `/processing-status`）
- [ ] 5.5 移除 amis 时期为直传注入的 JS 方案

## 6. 用户管理屏（阶段六）

- [ ] 6.1 用 ProTable/ProForm 复刻用户增删改查与角色分配，接阶段二自建鉴权模型的后端

## 7. 收尾与清理（阶段七）

- [ ] 7.1 撤下 amis 页面挂载，删除 `app/admin_site.py`、各 `*_admin.py`、`upload_component.py`
- [ ] 7.2 移除 `fastapi-amis-admin` 与 `fastapi-user-auth` 依赖（鉴权已自建）
- [ ] 7.3 前端最小工程文档（本地开发、构建、nginx 部署、目录约定）
- [ ] 7.4 更新 `CLAUDE.md`：前端为独立 AntD Pro SPA，nginx 托管 + 反代，后端只提供 JSON API，鉴权自建

## 8. 前端测试与质量

- [ ] 8.1 前端关键流程测试（登录/鉴权跳转、分析查询表格、上传 `customRequest` 逻辑，OSS SDK 用桩）
- [ ] 8.2 前端 lint/类型检查接入 CI

## 9. 发布与回滚

- [ ] 9.1 每阶段发布保留上一个可部署版本与回退窗口（直接重构无页面级切回，回滚以整体部署回退为单位）
