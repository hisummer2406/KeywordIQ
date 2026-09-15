## 1. 操作列文字入口

- [x] 1.1 `frontend/src/pages/Analysis/columns.tsx`：「操作」列新增「历史走势」文字入口，
      仅依赖 `onOpenTrend` 是否传入，不依赖站点码或该行是否有小图数据
- [x] 1.2 列宽 48→80，容纳四字文案与既有三个外链图标的纵向堆叠
- [x] 1.3 `KeywordLibrary/columns.tsx` 复用 `buildAnalysisColumns`，无需单独改动即可同步生效

## 2. 默认范围改为「全部可用历史」

- [x] 2.1 `KeywordTrendDrawer/index.tsx`：初始 state 与每次打开重置的 state 均由
      `RECENT_30D` 改为 `ALL`

## 3. 测试

- [x] 3.1 `columns.test.tsx`：新增「历史走势」入口的渲染、点击回调、无小图行可用、
      不传 `onOpenTrend` 时不渲染四组用例
- [x] 3.2 `KeywordTrendDrawer/index.test.tsx`：更新默认范围相关断言（`baseResponse`
      默认值、打开即请求 `ALL`、切换关键词不残留「近 30 天」选择、两条"新响应落地前
      不展示旧范围数值"用例的方向对调）
- [ ] 3.3 `pnpm lint && pnpm tsc && pnpm test` 全绿（提交门禁会跑，此处待确认）

## 4. 实机核对

- [ ] 4.1 市场分析屏：确认「操作」列「历史走势」入口可见、可点击、打开的详情与小图
      入口打开的一致
- [ ] 4.2 我的词库屏：同上
- [ ] 4.3 确认默认打开即为「全部可用历史」，且列宽调整后表格横向布局未破版
