---
paths:
  - "app/table/competitor/**/*.py"
  - "frontend/src/pages/Competitor/**/*"
  - "extension/src/**/*"
  - "test/test*competitor*.py"
  - "test/test*store*.py"
  - "test/test*asin*.py"
---

# 竞对数据实现约束

- 按 [竞对导航](../../openspec/guides/competitor.md) 读卖家、ASIN、DTC 与联动筛选规格，避免混用时间语义。
- 共享观测不带账号属性；私有关注/组/预览逐查询限 user_id，他人资源 404。竞对标签组跨站，删组不取消关注。
- 卖家书签与手工粘贴共用解析器；不存 ds 会话参数，缺站点依据拒绝，不猜默认；逐项回拒绝原因。
- 同事务建立基线与首批 ASIN，基线仅 NULL 时写；客户端 captured_at 仅审计，不能参与新发现判定。
- request_id 按用户隔离，hash 含规格规定的审计字段；同键异内容 409，真正重试重发原请求，不静默回放吞观测。
- 零有效 ASIN 整批 422，不能落空基线；超条数拒绝不截断；保留 first_report_id 追溯，删账号不连带删共享商品。
- 被动上报“没见到”不证明下架；新发现不表示商品首次上架，配送地区只留档，不能作存活判据。
- 上新流在 SQL UNION 后统一分页，身份/rowKey/排序完整含 site；无基线数量 NULL，不 COALESCE 成 0。
- Shopify 下架只在 catalog_complete 时做；新品看 product_id 是否存在，不看可重置的 published_at。
- 价格仅 price/compare_at_price/available 真变才追加，当前值取最新观测；币种持久化，未知不造符号。
- 畅销榜只表示店内相对名次，不能造销量或跨店比较；目录与畅销解析失败状态独立。
- 抓取复用轮询和 SKIP LOCKED；网络异步，不长占 DB/共享线程或加全局网络锁。
- 竞对普通表不进事实保留注册；不擅自补取关 GC、逐次目录快照或后台巡检等未批准范围。
