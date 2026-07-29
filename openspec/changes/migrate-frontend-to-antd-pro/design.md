## Context

现状：后台整套 UI 是 amis（`fastapi-amis-admin`），Python 生成 JSON schema，服务端渲染。
屏幕：分析查询（核心产品，`table_component.py` ~306 行）、用户管理（~328 行）、上传（~115 行）、
外壳/鉴权（`admin_site.py` ~240 行）。API 返回 amis 形状（`{status,msg,data}`、`source:${items}`）。
鉴权：JWT 存 localStorage，`AdminAuthMiddleware` 从 Cookie/Authorization 校验；`/api/upload` 白名单。
仓库**无任何 JS 构建**。OSS-first 后端（`/api/upload/oss/*`）已就绪，仅差前端（`add-oss-first-ingestion`
的 6.1）。

目标：迁到 Ant Design Pro（Umi Max + AntD + ProComponents，TS），后端退回"纯 JSON API + 托管 SPA"。
这是 re-platform，不改业务语义。核心约束：迁移期分析查询不能停摆，必须 amis 与新 SPA 并行到逐屏切完。

## Goals / Non-Goals

**Goals:**
- 独立 React SPA 承载全部后台界面，后端只提供 JSON API 并托管静态产物。
- 列表接口统一为 ProTable 契约 `{data,total,success}`（经适配层，不逐接口散改）。
- 复用现有 JWT；前端统一 token 注入与 401 跳转。
- OSS 直传在新栈用 `Upload + ali-oss` 原生实现，后端契约不变。
- 分阶段、可回退地迁移，核心屏零停摆。

**Non-Goals:**
- 不改后端业务语义、数据模型、导入内核、OSS 后端契约。
- 不做 SSR/同构；SPA 即可（后台系统无 SEO 需求）。
- 不在本变更引入微前端/多包架构；单一 Umi 工程足够。
- 不重设计交互/视觉规范（先等价迁移，再谈增强）。

## Decisions

**决策 1：技术选型 Umi Max + Ant Design Pro（TS），包管 pnpm。**
- Umi Max 内置 `request`（拦截器/错误处理）、`access`（权限）、`initialState`（当前用户）、约定式路由，
  与 ProTable/ProForm 契合，省去自搭。
- 备选 Vite + 纯 antd。否决——ProTable/权限/请求要自己拼，迁移成本更高、收益更小。

**决策 2（已定）：生产用 nginx 托管前端 + 反代 `/api` 到 FastAPI。**
- nginx 直接服务 SPA 静态产物（gzip/缓存/长缓存哈希文件名）、对非资源路径回退 `index.html`
  （HTML5 history 深链接），并把 `/api` 反代到 uvicorn。同源，无额外 CORS。
- 本地开发用 Umi devServer proxy 到后端；生产由 docker-compose 新增 nginx 服务承担。
- （原备选"FastAPI StaticFiles 托管"改为仅本地/简易环境的退路；生产以 nginx 为准，换取性能与缓存。）

**决策 3：列表响应用"适配层"统一成 `{data,total,success}`，不逐接口散改。**
- 加一个响应包装（依赖/装饰器或统一 response model），把现有返回整形为 ProTable 契约；
  amis 专用字段（`items`/`status:0`）在迁移完成后清理。
- 备选逐接口改写。否决——散改易漏、回归面大。

**决策 4（已定，2026-07-29 依代码现实修订）：登录并入 SPA 路由；在现有自建鉴权上扩出 RBAC。**

> **现实纠正**：立项时以为鉴权是 `fastapi-user-auth` 的角色模型、需整体替换。核查代码后确认
> **JSON API 的鉴权早已完全自建，未用 `fastapi-user-auth`**：自建 `UserCenter` 模型
> （`user_center` 表，字段 `user_name`/`hashed_pwd`/`is_active`/`is_super`）、`passlib` bcrypt
> 密码、自建 `AuthService`（PyJWT / HS256 / `ADMIN_SECRET_KEY`）、`SimpleAuth` +
> `AdminAuthMiddleware`。`fastapi-user-auth`/`fastapi-amis-admin` 仅存在于 3 个 amis 文件
> （`admin_site.py`、`user_admin.py`、`analysis_admin.py`），是 amis 界面外壳，阶段七随之删除。
> 故本阶段**不是"替换 fastapi-user-auth"**，而是在自建鉴权基础上**扩出角色/权限体系**。

