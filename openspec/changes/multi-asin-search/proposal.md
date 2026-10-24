## Why

Refs #150。分析查询屏的检索框按输入格式自动判定目标：只有整串形如 `B0` 加 8 位的输入才按 ASIN 查，其余一律按关键词查。由此产生三个问题：

- 一次只能查一个 ASIN，运营手上的竞品清单要逐个粘贴。
- 非 `B0` 编号（德国站图书 ISBN-10 等）查不了，会被当成关键词。
- 10 位英文单词可能被误判为 ASIN。

关键词模式的下拉嵌在输入框内部，自动判定又改变它的出现与否，界面状态难以预期。

## What Changes

- 检索框前新增「搜索方式」下拉，三个选项：关键词 · 全部词（缺省）、关键词 · 词组、ASIN · 批量。查询目标完全由所选方式决定，**不再按输入格式推断**。
- **BREAKING**（接口）：`/analysis/search` 与 `/analysis/export` 的 `asin: str` 改为 `asins: list[str]`，最多 100 个（服务端去重后计数，超出 422），不校验格式，大小写不敏感，前三商品槽位任一命中。
- ASIN 批量输入仍是单行框：支持空格、逗号、中文逗号、分号、换行（含 Excel 多单元格粘贴）分隔，粘贴时把换行与制表符转成空格以保留边界；去重后超过 100 个时在界面提示并拒绝提交，不静默截断。
- 查询栏保持一行，模式下拉移出输入框成为独立等高控件。

## Capabilities

### Modified Capabilities

- `analysis-screen-layout`：用「显式选择搜索方式」取代「按输入格式判定检索目标」，并更新「每个筛选维度恰好一个入口」中检索框的描述。
- `site-scoped-query`：「ASIN 精确匹配覆盖三个商品槽位」扩展为 ASIN 集合（任意命中、去重、上限 100、不校验格式）。

## Impact

- 后端：`app/table/search/search_schemas.py`、`app/table/analysis/analysis_crud.py`、`app/table/analysis/analysis_service.py`。
- 前端：`frontend/src/pages/Analysis/` 下的 `filters.ts`、`ScopeBar.tsx/.less`、`useAnalysisQuery.ts`、`index.tsx`、`stateCache.ts` 及 `services/analysis.ts`。
- 会话快照形状变化，提升 `CACHE_VERSION`。保存的条件不含检索词和模式，`SAVED_MODE_VERSION` 不变。
- 规则：`.claude/rules/frontend.md` 中「ASIN 前端正则仅路由启发」一条随之失效，改为「检索目标由所选方式决定，ASIN 不做格式校验」。
- 接口变更随前后端同一镜像发布；未刷新的旧页面发送的 `asin` 字段会被忽略（请求模型不拒绝多余字段），表现为未施加 ASIN 筛选，刷新即恢复。
