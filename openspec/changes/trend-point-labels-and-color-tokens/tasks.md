## 1. 共享色值 token

- [x] 1.1 新建 `frontend/src/components/trendColors.ts`，导出 `TREND_COLORS`
      （`up: '#3f8600'` / `down: '#cf1322'` / `flat: '#1677ff'`），注释写明取深色组的
      理由是对比度，以及"共享色值不共享口径"
- [x] 1.2 `columns.tsx` 的 `renderChange` 与 `renderChangeRate` 改引用 token；
      `0` 由灰改蓝
- [x] 1.3 `TrendSparkline.tsx` 的 `color` 计算改引用 token，**口径不变**
      （仍是 `values[n-1]` vs `values[0]`）
- [x] 1.4 `columns.test.tsx` 补断言：上升/下降/无变化三态各自的色值来自 token

## 2. 各点排名常驻标注

- [x] 2.1 `TrendSparkline.tsx` 为每个点渲染 `<text>`，值用 `toLocaleString()`
      （与悬浮提示同格式）
- [x] 2.2 标签按下标奇偶交替置于折线上下两条固定基线，字号 9、填充 `#595959`
- [x] 2.3 高度默认值由 52 增至 72，并相应调整 `py()` 的绘图区，使折线不占用标签带
- [x] 2.4 单点与缺期情形：只为实际存在的点标注，不留空位

## 3. 测试

- [x] 3.1 `TrendSparkline.test.tsx`：7 点全部渲染出对应数值文本
- [x] 3.2 `TrendSparkline.test.tsx`：相邻两点的标签 y 值分居折线上下（交替生效）
- [x] 3.3 `TrendSparkline.test.tsx`：标签为中性色，不随升降变色
- [x] 3.4 `TrendSparkline.test.tsx`：3 点时只有 3 个标签
- [x] 3.5 `TrendSparkline.test.tsx`：颜色口径回归——末点优于首点即为绿，
      **即便**末点相对倒数第二点变差
- [x] 3.6 `pnpm lint && pnpm tsc && pnpm test` 全绿

## 4. 实机核对

> **未完成，开 PR 时仍未做。** 设计里写明"七个六位数的密度无法靠推演定论，只能做出来看"，
> 这三项因此不是可选项。当前状态：worktree 的 dev server 已起在 8002，但需登录才能看到
> 表格，而登录这一步没有做。**合并前必须补上**——若实测过密，退路按设计里的优先级：
> 只标首尾与最值 → 缩写 → 加宽列。

- [ ] 4.1 起前后端，在天视图看六位数排名行的实际密度，确认七个标签可读、不重叠
- [ ] 4.2 切周视图确认 4 点同样标注（注意：dev 库 US 站只有 2 期周报，
      缺期按 spec 少给点，那里实际只会画 2 个点，不是标注失效）
- [ ] 4.3 确认行高未被撑高（与商品卡牌列对比）
