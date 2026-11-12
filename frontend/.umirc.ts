import { defineConfig } from '@umijs/max';

import { routes } from './src/routes';

/**
 * Umi Max 配置。
 * - 生产由 nginx 同源托管 SPA 产物并反代 /api，故 API base 走相对路径（见 src/app.tsx request.baseURL）。
 * - 本地开发由 devServer proxy 把 /api 转到后端 uvicorn（默认 127.0.0.1:8000，可用 UMI_APP_API_TARGET 覆盖）。
 */
export default defineConfig({
  npmClient: 'pnpm',
  // antd 6 升级后，Umi 的 esbuild helper 检查报告各 chunk 的压缩 helper 同名冲突（antd 5 无此问题）。
  // 用 IIFE 包裹压缩产物消除冲突；不要关闭 esbuildHelperChecker（change upgrade-antd-v6 design D3）。
  esbuildMinifyIIFE: true,
  title: '海眸系统',

  // Umi Max 内置插件
  antd: {
    // 用 antd <App> 包裹，提供 message/notification 的上下文（App.useApp）
    appConfig: {},
  },
  access: {},
  model: {},
  initialState: {},
  request: {},
  // 注意：这里的 layout 配置**只有 title / locale / formatMessage 三个键会生效**。
  // Umi Max 生成的 plugin-layout 只从这份 userConfig 里取这三个，其余键不会传给
  // ProLayout（生成代码里 ProLayout 展开的是 src/app.tsx 导出的运行时配置）。
  // 所以布局形态（layout / contentWidth / fixedHeader 等）必须写在 src/app.tsx，
  // 写在这里是死配置、不报错也不生效。
  layout: {
    title: '海眸系统',
    locale: false,
  },

  // 路由与顶栏菜单抽在 src/routes.ts——那份能被测试直接 import，这里 import 不了
  // （经 @umijs/max 会拽进 esbuild，jsdom 下启动即崩）。见该文件头部注释。
  routes,

  // 本地开发反代：/api -> 后端。生产环境由 nginx 承担，不走这里。
  proxy: {
    '/api': {
      target: process.env.UMI_APP_API_TARGET || 'http://127.0.0.1:8000',
      changeOrigin: true,
    },
  },

  // 可安装为网站应用（issue #92）：manifest 与图标放在 public/，Umi 原样拷进 dist 根。
  // **href 必须以 / 开头**：入口页会在任意深链接（/analysis、/ip/review）下被加载，写成
  // 相对路径会解析成 /analysis/manifest.webmanifest，被 nginx 的 SPA 兜底当成 index.html
  // 返回——Manifest 静默解析失败，浏览器只是不提供安装，没有任何报错。
  // 这里**不新增 Service Worker**：本产品必须联网，安装只是入口，见 openspec installable-web-app。
  links: [
    { rel: 'manifest', href: '/manifest.webmanifest' },
    { rel: 'apple-touch-icon', href: '/apple-touch-icon.png' },
  ],
  metas: [{ name: 'theme-color', content: '#ffffff' }],

  // 生产用哈希文件名，配合 nginx 长缓存
  hash: true,
  history: { type: 'browser' },
});
