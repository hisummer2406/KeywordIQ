/**
 * 路由与顶栏菜单。
 *
 * **从 `.umirc.ts` 抽出来单独成文件，是为了让它可测。** 直接在测试里 import `.umirc.ts`
 * 会经 `@umijs/max` 拽进 esbuild，而 esbuild 在 jsdom 下启动即抛「your JavaScript
 * environment is broken」。菜单结构改错了不会有任何地方报错——顺序错了、单页顶层被加上
 * 子项、某项挂了不该有的 access，跑起来都"能用"，只是形态不对，而形态正是这一屏的全部
 * 内容。回归在 `src/routes.test.ts`。
 */
export const routes = [
  { path: '/', redirect: '/analysis' },
  { name: '登录', path: '/login', component: './Login', layout: false },
  {
    // 钉钉扫码后由钉钉 302 回这里。**不挂 access 守卫**：此刻还没有令牌，挂了就
    // 永远进不来。也不套 ProLayout——它是一个过场屏，不该出现顶栏与菜单。
    //
    // 重定向 URL 在钉钉开发者后台登记的必须是这条地址（SSO_REDIRECT_URI 与它一致），
    // 且 nginx 需让它走 SPA 的 history fallback（现有配置已经如此）。
    name: '钉钉登录回调',
    path: '/sso/callback',
    component: './SsoCallback',
    layout: false,
    hideInMenu: true,
  },
  // ↓↓↓ 一级菜单的归属判据：**按数据域（或使用场景）命名，不设按排除法命名的容器**。
  //
  // 这里曾有一个叫「工具箱」的一级菜单，定义是「不属于选品主线的其他东西」——名字说的是
  // 它不是什么。这种容器不给新功能提供归属判据：下一个功能只能问「它像不像已经在里面的
  // 那些」，而那个问题没有答案。它最后装了四个屏，其中两个读 ABA 事实表、两个读根本
  // 不存在的专利/商标库。竞对监控没有放进去，就是它已经没有原则的证据。
  //
  // 新增一级菜单前先问：它读哪个数据域？没有一项匹配就新开一个，别塞进泛化容器。
  {
    // 单页顶层（无子项），因此内容区全宽。**不要给它加二级菜单**：layout 是
    // mix + splitMenus，顶层节点一有子项就长出约 208px 的左侧栏，而这是全产品最宽的表
    // （指标列 + 走势小图 + 三个历史排名列）。按数据域，增长榜与词库本该并进这里，
    // 正是这条否决了它（design.md 决策 2）——横向裁掉的列找不回来。
    name: 'ABA数据选品',
    path: '/analysis',
    component: './Analysis',
    icon: 'BarChart',
    access: 'canViewAnalysis',
  },
  {
    // 两屏的共同点是**主题**（都是 ABA 关键词的透镜），不是「都不属于主线」。
    // 共用容器级权限点 keyword:view，不给两屏各开一个——词库管理的是用户自己的私有
    // 清单，「禁止某人管理自己的收藏夹」不是真实的授权需求。
    name: '关键词工具',
    path: '/keyword',
    icon: 'Appstore',
    access: 'canViewKeyword',
    routes: [
      { path: '/keyword', redirect: '/keyword/growth' },
      {
        name: '持续增长市场',
        path: '/keyword/growth',
        component: './Keyword/GrowthMarket',
        icon: 'RiseOutlined',
      },
      {
        name: '我的词库',
        path: '/keyword/library',
        component: './Keyword/KeywordLibrary',
        icon: 'Star',
      },
    ],
  },
  {
    // 独立顶层菜单——competitor:view 是独立权限点(design.md 决策 6/8)。同样是单页顶层
    // 模式，照 /analysis。排在关键词工具之后、侵权查询之前：前三项同属"看 ABA 与竞对
    // 数据"一类，风险查询次之，上传是"喂数据"排最后（见下方数据上传节点的注释）。
    name: '竞对监控',
    path: '/competitor',
    component: './Competitor',
    icon: 'EyeOutlined',
    access: 'canViewCompetitor',
  },
  {
    // 名字取**使用场景**（卖家嘴里说的是「查侵权」）而非数据域（「知识产权」）。
    //
    // **故意没有 access**，只要求已登录（外层 ProLayout 已保证）。两个子屏没有后端端点，
    // 罩着它们的权限点对它们不生效，就是「勾了也不生效」的复选框（`user:manage` 正是
    // 这样被删掉的）。接上数据源、有了真实端点再加权限点——那时是加法，不会留下失效的
    // 历史勾选。别为了「每个菜单都有 access」的整齐而补一个。
    //
    // 进顶栏是自觉的取舍：产品最空的一块因此成了最显眼的一块，换来的是「这块在做」这个
    // 路线图信号可见。两个屏自己把「数据源尚未接入」写在脸上，不发请求。
    name: '侵权查询',
    path: '/ip',
    icon: 'SafetyCertificate',
    routes: [
      { path: '/ip', redirect: '/ip/patent' },
      {
        name: '外观专利',
        path: '/ip/patent',
        component: './IP/DesignPatent',
        icon: 'FileProtect',
      },
      {
        name: '全球商标库',
        path: '/ip/trademark',
        component: './IP/Trademark',
        icon: 'Trademark',
      },
    ],
  },
  // 旧「工具箱」地址不落空。只给有真实数据的两屏留重定向——没有人会去书签一个写着
  // 「数据源尚未接入」的屏，逐条保留只会让这份路由表越来越长。
  { path: '/toolbox', redirect: '/keyword/growth', hideInMenu: true },
  { path: '/toolbox/growth', redirect: '/keyword/growth', hideInMenu: true },
  { path: '/toolbox/library', redirect: '/keyword/library', hideInMenu: true },
  {
    // 「数据上传」是一级菜单，两个子屏走左侧二级菜单（layout: 'mix' + splitMenus，
    // 见 src/app.tsx）。二者同属一个权限点 upload:import——能导入的人才需要查导入
    // 历史、才有资格重导，故 access 挂在父级即可，子项继承。
    //
    // 排在最后一位是有意的：它是五个一级菜单里唯一「喂数据」的屏，前面四项都是
    // 「看数据」，夹在中间会把同类的几块拆开。顶栏顺序会被读成主线先后，
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
];
