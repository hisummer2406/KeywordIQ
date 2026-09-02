# 任务

> 进度留痕：并行会话只能通过这里知道做到哪了。勾选与说明必须一致。

## 1. 索引迁移

- [x] 1.1 手写 Alembic 迁移（分区表，autogenerate 看不见分区，禁止用它生成）：给
      `fact_daily`/`fact_weekly` 各补 `idx_{table}_top2_product`/`idx_{table}_top3_product`，
      列序 `(site, date_col, topN_product_id)`，照抄迁移 `d3e4f5a6b7c8` 第 3.5 步的写法
      （父表 `CREATE INDEX IF NOT EXISTS`），并在迁移 docstring 里写明已装满的库需要先离线
      按 ONLY + CONCURRENTLY + ATTACH 补建（同样照抄该迁移注释里的 recipe，`fact_weekly`
      的日期列是 `report_date_week` 不是 `report_date`，别抄错列名）——`7b8b046c075e`
- [x] 1.2 `uv run alembic upgrade head --sql` 核对生成的 DDL：只有 `CREATE INDEX`，
      不出现任何 `PARTITION BY`/`DROP`/对已有列的改动——已核对，只有 4 条 `CREATE INDEX`
- [x] 1.3 `uv run alembic upgrade head`（本地库）；跑一次 autogenerate 探针确认为空
      ——`alembic check` 输出 "No new upgrade operations detected."

## 2. 后端：竞对新品判定与 ASIN 扩展

- [x] 2.1 `app/table/search/search_schemas.py` 的 `AnalysisSearchRequest` 新增
      `competitor_new: bool | None = Field(None, description=...)`，与 `is_new` 同构
      （`None` = 不筛）
- [x] 2.2 `app/table/analysis/analysis_crud.py`：
      - `_apply_filters` 里把 `if req.asin:` 分支改成先解析 `product_id`（子查询）再
        `or_(top1==pid, top2==pid, top3==pid)`（design.md 决策 2），**确认
        `_count_base_query` 的 `DimProduct` LEFT JOIN 不能删**——`product_title` 筛选
        仍然依赖它
      - 新增 `if req.competitor_new:` 分支：解析出 `dim_product.product_id` 集合
        （join `competitor_seller_asin` → `user_seller_watch`，按 `user_id` 与当前 `site`
        限定），`or_(top1.in_(ids), top2.in_(ids), top3.in_(ids))`
      - `user_id` 从 service 层传入的作用域参数取，MUST NOT 从请求体读
      - **`request_has_filters()` 的元组里加入 `req.competitor_new`**（design.md 决策 3——
        这是本变更唯一一处"漏掉不会报错，只会让总数算错"的地方，实现后必须专门用一个
        只带 `competitor_new` 的请求核对总数走的是 `count_filtered` 不是 `site_data_stats`）
      ——四条全部落地；`search_stmt`/`count_capped_stmt`/`count_filtered_stmt`/`export_stmt`
      及其同步包装函数都改签名加了 `user_id`，API 层从 `current_user.id` 取值透传
- [x] 2.3 `app/table/analysis/analysis_service.py`：作用域解析里把当前用户 `user_id` 传给
      `_apply_filters`（若当前签名没有带，需要顺着调用链补上，注意不要影响其余不需要
      `user_id` 的筛选路径的现有测试）——`search`/`resolve_export_scope`/`iter_export_csv`
      均加了 `user_id` 参数并透传给 crud 层
- [x] 2.4 `app/table/competitor/asin_model.py` 的 `CompetitorSellerAsin` 与
      `app/table/analysis/dimension_model.py` 的 `DimProduct` 之间目前没有 ORM 级关联
      （靠 `asin` 字符串值 join，不建外键——事实表同款纪律，两个 schema 内表本就不建
      跨域外键）；确认 `analysis_crud.py` 里引入 `CompetitorSellerAsin`/`UserSellerWatch`
      的 import 路径正确，不产生循环 import（`app.table.analysis` → `app.table.competitor`
      方向的依赖此前不存在，需要确认这个方向可行或是否要挪到更上层）——已核对：两个包的
      `__init__.py` 均为空，`asin_model.py` 只反向导入 `column_comments` 子模块，无环

