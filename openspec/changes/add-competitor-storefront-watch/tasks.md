## 0. 开工前置

- [x] 0.1 按 `start-change` 建仓库外 worktree `~/.claude-worktrees/add-competitor-storefront-watch`
      与分支 `change/add-competitor-storefront-watch`（从含本变更文档的提交 `3462362` 开出，
      非从裸 `v2`）；已补 `.env`、`frontend/node_modules`
- [x] 0.2 `keywordiq_test` 已在（容器此前已停，`docker start pgbase` 后确认库存在）。
      后端基线 `615 passed, 1 skipped`（唯一跳过是 `TEST_OSS_*` 真实 OSS 回路，非静默
      全跳过）；前端 `pnpm lint` + `pnpm tsc` 全过（vitest 全量留到收工前一次性跑）

## 1. 数据模型与迁移

- [x] 1.1 写 `app/table/competitor/seller_model.py`：`CompetitorSeller`
      （`(seller_id, site)` 复合主键、`baseline_at` 可空、`created_at`）
- [x] 1.2 写 `app/table/competitor/watch_model.py`：`UserSellerGroup`
      （`UNIQUE(user_id, name)`）、`UserSellerWatch`（`UNIQUE(user_id, seller_id, site)`，
      `group_id` 可空外键 `ON DELETE SET NULL`、`priority`/`note`）、`CompetitorWatchPreview`
      （`preview_token` 主键、`user_id`、`parsed` JSONB、`expires_at`）
- [x] 1.3 四张表登记进 `migration_scope.py` 的 `V2_TABLES`
- [x] 1.4 `app/auth/permissions.py` 新增 `COMPETITOR_VIEW = "competitor:view"` 常量与
      `PERMISSIONS` 条目；admin/operator/analyst 三个角色集合加入这个权限码
- [x] 1.5 Alembic 迁移 `a32f4b9a7841`：autogenerate 建三张普通表（预览表 JSONB/过期时间
      列 autogenerate 正确处理，未手写）+ 手写权限点种子（仿 `f2a3b4c5d6e7`）。
      `alembic upgrade head --sql` 核对过无 `PARTITION BY`（普通表符合预期）；downgrade→
      upgrade 往返在测试库上过了一遍
- [x] 1.6 `test_models_match_migrated_schema` 通过（需在测试函数内加
      `from app.table.competitor import seller_model, watch_model` 的 import，与
      `alembic/env.py` 同一条纪律——漏了表现是一整屏 `remove_table`，已在
      `test/test_v2_integration.py` 补上）
- [x] 1.7 `COMPETITOR_MAX_WATCH_PER_USER`（3000）、`COMPETITOR_MAX_IMPORT_BATCH`（5000）、
      `COMPETITOR_PREVIEW_TTL_SECONDS`（600）加进 `config.py`（含 field_validator）并
      同步 `.env.example`

## 2. SellerRefParser（纯函数，先做，其余都依赖它）

- [x] 2.1 写 `app/table/competitor/seller_ref_parser.py`：`SELLER_ID_RE`、六种输入形态
      的解析规则、两种拒绝形态附原因文案
- [x] 2.2 站点识别：`marketplaceID` 参数优先、域名 TLD 回落，经 `app/site_registry.py`
      校验/转换。**订正了 issue #56 原文的一个笔误**：GB 的 marketplaceID 是
      `A1F83G8C2ARO7P`（末尾带 `P`），原文少写了这个字母——用真实书签文件核实过
      （303 条 UK 链接全部带这个参数，与 issue 背景一节"UK 链接缺 marketplaceID"的说法
      不符，已在 design.md 订正）
- [x] 2.3 单元测试 `test/test_seller_ref_parser_unit.py`：16 个用例，六种支持形态、两种
      拒绝形态、marketplaceID 缺失回落域名、`ds=` 不出现在结果里、**裸 ID 需要
      `fallback_site` 兜底**（这是实现时补的设计缺口，design.md/spec.md 已同步）、
      书签 HTML 嵌套文件夹路径提取、候选分组名派生。样本取自真实书签文件（12/13/14 位
      ID 各一例）。全绿

## 3. 后端：分组与关注关系

- [x] 3.1 `watch_schemas.py`：分组增删改查、关注记录的批量查询/移除/改分组，
      ProTable 契约响应 `{data,total,success}` + 配额回显
- [x] 3.2 `watch_crud.py`：分组 CRUD 语句、关注记录的分页查询语句（带分组筛选）、
      批量删除语句、预览暂存的增删查
- [x] 3.3 `watch_service.py`：同名分组冲突（409）、跨用户 404、配额判定（422，整批
      不写）；全部抛 `APIError` 子类
- [x] 3.4 `watch_api.py`：分组四个端点 + 关注记录查询/改/删端点，全部
      `Depends(simple_auth.require_permission(COMPETITOR_VIEW))`；路由挂
      `/api/competitor`，`app/api_router.py` 已注册
