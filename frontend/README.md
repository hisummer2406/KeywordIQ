# KeywordIQ 前端（Ant Design Pro / Umi Max）

后台单页应用（SPA），取代原 amis 界面。技术栈：Umi Max + Ant Design + ProComponents + TypeScript，pnpm 管理。

## 本地开发

```bash
cd frontend
pnpm install          # 首次安装依赖（postinstall 自动 max setup 生成 src/.umi）
pnpm dev              # 启动 devServer，默认 http://localhost:8000
```

- devServer 把 `/api` 反代到后端 uvicorn（默认 `http://127.0.0.1:8000`）。
- 后端端口不同就在 `frontend/.env.local` 里覆盖 `UMI_APP_API_TARGET`（该文件不入库）。
- 后端需另起：仓库根目录 `uv run uvicorn main:app --reload --port 8000`。

## 常用脚本

| 命令 | 作用 |
|---|---|
| `pnpm dev` | 本地开发（HMR） |
| `pnpm build` | 生产构建，产物在 `dist/`（哈希文件名） |
| `pnpm tsc` | TypeScript 类型检查（不产出） |
| `pnpm lint` | ESLint |
| `pnpm format` | Prettier 格式化 |

## 目录约定

```
src/
├── app.tsx          # 运行时配置：getInitialState / layout / request 拦截器（token 注入、401 跳转）
├── access.ts        # 权限工厂（canAdmin 等），由 initialState.currentUser 派生
├── typings.d.ts     # 全局 API 类型（API.CurrentUser、API.TableResponse 等）
├── services/        # 后端接口封装（按域拆分）
└── pages/           # 屏：Login / Analysis / Upload / User / 404
```

## 环境变量

Umi 只把 `UMI_APP_` 前缀的变量暴露到 `process.env`。

| 变量 | 说明 | 默认 |
|---|---|---|
| `UMI_APP_API_BASE` | 请求 baseURL；生产同源反代故留空走相对路径 | `''` |
| `UMI_APP_API_TARGET` | 本地 devServer `/api` 反代目标 | `http://127.0.0.1:8000` |

入库的 `.env` 只放安全默认，个性化用 `.env.local`（不入库）。

## 生产部署

生产由 nginx 同源托管 SPA 产物 + 反代 `/api` 到 uvicorn（history 深链接回退 `index.html`）。
`nginx.conf` 与 `Dockerfile`（多阶段：node 构建 → nginx 承载）随工程提供。docker-compose 服务接线与 CI 见根目录部署配置（阶段一 1.3/1.4）。

## 迁移进度（openspec: migrate-frontend-to-antd-pro）

当前为**阶段一：脚手架**。各屏为占位，按阶段推进：
鉴权替换（二）→ 响应适配层（三）→ 分析查询（四）→ 上传+OSS 直传（五）→ 用户管理（六）→ 收尾（七）。