- **登录归 SPA**：不再保留 `/admin/login`，登录界面是 SPA 的一个路由。
- **契约用干净 REST（已定）**：规范化 `POST /api/auth/login → {token, user:{id,username,role,...}}`、
  新增 `GET /api/auth/me → CurrentUser`、`POST /api/auth/logout`；`is_super`/角色映射为 `role`。
  现有 amis 信封（`{status,msg,data}`）在收尾阶段随 amis 一并清理。前端 Umi `request` 拦截器注入
  `Authorization`，401 → 清 token、跳登录路由（前端脚手架阶段一已按此形态搭好）。
- **RBAC 自建（已定）**：现实只有 `is_super` 两级，用户选择**现在就建真正的角色/权限体系**
  （非沿用两级）。新增角色/权限存储 + 角色-权限映射，JWT 承载角色，后端以权限依赖
  （替代 `get_super_user` 的 `is_super` 硬判断）做逐路由授权。具体角色与权限矩阵见决策 4b。
- **密码零迁移（已定）**：现有密码已是 `passlib` bcrypt，既有用户直接可登，不重置、不换算法。
- **迁移映射**：既有 `is_super=True` → `admin` 角色；`is_super=False` → 默认角色（见 4b）。
  `is_super` 列在迁移期保留（amis 外壳仍读它），阶段七删 amis 时一并清理。
- 备选：沿用 `is_super` 两级不建 RBAC。已被否决（用户选择"现在就建"）——换取多角色与细粒度权限，
  代价是新增角色/权限表 + Alembic 迁移 + 逐路由授权改造。

**决策 4b（已定）：RBAC 的角色与权限矩阵。**

- **权限点（5 个，`resource:action`）**：`analysis:view`、`analysis:export`、`upload:import`、
  `user:manage`、`role:manage`。
- **角色（3 个）与矩阵**：

  | 角色 | view | export | import | user:manage | role:manage |
  |---|:-:|:-:|:-:|:-:|:-:|
  | `admin` | ✓ | ✓ | ✓ | ✓ | ✓ |
  | `operator` | ✓ | ✓ | ✓ | ✗ | ✗ |
  | `analyst` | ✓ | ✓ | ✗ | ✗ | ✗ |

- **数据模型**：`role`（id, code 唯一, name, is_system）、`permission`（id, code 唯一, name）、
  `role_permission`（M2M）；`user_center` 加 `role_id` FK → `role`。**每用户一个角色**；角色↔权限
  M2M，admin 可改角色的权限集。三张新表与既有 `user_center` 同 schema（见实现时对 `database.Base`
  默认 schema 的核对）。
- **JWT 与授权**：令牌承载 `role`（并暂留 `is_super` 供 amis）；权限在请求时由用户所属角色解析
  （中间件已从 DB 载用户，顺带 eager-load role+permissions），逐路由用 `require_permission("upload:import")`
  依赖替代 `is_super` 硬判断。不把权限码塞进 JWT，避免 admin 改权限后令牌陈旧。
- **迁移回填**：`is_super=True` → `admin`；`is_super=False` → **`operator`**（保持"任意登录用户当前即可上传"
  的现状，避免把老用户锁在上传之外；若核实上传端点本就限超管，则回填 `analyst`——以实现期对上传端点
  鉴权的核对为准）。种子数据（3 角色 + 5 权限 + 映射）随迁移写入。
- **amis 兼容**：迁移期 amis 仍是线上 UI，`is_super` 列与既有 amis 鉴权路由保持不变，阶段七删 amis
  时一并清理。

**决策 5：OSS 直传用 `Upload customRequest` + `ali-oss`（npm）。**
- `customRequest`：先 `GET/POST /api/upload/oss/upload-credentials` 拿 STS + object_key →
  `new OSS({region,accessKeyId,accessKeySecret,stsToken,bucket}).multipartUpload(key,file,{progress})`
  → 成功后 `POST /api/upload/oss/import` → 复用 `/processing-status` 轮询。
- npm 装 `ali-oss`，无自托管/CSP 顾虑（SPA 自带打包）。这条即 `admin-spa` 的直传要求落地。

**决策 6（已定）：直接重构，不维持 amis/SPA 双入口并行；逐屏在 staging 验证后一次性切换。**
- 用户选择"不考虑原入口，直接重构"——不搞两套 UI 并行灰度、不留特性开关切换入口。仍按屏推进
  （脚手架+鉴权 → 分析查询 → 上传 → 用户管理 → 收尾），但每屏是**在 staging 校验通过后一次性切到
  新前端**，而非线上双跑。
- **代价（如实记录）**：放弃了原先"核心屏零停摆·线上并行灰度可即时切回"的安全垫。缓解：分析查询
  这类核心屏切换前，用 staging + 生产数据样本做等价性核对（行数、筛选、导出一致），并安排一个可回退
  的发布窗口（保留上一个可部署版本，出问题整体回滚，而非页面级切回）。