- [x] 3.5 单元测试 `test/test_competitor_watch_unit.py`（10 例）：请求 schema 边界
      （建组名空/超长、commit 模式白名单、分页边界、批量条数上限）、去重与 priority
      折算的纯逻辑、upsert 语句按 append/replace_group 生成不同 SET 子句

## 4. 后端：预览/确认两步导入

- [x] 4.1 `watch_service.preview()`：调用 `SellerRefParser` 逐条解析（书签走
      `extract_bookmark_entries` + 逐条 `parse_seller_ref`，文本走 `parse_bulk`），
      按 `(seller_id, site)` 去重算 priority，查 `user_seller_watch` 算 new/existing，
      候选分组名来自书签文件夹路径拍平，写入 `competitor_watch_preview`
- [x] 4.2 `watch_service.commit()`：校验 token 未过期且归属当前用户；`append` 模式对
      非空分组名做 get-or-create、`ON CONFLICT DO NOTHING` 建 `competitor_seller` +
      单条多行 upsert `user_seller_watch`（SET 子句只含 `priority`）；`replace_group`
      模式先删目标分组下全部 watch（`removed_watches` 回传）再重建（SET 子句含
      `group_id`/`note`）
- [x] 4.3 配额校验在 `preview()` 里做（关注总数、单批解析数均在预览阶段拦截，超限
      不产生任何写入）
- [x] 4.4 `purge_expired_previews()` 挂在 `import_queue.py` 的
      `_purge_expired_competitor_previews()`，与 `_reap_stale_uploads` 并列跑在同一轮询
      循环里，异常就地吞掉不连累排队接手
- [x] 4.5 `watch_api.py` 的 `POST /watch/preview`（`multipart/form-data`：`raw_text`
      表单字段或 `file` 文件二选一）、`POST /watch/commit`（JSON）
- [x] 4.6 集成测试 `test/test_competitor_watch_integration.py`（14 例，`TEST_DATABASE_URL`
      gate，全绿）：预览不写库、重复链接折算 priority、UK→GB 映射、append 不覆盖已有
      分组/备注、replace_group 清空重建、配额在预览阶段拦截、过期 token 拒绝确认、
      跨用户预览 token 不可确认、跨用户分组不可见、删组不取消关注（变为未分组）、
      裸 ID 无/有 fallback_site 两种结果、独立验证 `purge_expired_previews` 的清理效果

## 5. 前端

- [x] 5.1 `services/competitor.ts`：分组、关注记录、预览、确认的请求函数（预览走
      `FormData`，不设 `requestType`，umi 的底层 request 对 `FormData` 实例直接透传）
- [x] 5.2 `frontend/src/access.ts` 加 `canViewCompetitor: has('competitor:view')`
- [x] 5.3 `frontend/.umirc.ts` 新增顶层菜单项 `/competitor`（照 `/analysis` 的单页顶层
      模式，非工具箱式二级菜单；排在工具箱之后、数据上传之前——同属「看数据」一类）
- [x] 5.4 `pages/Competitor/`：分组管理区（`GroupBar.tsx`，表格上方横向排布，含恒定的
      「全部关注」伪分组）+ 主表格（`columns.tsx`：状态、卖家、站点、分组下拉、
      `priority`、近 7 日上新数占位「待接入」、关注时间、备注、取消关注）+ 配额条
      「已关注 N / 3000」
- [x] 5.5 `ImportModal.tsx`：「添加卖家」（textarea 批量粘贴）与「导入书签」
      （文件上传）共用一个弹窗、两个 tab，都先调用 preview 展示结果（新增/已存在/
      解析失败列表 + 候选分组 + 抽样），用户确认 append/replace_group 模式后才调
      commit；解析失败列表逐条展示原文与原因
- [x] 5.6 vitest：`index.test.tsx`（8 例，进屏即查/配额条/分组区/状态占位/空状态/
      导入入口）+ `ImportModal.test.tsx`（5 例，预览结果展示、候选分组、
      append/replace_group 文案区分、预览失败留在输入步不崩溃）。全绿，全量前端
      `pnpm tsc && pnpm lint && pnpm test`（35 files / 337 tests）与后端
      `TEST_DATABASE_URL=... uv run pytest test/ -q`（654 passed, 1 skipped）均过
- [x] 5.7（补）修复 `ImportModal.tsx`/`index.tsx` 里 `runPreview`/`runCommit`/
      `onChangeGroup`/`onRemove` 缺失的 `catch`——Button `onClick`、Select `onChange`、
      Popconfirm `onConfirm` 都不消费返回的 promise，失败请求会成为 unhandled
      rejection（对照 `KeywordLibrary/GroupBar.tsx` 已有注释记录的同一条约定
      发现）；`ImportModal.tsx` 顺带把废弃的 `destroyOnClose` 改成 `destroyOnHidden`
