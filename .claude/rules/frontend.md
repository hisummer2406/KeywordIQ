---
paths:
  - "frontend/src/**/*"
  - "frontend/.umirc.ts"
  - "frontend/vitest.config.ts"
  - "frontend/src/test/**/*"
---

# 前端实现约束

- 先按 [前端导航](../../openspec/guides/frontend.md) 读相关规格；菜单/标签/查询时机不是指令文件中的功能清单。
- routes.ts 保持独立可测；单页宽表不加挤占空间的二级侧栏；分组管理按既有规格用纵向空间。
- 模块内互不相关的功能一律做左侧二级菜单（独立路由），不用页内 Tab；同一数据的筛选、机会中心阶段、市场分析单页除外（用户 2026-11-10 全系统约定，见 top-nav-structure）。
- 分析三个 hook 不互相 import，由 index 传值/回调协调；展示组件受控、不自行发请求。
- 默认工厂共用；草稿与生效条件分离，导出/数量/加入词库跟已加载作用域，不能跟未应用的草稿。
- 首屏初始化用 reloadScopeMeta 返回值，恢复类目传 keepCategories；不能用尚未落位的 React state 组请求。
- 会话快照优先于按 userId 保存条件再到默认；快照 logout 清，保存条件跨 logout，缺 userId 不回退共享键。
- 保存生效条件，不存绝对日期/检索词；dirty 拒绝保存。改会话快照形状提升 `CACHE_VERSION`，改保存条件形状提升 `SAVED_MODE_VERSION`（分别在 Analysis/stateCache.ts、savedMode.ts），旧 payload 不迁移，按规格丢弃。仅删除字段时在读取处剔除（savedMode 的 `RETIRED_FILTER_KEYS`），不提升版本，免得整份丢掉用户其余有效条件（来源 #166 design D4）。
- 恢复快照只短路首次初始化，立即查询/翻页/排序仍请求；测试前 clearSnapshot，滚动恢复等结果 DOM。
- 滚动位置靠 scroll 监听，不在卸载 cleanup 才读；重置范围和跨账号缓存不凭直觉修改。
- 新筛选同步 request_has_filters/FILTER_KEYS 与计数/导出；缺失、未查询、真零与权限失败分别呈现。
- 关键词模式/品牌排除/黑名单/7日变化率按 site-scoped-query 与 analysis-screen-layout 的契约，不在 UI 再造公式。
- 检索目标只由所选搜索方式决定，不按输入格式推断；ASIN 前后端都不做格式校验（ISBN-10 也可能是 ASIN）（#150）。
- 份额为 0～100 百分数；排名变化原始负号代表上升，展示不翻转符号。
- 占位入口不造业务值或空权限点；真实端点接入时补后端权限并替换占位 access。
- 修改令牌清理、登录跳转另读 auth 规则；不能只靠全局错误处理器覆盖裸 fetch。
