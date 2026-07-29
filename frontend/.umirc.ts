import { defineConfig } from '@umijs/max';

/**
 * Umi Max 配置。
 * - 生产由 nginx 同源托管 SPA 产物并反代 /api，故 API base 走相对路径（见 src/app.tsx request.baseURL）。
 * - 本地开发由 devServer proxy 把 /api 转到后端 uvicorn（默认 127.0.0.1:8000，可用 UMI_APP_API_TARGET 覆盖）。
 */
export default defineConfig({
  npmClient: 'pnpm',
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
  layout: {
    title: '海眸系统',
    locale: false,
  },

  // 约定式 + 配置式路由：登录不套 ProLayout，其余屏进后台外壳
  routes: [
    { path: '/', redirect: '/analysis' },
    { name: '登录', path: '/login', component: './Login', layout: false },
    {
      name: '分析查询',
      path: '/analysis',
      component: './Analysis',
      icon: 'BarChart',
      access: 'canViewAnalysis',
    },
    {
      name: '数据上传',
      path: '/upload',
      component: './Upload',
      icon: 'CloudUpload',
      access: 'canUpload',
    },
    {
      name: '用户管理',
      path: '/user',
      component: './User',
      icon: 'Team',
      access: 'canManageUsers',
    },
    { path: '*', component: './404' },
  ],

  // 本地开发反代：/api -> 后端。生产环境由 nginx 承担，不走这里。
  proxy: {
    '/api': {
      target: process.env.UMI_APP_API_TARGET || 'http://127.0.0.1:8000',
      changeOrigin: true,
    },
  },

  // 生产用哈希文件名，配合 nginx 长缓存
  hash: true,
  history: { type: 'browser' },
});
