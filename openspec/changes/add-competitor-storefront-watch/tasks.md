## 0. 开工前置

- [ ] 0.1 按 `start-change` 建仓库外 worktree `~/.claude-worktrees/add-competitor-storefront-watch`
      与分支 `change/add-competitor-storefront-watch`（从含本变更文档的提交开出，非从裸 `v2`，
      否则 openspec 文档会落下）；补 `.env`、`frontend/node_modules`
- [ ] 0.2 确认 `keywordiq_test` 在（`local-db` skill），基线回归 `uv run pytest test/ -q`
      + `pnpm lint && pnpm tsc && pnpm test`，记录结果作为对照基线

## 1. 数据模型与迁移

- [ ] 1.1 写 `app/table/competitor/seller_model.py`：`CompetitorSeller`
      （`seller_id`/`marketplace` 复合唯一、`baseline_at` 可空、`created_at`）
- [ ] 1.2 写 `app/table/competitor/watch_model.py`：`UserSellerGroup`
      （`UNIQUE(user_id, name)`）、`UserSellerWatch`（`UNIQUE(user_id, seller_id,
      marketplace)`，`group_id` 可空外键、`priority`/`note`）、`CompetitorWatchPreview`
      （`preview_token` 主键、`user_id`、`parsed` JSONB、`expires_at`）；`site`/站点相关
      列如需注释复用 `column_comments.SITE_COLUMN_COMMENT`
- [ ] 1.3 四张表登记进 `migration_scope.py` 的 `V2_TABLES`（含预览表，漏登会让
      autogenerate 对它的后续结构变化「看不见也不报错」）
- [ ] 1.4 `app/auth/permissions.py` 新增 `COMPETITOR_VIEW = "competitor:view"` 常量与
      `PERMISSIONS` 条目；`ROLES` 的 admin/operator/analyst 三个角色集合加入这个权限码
- [ ] 1.5 一条 Alembic 迁移：`alembic revision --autogenerate -m "竞对卖家关注清单"`
      建三张普通表（预览表可选是否 autogenerate 或手写，视其 JSONB/过期时间列
      autogenerate 是否正确生成）+ 手写权限点种子（`INSERT ... ON CONFLICT DO NOTHING`
      到 `permission`，再关联 `role_permission`，仿 `f2a3b4c5d6e7` 的写法，迁移文件
      不 import 应用代码、按 `permissions.py` 同一份矩阵硬编码字符串）
- [ ] 1.6 `uv run alembic upgrade head` 后跑
      `pytest test/test_v2_integration.py::test_models_match_migrated_schema`，确认
      autogenerate 对齐后产出空结果
- [ ] 1.7 `MAX_WATCH_PER_USER`（3000）、`MAX_IMPORT_BATCH`（5000）加进 `config.py`
      （含单位/理由的 docstring 或注释）并同步 `.env.example`

## 2. SellerRefParser（纯函数，先做，其余都依赖它）

- [ ] 2.1 写 `app/table/competitor/seller_ref_parser.py`：`SELLER_ID_RE`、六种输入形态
      的解析规则（详见 design.md 决策 3 的表）、两种拒绝形态附原因文案
- [ ] 2.2 站点识别：`marketplaceID` 参数优先、域名 TLD 回落，统一经
      `app/site_registry.py` 校验/转换（不新起编码，UK 输入必须映射成 `GB`）
- [ ] 2.3 单元测试 `test/test_seller_ref_parser_unit.py`（不连库）：六种支持形态各一例、
      两种拒绝形态各一例、`marketplaceID` 缺失时的域名回落、`ds=` 签名参数不出现在
      解析结果里。样本取自 `docs/bookmarks_2026_8_18(1).html` 的真实抽样（12/13/14 位
      ID 各一例）

## 3. 后端：分组与关注关系

- [ ] 3.1 `watch_schemas.py`：分组增删改查、关注记录的批量查询/移除/改分组，
      ProTable 契约响应 `{data,total,success}` + 配额回显
- [ ] 3.2 `watch_crud.py`：分组 CRUD 语句、关注记录的分页查询语句（带分组筛选）、
      批量删除语句
- [ ] 3.3 `watch_service.py`：同名分组冲突（409）、跨用户 404、配额判定（超限抛 422，
      整批不写）；失败一律抛 `APIError` 子类，不做 try/except 转译