**决策 7（已定）：导出下载鉴权——中小导出 fetch 取 blob，大导出走短时下载令牌。**
- SPA 不能像 `<a href>` 那样天然带 `Authorization`。默认：前端用带 token 的 `fetch` 拉 `StreamingResponse`
  为 blob，再用 `URL.createObjectURL` 触发浏览器下载——适用中小导出。
- 超过规模阈值的大导出：后端签发一次性、短时效的下载令牌（URL 携带），避免把整个大响应先读进浏览器内存。
- 备选：改用 Cookie 承载令牌让原生下载带凭证。否决——与现有 localStorage + Bearer 的鉴权模型不一致，
  且引入 CSRF 面。

## Risks / Trade-offs

- [团队 React/TS 能力断层] → 选 Umi Max 降低自搭成本；先做外壳+一屏打样统一范式，再复制推进；配套最小脚手架文档。
- [核心分析屏迁移期停摆风险] → 并行策略：新 SPA 与 amis 共存，分析屏切换前 amis 保持可用，灰度后再撤 amis。
- [API 契约整形引入回归] → 用统一适配层而非散改；对已有 API 加契约测试（`{data,total,success}` 形状）保护。
- [部署链新增 Node 构建] → docker 多阶段构建（node 阶段产出静态 → 拷进 Python 镜像）；CI 增前端构建与产物缓存。
- [直传所需 OSS 侧配置未就绪] → bucket CORS（放行后台域名 PUT/POST、expose ETag）与 STS 角色是硬前置；未就绪时上传屏可暂留 amis 或后端中转过渡。
- [amis/新栈双份鉴权注入不一致] → 迁移期两处拦截器都指向同一 JWT 语义，收尾统一到 SPA 后删除 amis 注入。

## Migration Plan

直接重构、逐屏在 staging 验证后一次性切换；每阶段整体可回滚（保留上一个可部署版本）：

1. **脚手架 + nginx 托管**：建 `frontend/`（Umi Max + AntD Pro + TS）；docker-compose 新增 nginx
   服务（服务 SPA 产物 + history 回退 + 反代 `/api` 到 uvicorn）；docker 多阶段构建 + CI 前端阶段。
2. **鉴权替换（后端 + 前端）**：自建用户/角色/权限模型 + JWT 登录/令牌接口，替换 `fastapi-user-auth`；
   登录并入 SPA 路由；`request` 拦截器注入 token、401 跳转；`access` 按角色控制路由/菜单。保证既有
   用户可继续登录。
3. **响应适配层**：后端加统一 `{data,total,success}` 包装；补契约测试。
4. **分析查询屏**（核心先行）：ProTable 复刻筛选/分页/类目统计/导出，接适配后的 `/search` 等接口；
   staging 用生产样本做等价性核对通过后切换。
5. **上传屏 + OSS 直传**：`Upload customRequest` + `ali-oss` 落地 STS 分片直传，接 `/oss/*`；
   前置：bucket CORS + STS 角色。删除 amis 注入 JS 方案。
6. **用户管理屏**：ProTable/ProForm 复刻增删改查与角色，接自建鉴权模型的后端。
7. **收尾**：撤下 amis 页面挂载与 `fastapi-user-auth` UI，删除 `*_admin.py`/`*_component.py`/
   `admin_site.py`；评估移除 `fastapi-amis-admin` 依赖。
8. **回滚粒度**：直接重构不留页面级切回，回滚以"整体部署回退到上一个可部署版本"为单位——
   故每阶段发布前必须保留可回退的上一版本与发布窗口。

## Open Questions

已拍板（见对应决策）：
- ✅ 登录并入新 SPA 路由，撤 `/admin/login`（决策 4）。
- ✅ `fastapi-user-auth` 角色/权限模型一并替换为自有实现（决策 4）。
- ✅ 生产用 nginx 托管前端 + 反代 `/api`（决策 2）。
- ✅ 不维持双入口，直接重构、逐屏 staging 验证后切换（决策 6）。
- ✅ **导出下载鉴权（决策 7）**：中小导出用带 token 的 `fetch` 取 blob 再触发下载；大导出用后端
  签发短时一次性下载令牌走 URL。两者按导出规模择一，默认前者、超阈值走后者。

待定（实施阶段细化，不阻塞立项）：
- 自建鉴权的角色/权限粒度沿用现有几个角色，还是借迁移收敛/细化？
- 既有用户密码哈希：沿用现算法直接迁移，还是首次登录时重置/升级？
- 导出走"下载令牌"的规模阈值取值（多大算大导出）。
