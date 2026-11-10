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
    // 承接"确定性 Signal → 候选机会 → 验证推进 → 风险核查"这条主动式工作流的入口
    // （issue #76）。**不叫"AI Agent"或"聊天助手"**——那是技术实现，不是用户任务。
    // 排最前是 issue 明确要求的：它是产品导航要演进去的方向，既有菜单相对顺序不变。
    //
    // 单页顶层（无子项），六个状态（待处理信号/候选机会/验证中/风险待核查/已通过/
    // 已否决）用页内 Tab 承载，不拆成带独立路径的二级路由——理由与"分析屏不长侧栏"
    // 同构：不为一个还没有真实数据支撑的信息架构预建路由层级。
    //
    // `opportunity:view` **不授予任何内置角色，默认只有 is_super 能看到**（design.md
    // 决策 1）——这是当前唯一一个"admin 角色也看不到"的权限点，与其余按角色矩阵授予
    // 的权限点不同档次。页面本身不发任何请求（design.md 决策 2/3）：机会中心的业务
    // 能力由其他 issue（#7 #13 #24 #25 #38 #54 #55）陆续接入，此刻只有骨架。
    name: '机会中心',
    path: '/opportunity',
    component: './Opportunity',
    icon: 'AimOutlined',
    access: 'canViewOpportunity',
  },
  {
    // 单页顶层（无子项），因此内容区全宽。**不要给它加二级菜单**：layout 是
    // mix + splitMenus，顶层节点一有子项就长出约 208px 的左侧栏，而这是全产品最宽的表
    // （指标列 + 走势小图 + 三个历史排名列）。按数据域，增长榜与词库本该并进这里，
    // 正是这条否决了它（design.md 决策 2）——横向裁掉的列找不回来。
    //
    // 原名「ABA数据选品」，issue #76 改名「关键词市场分析」——路由、组件、权限点均不变，
    // 只是换了个不那么依赖内部数据源缩写的名字。
    name: '市场分析',
    path: '/analysis',
    component: './Analysis',
    icon: 'BarChart',
    access: 'canViewAnalysis',
  },
  {
    // 两屏的共同点是**主题**（都是 ABA 关键词的透镜），不是「都不属于主线」。
    // 共用容器级权限点 keyword:view，不给两屏各开一个——词库管理的是用户自己的私有
    // 清单，「禁止某人管理自己的收藏夹」不是真实的授权需求。
    name: '词库工具',
    path: '/keyword',
    icon: 'Appstore',
    access: 'canViewKeyword',
    routes: [
      { path: '/keyword', redirect: '/keyword/growth' },
      {
        name: '增长市场',
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
    // 独立顶层菜单——competitor:view 是独立权限点(design.md 决策 6/8)。排在关键词工具之后、
    // 风险核查之前：机会中心之后的三项同属"看 ABA 与竞对数据"一类，风险核查次之，数据管理
    // 是"喂数据"排最后（见下方数据管理节点的注释）。
    //
    // **按运营任务拆两个子菜单**（#166 D1，top-nav-structure「竞对监控以任务菜单组织」）：
    // 商品发现看新品、店铺管理管关注对象。Amazon/Shopify 是来源类型，放在页内切换，不进菜单。
    // 有子项即长出可折叠的左侧栏——竞对两张表远窄于分析查询屏，这笔宽度付得起；这个例外
    // 不能拿来给分析查询屏或机会中心加子菜单。子屏继承父级 access，不新增逐页权限点。
    name: '竞对监控',
    path: '/competitor',
    icon: 'EyeOutlined',
    access: 'canViewCompetitor',
    routes: [
      { path: '/competitor', redirect: '/competitor/discovery' },
      {
        name: '商品发现',
        path: '/competitor/discovery',
        component: './Competitor/Discovery',
        icon: 'ShoppingOutlined',
      },
      {
        name: '店铺管理',
        path: '/competitor/stores',
        component: './Competitor/Stores',
        icon: 'ShopOutlined',
      },
    ],
  },
  {
    // 跨来源产品档案的独立入口；业务尚未接入，保持全宽。
    // 仅超管可见（canViewUnreleased），见 access.ts。
    name: '产品档案',
    path: '/products',
    component: './Products',
    icon: 'ProfileOutlined',
    access: 'canViewUnreleased',
  },
  {
    // 名字取**使用场景**（卖家嘴里说的是「查侵权」）而非数据域（「知识产权」）。
    // 原名「侵权查询」，issue #76 改名「风险核查」——为承接未来商标/专利之外的其他
    // 风险信号（如竞对监控的合规风险）留出更宽的名字，路由不变。
    //
    // 按任务组织、与定价核算同构：先「外观专利核查」（新建），再「核查记录」（列表 + 详情）。
    // 整个模块挂 `risk:view`；发起检索（计费）与签署意见另由 risk:search / risk:review 在页面
    // 按钮与后端逐端点把守。商标查询、风险案例两个空占位已移出菜单（用户 2026-11-11），
    // 真正接入数据源时再加回。详情是独立路由，不进菜单（刷新/书签可恢复）。
    // `/ip/review*` 是 #179 上线时的旧地址，保留重定向以免已收藏的链接失效。
    name: '风险核查',
    path: '/ip',
    icon: 'SafetyCertificate',
    access: 'canViewRisk',
    routes: [
      { path: '/ip', redirect: '/ip/patent' },
      { name: '外观专利核查', path: '/ip/patent', component: './IP/DesignPatent', icon: 'FileSearch' },
      { name: '核查记录', path: '/ip/records', component: './IP/Records', icon: 'History' },
      { path: '/ip/records/:id', component: './IP/RecordDetail', hideInMenu: true },
      { path: '/ip/review', redirect: '/ip/records', hideInMenu: true },
      { path: '/ip/review/:id', redirect: '/ip/records/:id', hideInMenu: true },
    ],
  },
  {
    // 快速核价、核价记录、费用配置是三个互不相关的功能，按全系统约定走左侧二级菜单
    // （各有独立地址），不做页内 Tab。整个模块只挂 pricing:view，子屏继承；
    // 费用配置的维护按钮另按 pricing:manage-rates 显示，后端逐端点再校验。
    name: '定价核算',
    path: '/pricing',
    icon: 'CalculatorOutlined',
    access: 'canViewPricing',
    routes: [
      { path: '/pricing', redirect: '/pricing/calculate' },
      { name: '快速核价', path: '/pricing/calculate', component: './Pricing/Calculate', icon: 'Calculator' },
      { name: '核价记录', path: '/pricing/records', component: './Pricing/Records', icon: 'History' },
      { name: '费用配置', path: '/pricing/rates', component: './Pricing/Rates', icon: 'Setting' },
    ],
  },
  // 旧「工具箱」地址不落空。只给有真实数据的两屏留重定向——没有人会去书签一个写着
  // 「数据源尚未接入」的屏，逐条保留只会让这份路由表越来越长。
  { path: '/toolbox', redirect: '/keyword/growth', hideInMenu: true },
  { path: '/toolbox/growth', redirect: '/keyword/growth', hideInMenu: true },
  { path: '/toolbox/library', redirect: '/keyword/library', hideInMenu: true },
  {
    // 「数据管理」是一级菜单，两个子屏走左侧二级菜单（layout: 'mix' + splitMenus，
    // 见 src/app.tsx）。二者同属一个权限点 upload:import——能导入的人才需要查导入
    // 历史、才有资格重导，故 access 挂在父级即可，子项继承。
    //
    // 原名「数据上传」，issue #76 改名「数据管理」——为将来接入 SP-API/第三方连接器
    // （届时不只是"上传文件"）预留语义空间，路由、组件、权限点都不变。
    //
    // 排在最后一位是有意的：它是八个一级菜单里唯一「喂数据/管数据」的屏，前面七项都是
    // 「看数据」，夹在中间会把同类的几块拆开。顶栏顺序会被读成主线先后，
    // 支持功能压在末尾即可——**不要**改成藏进头像下拉（那是 `用户管理` 的位置，
    // 判据是低频管理动作）：上传是日常操作，还带一个要盯的进度看板和二级菜单。
    // 另注：ProLayout 横向菜单从 logo 往右顺排，「排最后」不等于贴右边缘，
    // 真右对齐要自己接管菜单渲染，不值得。
    name: '数据管理',
    path: '/upload',
    icon: 'CloudUpload',
    access: 'canUpload',
    routes: [
      // 旧地址 /upload 仍可用，重定向到上传屏，不让既有书签落空
      { path: '/upload', redirect: '/upload/file' },
      { name: '文件导入', path: '/upload/file', component: './Upload', icon: 'Upload' },
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
