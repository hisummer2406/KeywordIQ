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
  // 注意：这里的 layout 配置**只有 title / locale / formatMessage 三个键会生效**。
  // Umi Max 生成的 plugin-layout 只从这份 userConfig 里取这三个，其余键不会传给
  // ProLayout（生成代码里 ProLayout 展开的是 src/app.tsx 导出的运行时配置）。
  // 所以布局形态（layout / contentWidth / fixedHeader 等）必须写在 src/app.tsx，
  // 写在这里是死配置、不报错也不生效。
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
