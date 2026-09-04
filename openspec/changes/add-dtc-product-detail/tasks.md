## 1. `ProductDetailDrawer.tsx`

- [ ] 1.1 打开时调用 `getProductHistory(domain, productId)`；加载态 / 错误态
      （404「该商品尚未有历史数据」——理论上不会发生，因为能点开的行本身就来自
      有历史的数据，但 401/网络失败仍要有出口）
- [ ] 1.2 价格历史表：观测时刻（倒序）/ 变体（`sku` 缺失时退回 `variant_id`）/
      价格 / 划线价（无则不显示该列内容，不显示 0）/ 是否在售，币种取
      `ProductHistoryResponse.currency`，`currency` 为 `null` 时只显示数字不臆造符号
      （既有约束，参见币种那条 Requirement）
- [ ] 1.3 名次历史表：日期 / 店内相对名次；标题或说明文字点明"店内相对名次，
      不可跨店比较"，不用图表
- [ ] 1.4 两个列表都为空时（理论上不会——能点开就有至少一条价格记录）显示明确的
      空状态文案，不显示裸表格

## 2. `NewArrivalsTab.tsx` 接入

- [ ] 2.1 商品列或整行加点击交互，仅 `row.source === 'DTC'` 时触发；`domain =
      row.source_key`，`productId = Number(row.product_key)`
- [ ] 2.2 Amazon 行（`row.source === 'AMAZON'`）点击不触发任何行为，与现状一致
- [ ] 2.3 更新组件顶部注释——原来写着"尚未实现：点开一行看详情"对独立站已不成立，
      改为只描述 Amazon 侧仍缺失

## 3. 测试

- [ ] 3.1 `ProductDetailDrawer.test.tsx`：多次调价记录、单条记录（从未变化）、
      加载失败三种情形
- [ ] 3.2 `NewArrivalsTab.test.tsx` 补一条：点击 DTC 行触发详情请求；点击 AMAZON
      行不触发

## 4. 验证

- [ ] 4.1 提交门禁：`pnpm lint` + `pnpm tsc` + `pnpm test` 全绿
- [ ] 4.2 CI
- [ ] 4.3 人工实测交给用户：真实浏览器里点开 blessrich.com 的某个上新商品，核对
      价格历史与名次历史显示正确（本地已有真实抓取数据，见 2026-09-04 的
      `docker exec` 核对记录）
