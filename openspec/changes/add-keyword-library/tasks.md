## 1. 数据模型与迁移

- [ ] 1.1 写 `app/table/toolbox/library_model.py`：`KeywordGroup`、`KeywordGroupItem`
      两个 SQLAlchemy 2.0 模型（`Mapped` / `mapped_column`），外键与唯一约束按 design 决策 1；
      `site` 列注释取 `column_comments.SITE_COLUMN_COMMENT`
- [ ] 1.2 两张表登记进 `migration_scope.py` 的 `V2_TABLES`（漏登会让 autogenerate 静默看不见）
- [ ] 1.3 `uv run alembic revision --autogenerate -m "词库分组与条目"`，人工核对 DDL 带
      `schema='analysis'`、外键 `ON DELETE CASCADE` 正确、downgrade 能干净删表
- [ ] 1.4 `uv run alembic upgrade head` 后跑
      `pytest test/test_v2_integration.py::test_models_match_migrated_schema`，
      要求 autogenerate 产出**空**结果（列注释一字不差）
- [ ] 1.5 两个上限加进 `config.py`（`LIBRARY_MAX_GROUPS_PER_SITE=20`、
      `LIBRARY_MAX_ITEMS_PER_GROUP=500`）并同步 `.env.example`（含单位与理由）

## 2. 查询层复用（先重构，单独一个提交）

- [ ] 2.1 把 `analysis_crud._base_query` 里的列投影抽成公共函数 `fact_row_columns(fact)`，
      `_base_query` 改为引用它，**不改变任何 SQL 语义**
- [ ] 2.2 跑既有查询回归（`pytest test/ -q` + 有真库时的
      `test_site_scoped_query_integration.py`），确认抽取前后结果一致

## 3. 后端：分组与条目

- [ ] 3.1 `library_schemas.py`：分组的增删改查、条目批量增删、盯盘查询与作用域的
      Pydantic v2 请求/响应模型（盯盘响应走 ProTable 契约 `{data,total,success}` + 作用域回显）
- [ ] 3.2 `library_crud.py`：分组 CRUD 与条目批量增删的语句（条目写入用
      `ON CONFLICT (group_id, keyword_id) DO NOTHING`，回传实际新增行数）
- [ ] 3.3 `library_service.py`：站点一致性校验、两个上限的判定（超限抛 422、整批不写）、
      同名冲突（409）、非本人分组（404）；失败一律抛 `APIError` 子类
- [ ] 3.4 `library_api.py`：分组四个端点 + 条目两个端点，全部
      `Depends(simple_auth.require_permission(TOOLBOX_VIEW))` + 当前用户，路由挂在
      `/api/toolbox/library`；在 `main.py` 注册
- [ ] 3.5 单元测试 `test/test_keyword_library_unit.py`：上限判定、跨站点加词被拒、
      重复加词幂等、非本人分组 404（不连库，纯服务层）

## 4. 后端：盯盘查询

- [ ] 4.1 `library_crud` 增盯盘语句：由条目表驱动、两个 LEFT JOIN（事实表 + `fact_dropped`）、
      状态三段序 + 排序白名单（走势与历史排名字段不许排序）
- [ ] 4.2 `library_service` 增 `watch` 与 `get_scope`：站点取自分组不由请求传入；
      可选日期集读 `site_data_stats`；分页 `total` 取条目数
- [ ] 4.3 分页后补走势与历史排名，复用 `fetch_trends` / `shape_trends`，
      **`on`/`view` 必传**（漏传会画出天 8 点 / 周 13 点且不报错）
- [ ] 4.4 集成测试 `test/test_keyword_library_integration.py`（`TEST_DATABASE_URL` gate）：
      在榜 / 掉榜 / 无数据三种状态各出现一行、排序时后两者恒在末尾、走势点数按视图正确、
      满员分组（500 条）跑一次盯盘查询

## 5. 前端：词库屏

- [ ] 5.1 `services/library.ts`：分组与盯盘的请求函数
- [ ] 5.2 `pages/Toolbox/KeywordLibrary/`：左侧分组列表（站点切换、新建、重命名、删除
      带二次确认），右侧 ProTable 盯盘；进屏自动查默认分组的最新一期
- [ ] 5.3 三种行状态的呈现：在榜正常、掉榜带标记与上期排名、无数据留空并明示该期无数据；
      空分组的空状态说明是「这个分组还没有词」
- [ ] 5.4 路由与菜单：`frontend/config/routes.ts` 在工具箱下加「我的词库」二级项
- [ ] 5.5 vitest：状态呈现与空状态文案的用例

## 6. 前端：分析屏加入词库

- [ ] 6.1 结果表格加 `rowSelection` + 批量操作条「加入词库」；选中状态不进 `stateCache`
      （因此不动 `CACHE_VERSION`）
- [ ] 6.2 选择分组的弹窗：只列当前站点的分组，支持当场新建；完成后反馈新增/已存在条数
      并清空选中
- [ ] 6.3 复选框列加入后**实测**列宽与 `scroll.x`（`getBoundingClientRect`，不靠推算）
- [ ] 6.4 vitest：勾选后出现批量操作条、切走再切回选中清空、弹窗只列当前站点分组

## 7. 文档与收尾

- [ ] 7.1 CLAUDE.md 增词库一节：两张表、行集合由条目决定（与分析屏相反）、两个上限、
      入库入口唯一、不新增权限点
- [ ] 7.2 `uv run pytest test/ -q` + `pnpm lint && pnpm tsc && pnpm test` 全绿
- [ ] 7.3 人工验收：建组 → 分析屏加词 → 盯盘看三种状态 → 删组
- [ ] 7.4 按路径提交、开 PR（`--base v2`，带变更 id、`Refs #37`、spec delta 文件清单）
