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
      name: 'ABA数据选品',
      path: '/analysis',
      component: './Analysis',
      icon: 'BarChart',
      access: 'canViewAnalysis',
    },
    {
      // 「工具箱」装的是不属于选品主线的独立工具，三个子屏走左侧二级菜单（同「数据上传」）。
      // 三者同属一个权限点 toolbox:view——外观专利与全球商标库尚无数据源、也没有后端端点，
      // 各给一个权限点等于加两个「勾了也不生效」的复选框（`user:manage` 踩过这个坑）。
      name: '工具箱',
      path: '/toolbox',
      icon: 'Appstore',
      access: 'canViewToolbox',
      routes: [
        { path: '/toolbox', redirect: '/toolbox/growth' },
        {
          name: '持续增长市场',
          path: '/toolbox/growth',
          component: './Toolbox/GrowthMarket',
          icon: 'RiseOutlined',
        },
        {
          // 词库与增长榜同属工具箱、同一个权限点（toolbox:view）。排在增长榜之后、
          // 两个占位屏之前：前两者有真实数据，占位屏还没有。
          name: '我的词库',
          path: '/toolbox/library',
          component: './Toolbox/KeywordLibrary',
          icon: 'Star',
        },
        // 下面两个是占位屏：仓库里没有任何专利/商标数据，页面明确写着「数据源尚未接入」，
        // 不发请求、无后端端点。菜单位置先占住，各自另立变更。
        {
          name: '外观专利',
          path: '/toolbox/patent',
          component: './Toolbox/DesignPatent',
          icon: 'FileProtect',
        },
        {
          name: '全球商标库',
          path: '/toolbox/trademark',
          component: './Toolbox/Trademark',
          icon: 'Trademark',
        },
      ],
    },
    {
      // 「数据上传」是一级菜单，两个子屏走左侧二级菜单（layout: 'mix' + splitMenus，
      // 见 src/app.tsx）。二者同属一个权限点 upload:import——能导入的人才需要查导入
      // 历史、才有资格重导，故 access 挂在父级即可，子项继承。
      //
      // 排在最后一位是有意的：它是三个一级菜单里唯一「喂数据」的屏，选品与工具箱
      // 都是「看数据」，夹在中间会把同类的两块拆开。顶栏顺序会被读成主线先后，
      // 支持功能压在末尾即可——**不要**改成藏进头像下拉（那是 `用户管理` 的位置，
      // 判据是低频管理动作）：上传是日常操作，还带一个要盯的进度看板和二级菜单。
      // 另注：ProLayout 横向菜单从 logo 往右顺排，「排最后」不等于贴右边缘，
      // 真右对齐要自己接管菜单渲染，不值得。
      name: '数据上传',
      path: '/upload',
      icon: 'CloudUpload',
      access: 'canUpload',
      routes: [
        // 旧地址 /upload 仍可用，重定向到上传屏，不让既有书签落空
        { path: '/upload', redirect: '/upload/file' },
        { name: '上传文件', path: '/upload/file', component: './Upload', icon: 'Upload' },
        { name: '导入历史', path: '/upload/history', component: './ImportHistory', icon: 'History' },
      ],
    },
    {
      // 不进顶栏菜单——入口在右上角头像的下拉里（见 components/RightContent）。
      // 用户管理是低频的管理动作，与「选品 / 上传」这两条日常主线不同层次，摆在
      // 顶栏与它们平级会让主线变长。
      //
      // hideInMenu 只影响菜单渲染，**路由与 access 守卫照常生效**：非超管直接敲
      // /user 仍会被挡回。菜单隐藏从来不是访问控制。
      name: '用户管理',
      path: '/user',
      component: './User',
      icon: 'Team',
      access: 'canManageUsers',
      hideInMenu: true,
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
