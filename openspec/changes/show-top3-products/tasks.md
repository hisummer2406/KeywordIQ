## 1. 查询层接出前二、前三

- [x] 1.1 `analysis_crud._base_query` 增加两个 `aliased(DimProduct)` 的 LEFT JOIN（按
      `product_id` 关联 `top2_product_id` / `top3_product_id`），SELECT 中补上两组
      `asin / title / brand / category` 与事实表的两组份额字段，共 12 个新 label
- [x] 1.2 核对 `_apply_filters` 与 `SORTABLE_FIELDS` 未被触碰——筛选条件仍全部绑在未 alias 的
      `DimProduct`（top1）上，新 alias 不出现在任何 WHERE 中
- [x] 1.3 `search_schemas._FactRowBase` 增加 top2 / top3 各 6 个字段，缺失槽位为 `None` / 0，
      与 top1 的现有默认值口径一致

## 2. 导出扩列

- [x] 2.1 `analysis_service._EXPORT_COLUMNS_BASE` 扩展为三组商品列，表头标明名次
      （如「Top1 ASIN」「Top2 品牌」），顺序与卡牌名次一致
- [x] 2.2 核对流式路径未变：`export_stmt` 仍走 `_base_query` + `yield_per`，行数上限判定仍在
      开流之前完成

## 3. 后端测试

- [x] 3.1 单元测试：导出列清单含三组商品列且表头可区分名次，日视图额外列仍在末尾
- [x] 3.2 集成测试（`TEST_DATABASE_URL` gate，扩充 `test/test_site_scoped_query_integration.py`）：
      构造一个三槽位齐全、一个只有两个商品的关键词，断言 `/search` 返回的槽位与名次对应、
      缺失槽位为空且不被顺延
- [x] 3.3 集成测试：以第二名商品的品牌筛选，断言该行**不**出现在结果中（口径回归——
      这是本变更最容易被后续改动破坏的一条）
- [x] 3.4 `uv run pytest test/` 全绿；本地 PostgreSQL 起着时确认集成用例未被 skip

## 4. 前端契约与图床映射

- [x] 4.1 `services/analysis.ts` 的 `AnalysisRow` 增加 12 个字段，与后端 schema 逐一对齐
- [x] 4.2 新增 `productImageUrl(asin)` 纯函数，地址推导集中一处。**实测修正**：市场码与
      图床域名对返回内容无影响（含单站点独有商品），故不按站点分支；但 `.01.` 段不可省略，
      省略后尺寸指令失效、返回全尺寸原图（12.7KB vs 1.5KB）
- [x] 4.3 `productImageUrl` 的单元测试（vitest）：地址含 `.01.` 段、不随站点变化，ASIN 为空时
      返回空值而非拼出畸形地址

## 5. 卡牌列

- [x] 5.1 新增商品卡牌组件：缩略图 + ASIN（可复制）+ 点击份额 + 转化份额（百分数口径），
      标题走悬停提示；图片 `loading="lazy"` + `referrerPolicy="no-referrer"`。**实测修正**：
      降级不能只靠 `onError`——图床对不存在的 ASIN 返回 200 + 1×1 透明 GIF，`onError` 永不
      触发，须在 `onLoad` 时按 `naturalWidth/Height` 判定
- [x] 5.2 `columns.tsx`：新增「点击前三 ASIN」列（三张卡横排、槽位缺失留空不收拢）与
      「点击前三品牌」列（按名次竖排）；移除 品牌 / ASIN / 商品标题 / 点击份额 / 转化份额
      五个平铺列；类目列保持不变
- [x] 5.3 ~~`buildColumns` 增加 `site` 入参~~ 实测后地址推导不需要站点，签名维持 `(view)`，
      `index.tsx` 无需改动
- [x] 5.4 过滤区标注口径差：品牌、类目、商品标题、点击份额、转化份额五处标明「只匹配点击量
      第一名」

## 6. 前端测试与实测

- [x] 6.1 `columns.test.tsx`：三槽位齐全时渲染三张卡；只有两个商品时第三位留空且前两张不
      前移；品牌列三行与卡牌名次对齐
- [x] 6.2 图片加载失败与"加载成功但是 1×1 占位像素"两种情形都降级为占位方块，ASIN 与份额
      仍正常渲染；正常尺寸的图片不被误判
- [x] 6.3 `pnpm lint && pnpm tsc && pnpm test` 全绿
- [x] 6.4 **实测图床地址**：已用 `docs/table` 三站点各 3 个 ASIN + 已知真实 ASIN + 德国站
      独有商品交叉验证。结论：URL 方案有效；市场码/域名无影响（映射已删）；不存在的 ASIN
      返回 200 + 1×1 透明像素而非 404（降级机制已据此改写）
- [x] 6.5 导出规模实测（2 万行 / 5 万行）：扩列前 12 列 1.02s / 2.58MB / 峰值 7.0MB，
      扩列后 1.43s / 6.64MB / 峰值 13.2MB；5 万行时峰值仍是 13.3MB——**内存与块大小同阶、
      不随结果集增长**，导出的核心承诺未被破坏

## 7. 收尾

- [x] 7.1 端到端（US 日报 40.3 万行，导入 → 查询 → 导出）：三个槽位的 ASIN / 标题 /
      点击份额 / 转化份额与源 CSV 逐字段一致。**过程中发现先于本变更存在的缺陷**：品牌与
      类目在报表里是关键词级独立排行而非商品属性（同一 ASIN 冲突率 28.5% / 27.3%，标题为
      0%），被导入侧错归到 dim_product。据此撤掉「点击前三品牌」列与槽位的品牌/类目导出列，
      品牌与类目维持改造前的单列呈现；数据模型修正另开变更
- [x] 7.2 中文 conventional commits 提交；提交门禁（ruff + pytest + 前端三件套）全绿