- [x] 5.8（补）浏览器手测：起临时 dev server（后端 8010 / 前端 8011，`.env.local`
      覆盖代理目标，未占用主仓的 8000/8001）+ 迁移 `keywordiq_v2_dev`（与主仓共享，
      测完已用 API 清空建的测试分组与关注记录）。验证过：登录后顶栏出现「竞对监控」、
      建组持久化、预览/确认整条链路（含 marketplaceID 缺失回落 `fallback_site`、
      `/stores/` 链接正确拒绝并说明原因）、表格渲染状态/占位列正确

## 6. 文档与收尾

- [x] 6.1 CLAUDE.md 加「竞对监控」一节：两层模型（共享卖家 + 私有分组/关注）、
      `competitor:view` 独立权限点、预览/确认两步、不含数据获取层的范围边界
- [x] 6.2 `uv run pytest test/ -q` + `pnpm lint && pnpm tsc && pnpm test` 全绿（含
      `TEST_DATABASE_URL`，确认不是集成测试静默跳过的假绿）。实测：`ruff` 通过、
      后端 **654 passed, 1 skipped**（唯一跳过是需真实 OSS 回路那个，非集成测试静默跳过）、
      前端 lint/tsc 通过 + **35 files / 337 tests 全过**
- [x] 6.3 人工验收：建组 → 粘贴文本添加几个卖家（预览→确认）→ 上传
      `docs/bookmarks_2026_8_18(1).html` 全量导入（预览→确认，观察 2657 家的响应耗时与
      正确性）→ `replace_group` 模式重新导入验证清空重建 → 删组。

      2026-08-29 实测（临时 dev server 后端 8010 / 前端 8011，`.env.local` 覆盖代理目标；
      库 `keywordiq_v2_dev`，测完已清回基线）。五步全通，逐项证据：

      - **建组**：持久化，刷新后仍在。
      - **粘贴文本**：7 行覆盖六种形态 → 解析 4、拒绝 2 且各带原因（`/stores/` 品牌店铺页、
        `/dp/` 商品详情页）。站点识别三条路径全中：域名 → US、`marketplaceID` → DE、
        `amazon.co.uk` → **GB**（不是 UK）、裸 ID → 兜底 US。重复行折算成 `priority=2`，
        未产生第二条关注。
      - **书签全量**：文件实为 **3177 条 `<A HREF>`、3115 条卖家链接**（任务里写的 2657 是
        旧数，实际去重后 **2667 个卖家**）；界面报 62 条无法解析，与 `3177−3115=62` 精确吻合。
        预览往返 **248ms**，确认落库 **465ms**（2666 卖家 + 2666 关注）。共享层去重生效：
        已存在的卖家未重建。站点分布 US 2370 / GB 272 / DE 28。
      - **`replace_group`**：更新 2667、清空重建 4，总数 2670 → 2667；核对后目标组 2667 条、
        未分组 0 条、其余 37 个分组全部清空、仅存在于文本导入的 3 个卖家被删除。
      - **删组**：确认文案写明「组内 2667 个卖家会变为未分组，关注关系本身不受影响」；
        执行后 **2667 条关注全部存活并变为未分组**，`ON DELETE SET NULL` 得到实测验证。
      - **额外覆盖到的路径**：预览 TTL 过期后确认，报「预览不存在或已失效，请重新提交」，
        弹窗留在预览步可「上一步」重来——错误契约与重试路径均正确。

      **发现两个问题，均不阻塞本次 P0，另立 issue**（见下方 6.5）。
- [x] 6.5（验收补）**站点码文案把英国写成 `UK`，已就地修正为 `GB`。** 三处：
      `GroupBar.tsx` 的用户可见提示 + 同文件注释、`services/competitor.ts` 注释。
      属本变更自己新增的代码且违反「英国是 `GB` 不是 `UK`」这条铁律，故不另立 issue。
      站点下拉本身取自 `site_registry`，显示的一直是 GB，问题只在这几处硬编码文案。

- [ ] 6.6（验收补，**不在本变更范围**）另立 issue 两项：
      1. **后端不可达时，登录页把网络失败呈现为「账号或密码错误」。** 本次验收因代理
         配错指向已停的 8000，5 次登录全是 ECONNREFUSED，界面却提示密码错——真实用户
         遇到只会反复试密码。属登录页既有行为，与本变更无关。
      2. **`replace_group` 的目标分组下拉没有搜索。** 书签导入会按文件夹名自动建组
         （本次一次建出 37 个），38 项虚拟滚动列表里找一个组要滚 5 屏；关注上限是 3000，
         分组只会更多。属体验增强，不是缺陷。

- [ ] 6.4 按路径提交、开 PR（`--base v2`，带变更 id、`Refs #56`、spec delta 文件清单，
      并在 PR 说明中注明这是 issue #56 的 P0，后续阶段另立变更）
