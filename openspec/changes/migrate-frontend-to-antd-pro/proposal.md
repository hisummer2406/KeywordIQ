## Why

现有后台是 amis 低代码（`fastapi-amis-admin`）——整套界面用 Python 生成 JSON schema
（分析查询 ~306 行、用户管理 ~328 行、上传 ~115 行、外壳 ~59 行），交互与自定义能力受
amis 表达力约束，OSS 直传这类需要浏览器 SDK 的场景在 amis 里只能靠注入 JS 硬凑。战略上决定
迁移到真正可自定义的 React 前端（Ant Design Pro：React + Umi + Ant Design + ProComponents），
换取组件生态、自定义 UX、前端工程化与可测试性，并让 OSS 直传等能力原生落地。

这是一次**前端技术栈更替**（re-platform），不改变后端的业务语义。影响后台全部界面；上传相关
的**日报与周报两条路径**都随之改由新前端触发（沿用既有 `/api/upload/oss/*` 契约）。

## What Changes

- **新增独立 React 前端**（Ant Design Pro / Umi Max，TypeScript），作为后台的单一 SPA，取代
  amis 渲染的全部页面。分阶段迁移，迁移期与 amis 并存直至切换完成。
- **后端从"渲染 UI"退回"只提供 JSON API"**：移除 `fastapi-amis-admin` 的页面渲染职责，
  FastAPI 专注 REST API + 托管 SPA 静态产物（或交 nginx）。
- **API 响应契约调整**：列表类接口从 amis 形状（`{status,msg,data}`、`source:${items}`）
  改为 Ant Design `ProTable` 约定（`{data,total,success}`）——通过统一响应适配层实现，避免逐接口散改。
- **登录并入 SPA + 鉴权模型一并替换**：撤 `/admin/login`，登录成为 SPA 路由；`fastapi-user-auth`
  的角色/权限模型替换为项目自有的用户/角色/权限 + JWT 签发（一条独立后端工作流，非仅换前端）。
  前端用 Umi `request` 拦截器注入 token、401 跳转，`access` 控制路由/菜单。
- **OSS 直传在新栈原生实现**：Ant Design `Upload` 的 `customRequest` + `ali-oss`（npm）驱动
  STS 分片直传，替代 amis 注入 JS 的临时做法。**这条取代 `add-oss-first-ingestion` 里未做的前端 6.1。**
- **生产用 nginx 托管前端 + 反代 `/api`**：nginx 服务 SPA 产物（缓存/gzip）+ history 回退，反代
  `/api` 到 uvicorn；docker-compose 新增 nginx 服务，CI 增前端构建阶段。
- **直接重构、不维持双入口**：不搞 amis/SPA 线上并行灰度，逐屏在 staging 验证后一次性切换，
  回滚以整体部署回退为单位。
- **BREAKING**：后台访问路径与页面形态改变（SPA 取代 amis 页面）、登录入口迁移、鉴权模型替换；
  amis 相关 Python 组件（`*_admin.py` / `*_component.py` / `admin_site.py`）与 `fastapi-user-auth`
  的 UI 部分在迁移完成后删除。

## Capabilities

### New Capabilities
- `admin-spa`: 后台以独立 React SPA（Ant Design Pro）交付，后端只提供 JSON API 并托管静态产物；
  统一鉴权、统一列表响应契约、前端路由与权限；OSS 直传的前端由本能力原生承载。

### Modified Capabilities
<!-- 不修改 oss-first-ingestion 的既有要求：其后端契约（/api/upload/oss/*）保持不变，本变更只在
     admin-spa 能力下"实现其被推迟的前端"。且该能力当前仍是 add-oss-first-ingestion 的待同步 delta，
     未进主 specs，不宜在此放 delta。 -->
- （无。OSS 直传的前端以 `admin-spa` 的新要求承载，见 specs/admin-spa。）

## Impact

- **影响路径**：后台全部界面；上传的**日报与周报两条路径**都改由新前端触发。
- **新增**：`frontend/` React 工程（Umi Max + AntD Pro + TS）、前端构建产物托管、Node/pnpm 工具链。
- **后端**：`main.py`（移除 amis 页面挂载）、`app/admin_site.py` 与各 `*_admin.py`/`upload_component.py`
  （迁移完成后删除）、分析/用户/上传各 API 增加响应适配层。
- **鉴权（工作量抬升）**：替换 `fastapi-user-auth` 的角色/权限模型——新增/扩展自有用户/角色/权限存储、
  登录与令牌接口、权限判定（`app/auth/*`、`app/user/*`）；需保证既有用户可继续登录（密码哈希沿用或迁移）。
- **依赖**：新增前端 npm 依赖（antd、@ant-design/pro-components、ali-oss、umi 等）；Python 侧收尾移除
  `fastapi-amis-admin` 与 `fastapi-user-auth`。
- **部署**：docker-compose 新增 nginx（托管前端 + 反代 `/api`）；CI 增前端构建。同源无额外 CORS。
- **迁移策略**：直接重构，不维持双入口；逐屏 staging 验证后一次性切换，回滚以整体部署回退为单位
  （放弃了页面级并行灰度的安全垫——核心屏切换前须做等价性核对，见 design 决策 6）。
