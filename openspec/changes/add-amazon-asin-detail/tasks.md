## 1. 后端

- [x] 1.1 `watch_crud.py` 新增 `asin_detail_stmt(user_id, seller_id, site, asin)`：
      join `CompetitorSellerAsin` + `CompetitorSeller` + `UserSellerWatch`（ownership），
      复用 `_last_reported_at_subq()` 取卖家级近似值
- [x] 1.2 `watch_schemas.py` 新增 `AsinDetailResponse`（`first_seen_at`、
      `seller_last_reported_at`），字段文档写明后者是近似值
- [x] 1.3 `watch_service.py` 新增 `asin_detail`：查不到（非本人关注）抛 `NotFoundError`
- [x] 1.4 `watch_api.py` 新增 `GET /watches/{seller_id}/{site}/{asin}/detail`
- [x] 1.5 集成测试（`test/test_watch_*.py` 或新文件）：已关注卖家能取到详情、非本人
      关注的卖家返回 404、卖家只被采集过一次时最近采集时刻不为空

## 2. 前端

- [x] 2.1 `services/competitor.ts` 新增 `AsinDetailResponse` 类型 + `getAsinDetail()`
- [x] 2.2 新增 `AsinDetailDrawer.tsx`：首次发现时刻（真实事实）+ 最近采集时刻（近似值，
      文案必须包含"该卖家级别、不代表本商品"的说明）
- [x] 2.3 `NewArrivalsTab.tsx`：`onRow` 对 `AMAZON` 行也挂点击，打开 `AsinDetailDrawer`；
      `sellerId = row.source_key`、`site = row.site`、`asin = row.product_key`
- [x] 2.4 组件顶部注释更新——不再写"Amazon 侧仍是已知缺口"

## 3. 测试

- [x] 3.1 `AsinDetailDrawer.test.tsx`：正常展示两个时刻、加载失败态、竞态防护
      （复用 `ProductDetailDrawer.test.tsx` 已验证过的模式）
- [x] 3.2 `NewArrivalsTab.test.tsx` 补：点击 AMAZON 行触发详情请求，参数取值正确

## 4. 验证

- [ ] 4.1 提交门禁：后端 ruff/pytest + 前端 lint/tsc/test 全绿
- [ ] 4.2 CI
- [ ] 4.3 人工实测交给用户：真实浏览器点开一条 Amazon 新品，核对两个时刻显示正确
      且措辞不会让人误读近似值为该商品专属