## 3. 前端：筛选面板

- [x] 3.1 `frontend/src/pages/Analysis/filters.ts`：`Filters` 接口加 `competitorNew: boolean`，
      `defaultFilters()` 缺省 `false`；`toSearchBody` 里 `!onlyDropped && filters.competitorNew
      ? true : undefined`（照抄 `brands`/`categories` 的 `!onlyDropped &&` 掉榜抑制模式——
      掉榜行没有当期数据，筛选条件对它们不成立，见 CLAUDE.md 分析查询屏一节）——
      同时按 CLAUDE.md 的既有纪律把 `FilterDraft` 形状纳入两处版本号：
      `stateCache.ts` 的 `CACHE_VERSION` 与 `savedMode.ts` 的 `SAVED_MODE_VERSION`
      均 1→2（两者都完整存 `FilterDraft`/`Omit<FilterDraft,'q'>`，不 bump 的话旧快照/
      旧保存条件会让 `competitorNew` 以 `undefined` 渗入 UI）
- [x] 3.2 `frontend/src/pages/Analysis/FilterPanel.tsx` 加一个「竞对新品」开关（两态，不做
      三态——不像"只看新词"是核心维度，这是一个可选的叠加信号）；旁边加一句提示："依赖
      浏览器插件采集的竞对在售数据，插件未上线前这里可能暂无命中"（design.md 风险 1，
      解决用户第一次看到空结果时的困惑）——用 antd `Switch` + `aria-label="竞对新品"`
      （供测试与无障碍工具按可访问名定位；`Row` 的标签本身只是纯文本，不天然绑定控件）
- [x] 3.3 `frontend/src/services/upload.ts` 或对应的请求体类型（凡是维护
      `AnalysisSearchRequest` 前端镜像类型的地方）同步加 `competitor_new` 字段——
      核实全仓只有 `frontend/src/services/analysis.ts` 一处 `SearchBody` 镜像，已加

## 4. 测试

- [x] 4.1 `test/test_analysis_filters_unit.py`：ASIN 命中 top2/top3（不止 top1）、
      `competitor_new` 的 SQL 语句形状断言（`or_` 三列 + 正确 join 链）、
      `request_has_filters` 在只传 `competitor_new` 时返回 `True`（design.md 决策 3
      的回归测试，且要验证"去掉这一行断言会不会漏"——先临时不把 `competitor_new` 加进
      元组，确认这条测试真的会红，再放回来）——已临时移除并确认测试真的会红（见提交前
      的验证记录），三条新测试 + 已有 12 条全绿
- [x] 4.2 `test/test_site_scoped_query_integration.py`（真库）：
      - ASIN 命中第二/第三槽位的关键词——重写了原先钉住"只匹配 top1"的用例
      - 关注两个不重叠在售集合的卖家，两个不同用户查询得到不同命中集合（跨用户隔离）
      - 跨站点关注不影响本站点判定（在 `US` 关注的卖家不影响 `GB` 站点的判定）
      - 未关注任何卖家时命中为空、不报错
      - `competitor_new` 与品牌筛选同时开启按 AND 组合
      - 只传 `competitor_new` 时返回的 `total` 与实际命中行数一致（不是 `site_data_stats`
        的预存值）——这条断言直接对应 2.2 的实现要点，真库上验证比单测更有说服力
      ——新增 `_seed_competitor_watch`（四种关注形态：owner/other/no_watch/gb_only）+
      6 个新测试用例，全部通过；连带修掉 `service.search`/`resolve_export_scope`/
      `iter_export_csv` 全部既有调用点的签名（本文件 + export/window-scoped 两个
      集成测试文件 + export 单元测试的 stub）。TEST_DATABASE_URL 全量真库跑法：
      `819 passed, 1 skipped`（后端全部测试，非本节子集）
