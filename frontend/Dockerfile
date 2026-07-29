# 多阶段构建：node 阶段产出 SPA 静态产物 → nginx 阶段承载 + 反代 /api。
# 从仓库根目录构建：docker build -f frontend/Dockerfile -t keywordiq-frontend frontend

# ---- 构建阶段 ----
FROM node:22-alpine AS build
WORKDIR /app

# 借助 pnpm（corepack 内置），先装依赖以利用层缓存
RUN corepack enable
COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install
COPY . .
RUN pnpm build

# ---- 运行阶段 ----
FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