- [ ] 3.4 `watch_api.py`：分组四个端点 + 关注记录查询/移除/改分组端点，全部
      `Depends(simple_auth.require_permission(COMPETITOR_VIEW))` + 当前用户；路由挂
      `/api/competitor`；`main.py` 注册
- [ ] 3.5 单元测试 `test/test_competitor_watch_unit.py`（不连库）：请求 schema 白名单、
      分页语句形状、配额判定的边界值

## 4. 后端：预览/确认两步导入

- [ ] 4.1 `watch_service.py` 加 `preview(raw_text | file) -> PreviewResult`：调用
      `SellerRefParser` 逐条解析，按候选 `(seller_id, marketplace)` 查已存在的
      `user_seller_watch` 算出 new/existing，书签文件夹路径拍平出候选分组名，写入
      `competitor_watch_preview`（`expires_at` 设若干分钟）
- [ ] 4.2 `watch_service.py` 加 `commit(preview_token, mode, target_group_id) ->
      CommitResult`：校验 token 未过期且归属当前用户；`append` 模式
      `ON CONFLICT DO NOTHING` 建 `competitor_seller` + upsert `user_seller_watch`
      （仅更新 `priority`，不动 `group_id`/`note`）；`replace_group` 模式先删目标分组
      下全部 watch 再重建
- [ ] 4.3 配额校验放进 `preview`（`MAX_WATCH_PER_USER`/`MAX_IMPORT_BATCH` 超限即返回
      错误，不进入可确认状态）
- [ ] 4.4 过期预览行清理挂在 `import_queue.py` 已有轮询循环上（同一循环内加一次
      `DELETE FROM competitor_watch_preview WHERE expires_at < now()`，不新开
      lifespan 任务）
- [ ] 4.5 `watch_api.py` 加 `POST /watch/preview`（接受 `raw_text` 或文件上传）、
      `POST /watch/commit`
- [ ] 4.6 集成测试 `test/test_competitor_watch_integration.py`（`TEST_DATABASE_URL`
      gate）：预览不写库、append 不覆盖已有分组/备注、replace_group 清空重建、过期
      token 拒绝、配额在预览阶段拦截、跨用户预览 token 不可确认

## 5. 前端

- [ ] 5.1 `services/competitor.ts`：分组、关注记录、预览、确认的请求函数
- [ ] 5.2 `frontend/src/access.ts` 加 `canViewCompetitor: has('competitor:view')`
- [ ] 5.3 `frontend/.umirc.ts` 新增顶层菜单项 `/competitor`（照 `/analysis` 的单页顶层
      模式，非工具箱式二级菜单）
- [ ] 5.4 `pages/Competitor/`：分组管理区（表格上方横向排布）+ 主表格（卖家、站点、
      分组、状态固定「待建立基线」、`priority`）+ 配额条「已关注 N / 3000」
- [ ] 5.5 「添加卖家」入口（textarea 批量粘贴）与「导入书签」入口（文件上传），两者
      都先调用 preview 展示结果（新增/已存在/解析失败列表 + 候选分组），用户确认后
      才调 commit；解析失败列表逐条展示原文与原因
- [ ] 5.6 vitest：预览结果展示、配额超限提示、append/replace_group 两种模式的界面
      文案区分

## 6. 文档与收尾

- [ ] 6.1 CLAUDE.md 加「竞对监控」一节：两层模型（共享卖家 + 私有分组/关注）、
      `competitor:view` 独立权限点、预览/确认两步、不含数据获取层的范围边界
- [ ] 6.2 `uv run pytest test/ -q` + `pnpm lint && pnpm tsc && pnpm test` 全绿（含
      `TEST_DATABASE_URL`，确认不是集成测试静默跳过的假绿）
- [ ] 6.3 人工验收：建组 → 粘贴文本添加几个卖家（预览→确认）→ 上传
      `docs/bookmarks_2026_8_18(1).html` 全量导入（预览→确认，观察 2657 家的响应耗时与
      正确性）→ `replace_group` 模式重新导入验证清空重建 → 删组
- [ ] 6.4 按路径提交、开 PR（`--base v2`，带变更 id、`Refs #56`、spec delta 文件清单，
      并在 PR 说明中注明这是 issue #56 的 P0，后续阶段另立变更）