- [x] 4.3 前端：`filters.test.ts` 补 `competitorNew` 的 `toSearchBody` 映射（含
      `onlyDropped` 时被抑制）；`FilterPanel` 相关测试文件补开关的交互测试——本仓库
      没有独立的 `FilterPanel.test.tsx`（交互测试走 `scopeAndFilters.test.tsx` 这个
      整屏集成测试文件，与其余筛选控件同一模式），已在其中补一条：切换开关只改草稿、
      点「开始筛选」才带上 `competitor_new: true`。提交门禁跑了真实的 `pnpm lint` /
      `tsc` / `test`（worktree 首次装了 `node_modules`），抓到两处真实回归：
      `savedMode.test.ts` 的夹具缺 `competitorNew` 字段（tsc 报类型不全）、
      `defaultsAndFirstPaint.test.tsx` 的 `seedSaved`/断言硬编码 `version: 1`
      （`SAVED_MODE_VERSION` 已 bump 到 2，硬编码版本号会被判过期静默丢弃，「我的默认」
      标记读不出来）——均已修好，两处改用符号常量 `SAVED_MODE_VERSION` 而非字面量

## 5. 文档与收尾

- [x] 5.1 CLAUDE.md「分析查询屏」一节补充「竞对新品」筛选维度的说明（口径、AND 组合、
      `request_has_filters` 那个陷阱——按本文件既有的"错了不会报错"密度写）；
      「竞对监控」一节的 issue #56 阶段说明更新（P2 已完成，方向 C 移出待补数据源）
- [x] 5.2 `.env.example` 若引入新配置项（目前预计不需要，若实现时发现需要上限类配置
      则补齐，含单位与理由）——未引入新配置项，无需改动
- [x] 5.3 `openspec validate add-competitor-keyword-crosslink --strict` 通过——
      "Change 'add-competitor-keyword-crosslink' is valid"
- [x] 5.4 后端全量真库回归 + 前端全量（`pnpm lint && pnpm tsc && pnpm test`，**由用户
      本地跑，不代跑**）；`ruff check .`——后端已单独跑过：`819 passed, 1 skipped`（连
      `TEST_DATABASE_URL`）+ `ruff check .` 全绿。前端三项由**提交门禁**（`pre_commit.py`）
      在 `git commit` 时强制跑了一遍（首次提交因 worktree 没装 `node_modules` 而报
      "command not found"，`pnpm install --prefer-offline` 后门禁真正执行，抓到并
      修好了 4.3 记的两处回归），门禁通过即代表这一遍全绿——不是本任务的"用户自己另跑
      一遍"那条，那条仍然按约定不代劳
- [x] 5.5 按路径提交 → 推分支 → `gh pr create --base v2`（PR 正文含变更 id、
      `Refs #56`、spec delta 文件列表）——PR #66（`base: v2`，已核对），
      https://github.com/hisummer2406/KeywordIQ/pull/66

## 6. PR #66 复审修复（2026-09-02）

复审在 `91456e4` 上独立跑通 CI 后指出 2 个 P1 阻断项 + 1 个 P2 语义问题，均已修复：

- [x] 6.1 **[P1] "竞对新品"改成真正的时间窗口判定**：`analysis_crud.py` 的
      `competitor_new` 子查询补 `CompetitorSeller` join，加两个条件
      `first_seen_at > baseline_at`（排除基线批次）与
      `first_seen_at > now() - interval 'N days'`（复用
      `watch_crud.NEW_DISCOVERY_WINDOW_DAYS`，不新定义窗口常量）。此前的实现只判断
      "卖家是否上报过这个 ASIN"，`competitor_seller_asin` 不做下架删除，等价于命中
      卖家有史以来的全部商品。design.md 决策 2、两份 spec delta、CLAUDE.md 均已同步
      改写。真库集成测试新增
      `test_competitor_new_excludes_baseline_batch`/`test_competitor_new_excludes_stale_discovery`
      直接验证这两个时间条件缺一不可；`_seed_competitor_watch` 补了显式
      `baseline_at`/`first_seen_at`
