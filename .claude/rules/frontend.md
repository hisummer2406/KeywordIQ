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
- 分析三个 hook 不互相 import，由 index 传值/回调协调；展示组件受控、不自行发请求。
- 默认工厂共用；草稿与生效条件分离，导出/数量/加入词库跟已加载作用域，不能跟未应用的草稿。
- 首屏初始化用 reloadScopeMeta 返回值，恢复类目传 keepCategories；不能用尚未落位的 React state 组请求。
- 会话快照优先于按 userId 保存条件再到默认；快照 logout 清，保存条件跨 logout，缺 userId 不回退共享键。
- 保存生效条件，不存绝对日期/检索词；dirty 拒绝保存。改会话快照形状提升 `CACHE_VERSION`，改保存条件形状提升 `SAVED_MODE_VERSION`（均在 Analysis/index.tsx），旧 payload 不迁移，按规格丢弃。
- 恢复快照只短路首次初始化，立即查询/翻页/排序仍请求；测试前 clearSnapshot，滚动恢复等结果 DOM。
- 滚动位置靠 scroll 监听，不在卸载 cleanup 才读；重置范围和跨账号缓存不凭直觉修改。
- competitorNew 在草稿初值、首屏初始化和 apply 各按当前权限规范化；读私有数据同时按后端权限限制。
- 新筛选同步 request_has_filters/FILTER_KEYS 与计数/导出；缺失、未查询、真零与权限失败分别呈现。
- 关键词模式/品牌排除/黑名单/7日变化率按 site-scoped-query 与 analysis-screen-layout 的契约，不在 UI 再造公式。
- ASIN 前端正则仅路由启发，不作后端格式限制（ISBN-10 也可能是 ASIN）。
- 份额为 0～100 百分数；排名变化原始负号代表上升，展示不翻转符号。
- 占位入口不造业务值或空权限点；真实端点接入时补后端权限并替换占位 access。
- 修改令牌清理、登录跳转另读 auth 规则；不能只靠全局错误处理器覆盖裸 fetch。