- [x] 6.2 **[P1] `competitor_new=true` 补 `competitor:view` 权限校验**：
      `simple_auth.py` 新增 `SimpleAuth.check_permission`（供依赖注入覆盖不到的
      "权限要不要求取决于请求体内容"场景用）；`/search`、`/export` 在触达 service 层
      之前各加一行 `if req.competitor_new: simple_auth.check_permission(user,
      COMPETITOR_VIEW)`。前端 `FilterPanel` 新增 `canCompetitorNew` prop，
      `index.tsx` 传入 `access.canViewCompetitor`，无权限时不渲染开关（体验层，
      真正边界在后端）。新增单测文件
      `test_analysis_competitor_permission_unit.py`（4 个用例：两端点各测有/无权限）+
      前端一条隐藏开关的用例；`analysisMocks.ts` 的 `accessState` 补
      `canViewCompetitor: true` 默认值
- [x] 6.3 **[P2] `competitor_new=false` 不再被误判为"带筛选"**：
      `request_has_filters` 元组里改成 `req.competitor_new or None`——这个字段只有
      两态（不像三态的 `is_new`），`_apply_filters` 是 `if req.competitor_new:`，
      `False` 与 `None` 查询结果一样，裸传会让 `False` 被 `v is not None` 误判成
      "带筛选"从而错误切到精确计数路径。补单测 `test_competitor_new_false_is_not_a_filter`
- [x] 6.4 复审后重新跑通：`ruff check .` 全绿；`TEST_DATABASE_URL` 全量真库回归
      `825 passed, 1 skipped`（含新增 6 个用例；另有 1 个与本 PR 无关的既有日期边界
      失败，复审已确认不是本 PR 的阻断项）；`openspec validate --strict` 通过
- [x] 6.5 按路径提交 → 推送 → 等待复审重新确认——两个提交：`3d8d522`（无关的挂钟时间
      测试修复，独立提交）、`0992e8d`（三处复审修复），已推送

## 7. PR #66 二次复审修复（2026-09-02）

二次复审在 `4460e3c` 上确认上轮 2 个 P1 + 1 个 P2 均已关闭，但新发现 2 个 P2（均为
纯前端问题，未涉及后端）：

- [x] 7.1 **[P2] 权限撤销后，已保存的竞对筛选会让首屏持续 403**：`filters.ts` 新增纯函数
      `sanitizeCompetitorNew(filters, canCompetitorNew)`，无权限时强制把 `competitorNew`
      规范化为 `false`。`index.tsx` 在两处读取保存条件的地方接入（草稿初值、
      `resolveInitialScope` 组装首屏条件）；`apply()`（立即查询/开始筛选/首屏自动查询
      三处生效入口唯一共同必经之处）额外做一次同样的兜底并据此重算 `sig`，不依赖调用方
      各自记得清空这个字段——顺带把 `apply` 的第二个参数（调用方传入的 `sig`）去掉，
      改为函数内部统一算，避免字段被清空后 `body` 与外部传入的 `sig` 不再对应同一份
      内容。新增单测：`filters.test.ts` 3 个用例（有权限透传/无权限强制关闭/无权限且
      原值已是 false）+ `defaultsAndFirstPaint.test.tsx` 1 个集成用例（保存
      `competitorNew: true`、当前 `canViewCompetitor: false` 时首屏请求不带
      `competitor_new`、开关隐藏、且不误报「未生效条件」）
- [x] 7.2 **[P2] `competitor_new` 漏出掉榜提示的筛选字段清单**：`index.tsx` 的
      `FILTER_KEYS` 补上 `'competitor_new'`。新增集成测试
      `scopeAndFilters.test.tsx`：只启用竞对新品筛选（新词切到全部）时仍显示
      「本次结果不含掉榜行」提示
- [x] 7.3 CLAUDE.md「竞对新品」一段补充这两处修复的说明（按既有密度：错了不会报错的
      地方才展开）
- [x] 7.4 前端全量：`pnpm exec vitest run src/pages/Analysis/`（195 passed）、
      `pnpm exec tsc --noEmit`（无报错）、`pnpm exec eslint`（改动文件均通过）。
      本轮未改动后端，未跑后端回归
- [x] 7.5 按路径提交 → 推送 → 等待复审重新确认
