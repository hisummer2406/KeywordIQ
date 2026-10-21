# CLAUDE.md

本文件面向所有在仓库工作的智能体；文件名沿用历史命名。先读根目录 `AGENTS.md`，再读本文件。
本文件规定项目红线、开发流程与按需读取入口；[领域指南](openspec/guides/README.md) 是它的细则附录。
新增约定写在这里或对应领域指南；`AGENTS.md` 只维护运行环境差异与 Codex 审核规则。
修改被 Codex 审核规则浓缩的约束时，必须同步核对 `AGENTS.md`，防止两份口径分叉。
用户当次指令优先；已有明确授权继续有效，不能因换工具而重复索取。

## 开工必读：按改动范围加载细则

所有任务先读本文件；动手前再读下表命中的指南。涉及多个领域时取并集，不得只读摘要就实现。
指南不自动全部加载；历史 Issue/change id 用于溯源，不能把旧部署参数或历史测试结果当作当前事实。
指南路径相对仓库根目录；若实现与约定冲突，先核实并报告，不能默默选一份绕过约束。

| 改动范围或任务 | 必须先读 |
|---|---|
| 事实/维度、分区/保留、查询/导出、增长榜/词库/排名趋势 | [分析、词库与数据模型](openspec/guides/analysis.md) |
| 导入、首次入榜证据写入、批次/重导、OSS 直传 | [CSV 与 OSS 导入](openspec/guides/ingestion.md) + 分析指南 |
| 导航、分析筛选、首次入榜展示、表格/缓存/保存条件 | [导航与分析界面](openspec/guides/frontend.md)；涉及口径时同时读分析指南 |
| 卖家关注、ASIN 上报、Shopify、上新流、浏览器插件 | [竞对监控与浏览器插件](openspec/guides/competitor.md)；联动分析筛选时同时读前端指南 |
| 本地登录/退出、会话/JWT、RBAC、钉钉登录/回调 | [认证、权限与钉钉登录](openspec/guides/auth.md) |
| V2 ECS/OSS/飞书运维、容量、部署/健康检查 | [生产边界与容量](openspec/guides/operations.md) + [当前部署交接](docs/ops/v2-ecs-handoff.md) |
| 开始/接手完整变更、提交、PR、评审、合并/归档 | [开发流程与历史依据](openspec/guides/workflow.md) + 对应 skill/command |

主文件保留关键判据，指南保存完整细则、反例与测试入口。业务需求及其场景仍以 `openspec/specs/`
和获批变更的 spec delta 为准；指南解释维护约束，不另建一套业务规格。实现时还须读对应 spec。

## 项目与命令

KeywordIQ 是 Amazon 搜索词分析平台，FastAPI + PostgreSQL 15 + AntD Pro/Umi Max SPA。
读多写少，约 200 并发；日报约 30 万行 / 150 MB，周报约 300 万行 / 1.5 GB，性能需分别评估。
V2 全面重建、已上线；V1 代码不复用，但同机 V1 服务仍受隔离保护。所有工作基于 `v2`。

```bash
uv sync
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000  # 仅本地；生产 V2 宿主机为 8001
uv run pytest test/
uv add <package>                 # 开发依赖用 --dev
uv run alembic upgrade head      # 本地数据库；生产迁移由发布工作流封装
uv run alembic revision -m "说明" # 分区表/物化视图必须手写
uv run alembic upgrade head --sql
openspec list
openspec validate --strict --all
```

生产 `.env` 中数据库主机为容器网络 `db`，宿主机不能直接使用该连接；不要裸跑宿主机迁移。
常规发布入口只有 GitHub Actions `Deploy V2 to ECS`，授权和门槛见「部署与运维」。

## 分层与通用编码约束

- `app/table/*` 按 model、crud、service、api、schemas 分层；调用方向 api → service → crud。
  API 是薄控制器，业务编排及事务由 service 管理；新 CRUD 不自行 commit。
- **SQLAlchemy 2.0 Only**：`DeclarativeBase`、`Mapped`、`mapped_column`、`select()`、`Session.execute()`。
  不引入 `declarative_base()`、`Column(...)` 模型声明或 `.query()`。
- **Pydantic 2.0 Only**：`ConfigDict`、`field_validator`、`model_validate`、`model_dump`；不引入 v1 API。
- 业务表全部使用配置的 `analysis` schema；`ops_bot/` 独立使用 `ops` schema 与 `OpsBase`。
- 不在 FastAPI 事件循环上做阻塞数据库调用、长 CPU 解析或整文件读取；GB 文件必须流式、内存有界。
  子进程若存在，必须自建并释放引擎/会话，不继承父进程连接；V2 导入不恢复 V1 的并行分片流程。
- 不返回未分页全表；引入筛选/关联前考虑连接池、内存、索引与执行计划，按日报/周报分别论证。
- 新配置同步 `.env.example`，注明单位、安全默认值与生效范围；运行值由配置和交接记录确定。
  本文件不复制完整环境模板；不得提交 `.env`、私钥、AccessKey、App Secret、客户报表、日志或临时切分文件。
- 问题定位先看失败用例和真实前提，再设计；租户、权限、数据源和收费等前提需查证。
  缺失数据与尚未接入能力明确显示缺失或未接入，不编造 0、销量、结论或已验收状态。

## V2 数据模型：事实只追加，站点只有一个来源

表、字段、索引和迁移细则见分析指南。V1 `csv_processor.py`、关键词 UPSERT 与 JSONB 趋势已退役。

| 表/模块 | 约束 |
|---|---|
| `dim_keyword` | `(site, keyword)` 唯一；关键词全文检索与永久首次记录在这里 |
| `dim_product` | ASIN 唯一；SCD Type 1，仅属性真变才写 |
| `fact_daily` / `fact_weekly` | 按日期月度 RANGE + 站点 LIST 分区；日期列分别为 `report_date` / `report_date_week` |
| `fact_dropped` | 按 `anchor_date` 月度分区，无站点子分区；含周期 |
| `fact_growth` | 按 `anchor_date` 月度分区，无站点子分区；只有日报，无 `period_type` |
| `site_data_stats` | 期次、在榜/掉榜数量，供作用域与掉榜总数；在榜可见总数另按当前基线实算 |
| `category_stats` / `brand_stats` | 站点/周期/期次普通统计表，导入只重算当期切片 |
| `keyword_group` / `keyword_group_item` | 私有词库普通表，不被导入写入，不参与保留清理 |
| `competitor_*` / `user_*_watch` | 普通表；共享观测与私有关注分层，不参与事实分区保留清理 |

1. **四类事实表只追加，不原地 UPDATE。** 排名及派生值在导入时算定；查询只读。
   唯一例外为同期重导：删除该站点/周期/期次切片，再重新写入；不扫描重写其他切片。
2. **写事实前无条件调用 `ensure_fact_partitions`**，它是幂等的。
   新分区表登记 `partitioning.PARTITIONED_TABLES`；普通竞对表不得登记，否则历史会被整月 DROP。
3. 维度 UPSERT 保留 `IS DISTINCT FROM` 守卫；`dim_keyword`/brand/category 的去重预筛也不可随意删除。
   相同值 UPDATE 仍制造死元组、索引写入与 WAL，测试绿不代表写放大安全。
4. **站点与展示元数据唯一来源为 `app/site_registry.py`**；英国用 `GB`，不用 `UK`。
   非法或必填场景缺失的站点返回 422，不猜测 `DEFAULT_SITE`，不另建站点表/硬编码清单。
   仅接口明确允许省略站点时使用配置默认值；卖家裸 ID 必须由调用方提供站点依据。

### 分区、迁移与保留清理

- 业务模型新增时登记 `migration_scope.V2_TABLES`；ops 模型登记 `OPS_TABLES`。
  漏登会让 autogenerate 静默忽略；分区表/物化视图必须手写迁移，离线核对 DDL 中 `PARTITION BY`。
- 列注释参与 autogenerate 比对；模型、迁移与库保持一致，共用文本放 `column_comments.py`。
  真实数据库迁移到 head 后，模型比对必须为空；大量注释噪声会掩盖真正的结构差异。
- `HOT_WINDOW_MONTHS` 默认 3，按自然月保留；过期分区整月 DROP，日/周同口径，不承诺更早历史可查询。
  运行 `scripts/prune_retention.py` 前先 dry-run；是否调度、是否留档按运维交接确认。
- 保留清理无条件删除 `site_data_stats`/`category_stats`/`brand_stats` 的超期记录，不能依赖本次有 DROP。
  后两表保留 `report_date` 单列索引；仅复合主键不能支持这一清理的范围过滤（#100 / #101）。
- 清理非 dry-run 路径必须持有与导入共用的 `import_lock` / `IMPORT_LOCK_KEY`。
  拿不到锁返回 `lock_skipped`，不阻塞、不重试；否则事实提交与统计提交间会出现事实被删、统计被补回。

## 分析查询、导出与排名趋势

修改该域前读分析指南；首次入榜、筛选与缓存展示还需读前端指南。接口作用域为单站点、单视图、锚定期。

| 维度 | DAY | WEEK |
|---|---|---|
| 行窗口 | `[d-6,d]` | 近 4 周 |
| 历史参照 | `d-1/d-3/d-7` | `w-1/w-4/w-12` |
| 趋势小图 | 7 点 | 4 点 |
| 回溯查询 | 7 天 | 84 天 |

- 行集合为在榜 ∪ 掉榜，`include=ALL/LISTED/DROPPED`；掉榜恒排在在榜之后，不随升降序翻转。
  有筛选时只匹配锚定期事实行，掉榜全部消失、`dropped_total=0`；界面必须解释，不能当成数据丢失。
- 日期选择器走保留范围内 `site_data_stats`；类目/品牌选项走当期统计，不能在事实全表实时聚合。
  类目多选 `categories`，空列表不筛、上限 100；不恢复已删除的单值 `category_exact`。
- 在榜总数扣除类目黑名单后实算，筛选总数精确计数、不封顶；不要将原始 `row_count` 当分页分界。
  黑名单只排除 top1 类目、NULL 保留；下拉/列表/总数/导出一致，不计入 `request_has_filters`。
- 先分页再批量补走势与历史参照，两者共用回溯查询；`shape_trends` 和日期裁剪必须传 `on/view`。
  历史排名与走势不排序，排序白名单按视图；日视图不混入“当周排名变化”。
- 分页使用完整确定性次级键；联合上新流为 `source, source_key, site, product_key`。
  小样本结果稳定不能证明排序正确，必要时对 ORDER BY 结构断言。
- 列表成功使用 ProTable `{data,total,success}` 并回显作用域；失败使用非 2xx 的统一错误契约。
- 导出与 search 共用请求及口径，忽略分页；服务端游标分块、自建 Session，不能复用已清理的请求会话。
  超 `EXPORT_ROW_CAP`（默认 5 万）在开流前返回 413，禁止静默截断。
- 排名详情共用只读端点 `GET /api/keyword/trend`，抽屉展示，不新建菜单。
  事实历史使用 `idx_fact_{daily,weekly}_keyword_history`；掉榜用 `idx_fact_dropped_keyword_history`，
  **不走掉榜主键**，其日期位于关键词前会导致范围内全站扫描（#87）。
- 排名详情先判保留下界，再查期次是否导入；不信任下界之前的 `site_data_stats`。
  区分保留范围外、在榜、掉榜、已导入未收录、未导入五态；缺失不可当排名 0。
- `on` 是打开详情时来源页面的期次；切日/周沿用同一值，由后端取 `<= on` 最新一期，无则全局最新。
  `latest_available` 单独标日期；周历史步长复用导入侧 7 天偏移，不另造周边界。
- 趋势端点仅在“不含域私有数据且跨域共用”条件下允许 `analysis:view` 或 `keyword:view` 任一。
  `competitor_new` 读取私有关注，不能套用此权限放宽。

### 持续增长市场与我的词库

- 增长榜仅日报：窗口每期在榜、累计名次净增严格超过阈值、6 个日环比均达到阈值。
  口径只在 `importer.load_growth` 算定；改 `GROWTH_*` 后必须回填已存期次，上线时也必须回填。
  窗口缺期据实返回空榜和缺期清单；区分未查询、缺期、确无命中。
- 增长屏首屏不自动查，不显示伪造条数/“条件已修改”，提示“立即查询”；分析屏首屏自动查，不能互换。
- 词库由条目驱动 LEFT JOIN，词恒有一行，区分在榜、掉榜、该期无数据。
  条目只存 `keyword_id`；组绑站点且创建后不变；入库入口仅分析结果勾选，词库屏只移除。
- 词库组数/条数超限整批拒绝，默认每站点 20 组、每组 500 词；提限前重测历史查询的最坏代价。
  词库/竞对私有查询逐条带 user_id，访问他人的组/条目返回 404；不新增“管理自己收藏”权限。
- 词库进屏即查；没有分组、站点无数据、组内无词分别展示。组管理放表格上方，不挤压横向表格。
  词库与分析事实投影共用 `analysis_crud.fact_metric_columns`，不可复制第二份。

### 首次入榜（#137 V1 / #141）

- 永久证据存 `dim_keyword.first_seen_date/first_seen_period_type/first_seen_basis`；站点独立，日/周共用。
  导入只修正更早证据，不在导入阶段按基线决定“首次”，结果须与导入顺序、重导无关。
- 新插入词记 `IMPORTED`；存量回填为 `EARLIEST_OBSERVED`，无证据为 `UNKNOWN`。
  相同日期日报优先，等值不写；`IMPORTED` 不降级，UNKNOWN 有证据后转 EARLIEST_OBSERVED。
- 监测起点 `B` 只取 IMPORTED 的最早日期；首次命中=IMPORTED、落在映射窗口且窗口起点晚于 B。
  周报日期是周六结束日，覆盖 `[w-6,w]`；周来源只有周精度，不能分摊到每日。
- 判据只在 `analysis_crud.first_listed_expr`，列表/数量/筛选/词库共用，不另存按期数量。
  不可判断返回 `judgeable=false,count=null`；回填不产出 IMPORTED、不移动 B，不承诺补出历史首次。
- `is_new` 已弃用，但当前 expand/contract 阶段保留列与 DEFAULT false，不能提前删列/索引破坏旧容器。
  新请求为 `first_listed`；旧回填脚本及 `NEW_WORD_LOOKBACK_DAYS` 已退役，兼容细则见前端指南。

## 导入：先直传、后流式 COPY、后台串行接手

修改前读导入指南与分析指南；改变导入路径须做真实体量端到端验证。

1. `/oss/upload-credentials` 先落 UPLOADING 批次，再交受限 STS 凭证；落库失败不能签发。
2. 前端直传 OSS；GB 字节不经应用服务器。object key 由服务端限定站点/周期/期次前缀，日期只来自文件名。
3. `/oss/import` 验证前缀、对象存在与大小，认领 UPLOADING，交后台流式 COPY → UNLOGGED 暂存 → 维度 → 事实。
4. OSS 对象是原始归档，批次保留 object key 供溯源和失败重导；本地退路仅供本地/CI，不代替生产归档。

- 导入用 `pg_try_advisory_lock` 串行；拿不到进 QUEUED。轮询任务必须实际接手，不能只靠上次导入结束链式接力。
  无 object key、无法重建源的本地批次判 FAILED 并提示重传，不能永久排队。
- UPLOADING 回收时限来自 STS 有效期 + 300 秒宽限，不另造旋钮；对象存在则重新校验大小后排队，否则失败。
- 上传屏最近 5 条轮询与历史分页用途不同，共用 CRUD；历史屏不轮询，单页上限 100，超限拒绝。
- 重导只允许 FAILED，新建批次保留旧错误；不存在 404、非 FAILED 409、空源 422，其余前缀/称量校验先做完。
  与 `/oss/import` 共用 `verify_source_object`；后台任务不受全局响应处理器保护，不能把准入校验推入后台。
- STS 越权拒绝、bucket CORS、ETag 暴露只能实测；生产独立 bucket/RAM、内网 Endpoint 按交接逐项核对。
  `scripts/check_oss.py` 的结果不能用单元测试或代码评审代替。

## 认证与错误契约

修改登录、退出、令牌清理、RBAC、SSO 前读认证指南；安全边界不能只靠前端隐藏。

### 认证、会话与权限

- 自建 PyJWT/HS256 + bcrypt + RBAC；没有全局鉴权中间件，受保护端点逐个挂依赖。
  常用权限为 analysis:view/export、upload:import、keyword:view、competitor:view、opportunity:view。
- 用户管理端点只看 `is_super`，不是 role_code=admin，也不开放可授予但无效的 user:manage/role:manage。
  opportunity:view 默认不授予任何内置角色；admin 由全部权限减 `SUPER_ONLY_PERMISSIONS`，不能动态继承超管权限。
- 登录签发会话只有 `UserCenterCRUD.rotate_active_session` 一个入口，本地/SSO 共用。
  返回本地产生的 session_id，不读 commit/refresh 后的 user.active_session_id，防并发串号。
- `get_current_user` 对 session_id 缺失/空/不匹配抛 `SessionInvalidError`，错误码 auth.session_invalid。
  裸 HTTPException 会被映射为 auth.unauthorized，丢失“其他浏览器登录”的区别。
- logout 使用不校验会话匹配的 `get_current_user_for_logout`；清理必须条件更新，只有本令牌仍为当前会话才清空。
  否则旧会话退出会清掉新登录；旧会话不能在依赖层被提前拒绝而绕过条件更新。
- 前端每条失败后清令牌路径都在真正清理前紧贴比对“请求令牌仍为当前令牌”。
  `app.tsx`、fetchUserInfo、裸 fetch 导出均须覆盖；比对后如有 await，清理前再次比对。
- 钉钉是授权码 OAuth2，不是 OIDC；个人与企业凭证分开，企业 token 缓存用锁。
  账号只按 `(external_idp,external_id)` 匹配，external_id 用 unionId，禁止用户名自动绑定或按 userid 识别。
- SSO 新建用户 is_super 恒 false，角色取配置，不从部门映射，不回写既有属性。
  登录时反查在职；离职前已签 JWT 可活到过期，不能宣称实时吊销。SSO 不自动合并本地账号。
- SSO 账号无密码，仍走哑哈希路径，不能提前返回泄露账号类型；本地密码入口保留作故障逃生。
  SSO 关闭端点统一 404，仍注册路由；开启缺凭证启动失败；角色与数据库不可达的边界详见认证指南。
- Login/SsoCallback 共用 `useLoginRedirect`；先落 currentUser 再跳转，外域走 hardRedirect。
  真机扫码、回调域名、通讯录权限须实测 `scripts/check_dingtalk_sso.py`，代码审核不等于已通过。

### 所有失败响应统一

```json
{"success":false,"code":"<域>.<原因>","message":"<用户可见中文>","request_id":"0f3a1b2c"}
```

- Service 抛 `APIError` 子类；API 不用 try/except 盖掉域错误码，不返元组或失败的 200 ProTable。
  内部 SQL、路径、对象 key、栈和配置细节只进 `log_detail` 与日志，不能进入 message。
- 状态码按语义：400 参数、401 未认证、403 无权限、404 不存在、409 冲突、413 超上限、422 业务拒绝、
  503 基础设施不可用、500 兜底。Pydantic 校验只重建 fields 的 field/msg，不透传可能含密码的 input。
- `X-Request-Id` 贯穿请求、日志和错误体；已有值沿用。FastAPI 不传 `debug=`，避免 traceback 泄露。
- 流式响应的失败在开流前判定；BackgroundTasks 自捕获并 `logger.exception` 留栈，不用 error 丢栈。
- 前端错误提示唯一出口为 app.tsx 的 errorHandler；skipErrorHandler 必须显式声明并自行呈现失败。

## 前端与导航：生效条件、缓存和缺失态不能混淆

修改前读前端指南；本节仅保留跨屏原则和高风险判据。

- 导航按数据域命名，八项顺序为机会中心、市场分析、词库工具、竞对监控、产品档案、风险核查、定价核算、数据管理。
  路由在 `frontend/src/routes.ts`，不要复塞 `.umirc.ts`；单页顶层不增二级菜单，以免侧栏挤掉宽表。
- 风险/产品/定价未接入能力不发请求、不造数据、不加空权限点；仅超管前端 access 可见。
  接入真实端点时补后端权限并替换占位 access；机会中心有独立超管权限，不等同占位屏。
- 分析三个 hook 以 index.tsx 协调、值与回调传递，彼此不直接 import；展示组件受控、不发请求。
- 初始化优先级：会话快照 > 按 userId 保存条件 > 内置默认。defaultFilters 是唯一默认定义，默认只看首次入榜。
  草稿与生效条件分开，用户点立即查询后生效；改站点/视图只重拉元数据，排序/翻页即时请求。
- 保存条件只存生效站点/视图/筛选/排序，不存日期或检索词；dirty 时拒绝保存。
  userId 缺失不读写，不回退共享键；保存条件跨 logout，重置才删除，版本变化直接丢弃旧 payload。
- 会话快照在 logout 清空；切回页面只对首次恢复短路，立即查询/翻页/排序仍发请求。
  改形状提升 CACHE_VERSION，保存形状提升 SAVED_MODE_VERSION；测试前 clearSnapshot，防用例串数据。
- 保存条件按当前权限规范化 competitorNew，覆盖草稿初值、首屏初始化与 apply 入口。
  不得恢复一个用户已无权限、界面又看不到的隐形筛选而触发 403。
- 首屏使用 reloadScopeMeta 的返回值而非尚未更新的 React state；恢复已保存类目传 keepCategories。
  无期次不请求，显示无数据；滚动恢复等行落 DOM，位置靠 scroll 监听，不靠卸载 cleanup 读取。
- ASIN 等值查询、三槽位匹配；前端 B0 正则只是路由启发，不作后端格式限制，ISBN-10 也可能是 ASIN。
  ASIN 不建无用 trgm；关键词模式只对关键词输入出现，不入保存条件，规则唯一来源 keyword_match.py。
- ALL_TERMS 为完整词 AND、顺序不限、允许夹词；常规英文单复数互通，非英语/含数字/不规则只精确。
  最多 8 词，超限 422；拆不出词退回子串。PHRASE 连续子串，%/_ 按字面；不许暗示语义/同义词支持。
- 份额为 0～100 百分数；排名变化原始负号代表上升，不翻转符号。
  日视图“7天变化率”对 d-7，周视图对上一周；筛选/排序 ranking_change 仍对上一期，缺基期显示占位。
- 排除品牌词按该行前三品牌整词匹配，非品牌全集；只在首次入榜筛选时发出，NULL/短品牌边界见指南。
  所有真筛选进入 request_has_filters 与前端 FILTER_KEYS；布尔 false 与未传等价，使用 `or None`。
- 加入词库按当前结果的生效站点选组，不能跟草稿站点走；掉榜行可勾选，选中状态不进快照。

## 竞对监控与插件：共享观测，私有关注

修改前读竞对指南；插件还须读 `extension/README.md` 及采集变更 design。

- 共享主体/观测不带账号属性；分组/关注/预览私有且逐查询限制 user_id，别人的记录返回 404。
  竞对分组是跨站点标签，删组置 NULL、不取消关注；词库分组绑站点，两者不能照抄。
- 卖家链接与书签共用纯解析器；marketplaceID > TLD > 调用方 fallback_site，无依据拒绝，不猜站点。
  丢弃 ds 等会话参数；拒绝品牌店/详情页时逐项给原因。书签走预览→确认，配额预览时整批拒绝。
- ASIN 上报只接受本人关注的卖家；服务端决定 user_id/时间，captured_at 仅审计、不参与判定。
  基线与首批 ASIN 同事务写，首批恒不算新发现；基线只在 NULL 时更新，现有观测只增不改。
- request_id 按 user_id 隔离；命中后对 request_hash，不同内容返回 409，不能误回放吞观测。
  hash 除标题/图片外仍含 captured_at、ship_to_region、page_context；真正重试重发原请求。
- 单项坏 ASIN 可拒并回原因，重复去重；超条数整批拒绝，**零条有效 ASIN 整批 422，禁止空基线**。
  first_report_id 保留溯源，删账号 SET NULL 不连带删共享商品。
- “竞对新发现”仅首次见到晚于卖家基线且在近 7 日内，不代表商品新品或何时上架。
  依赖被动采集覆盖；缺采集、仅基线、超窗口分别解释。筛选前三 ASIN，额外要求 competitor:view，search/export 同校验。
- 上新流 SQL UNION ALL 后统一分页排序，不在应用层拼两页；响应身份、rowKey、完整排序键都包含 site。
  无基线的数量为 NULL，不用 COALESCE 伪造 0；配送地区只留档，不据“没见到”判下架。
- Shopify 新品看 product_id 是否已存在，不看可能被商户批量重置的 published_at。
  下架仅在 catalog_complete 时做：自然翻页终止且数量不少于 meta 声明，触顶/网络异常不可判整店下架。
- Shopify 价格/compare_at_price/available 真变才追加；当前价取最新观测，不加另一份当前价字段。
  币种来自 meta 并持久化，未知不造符号；best-selling 只有店内相对名次，不转销量、不跨店比较。
- 待首次拉取、未开放接口、失败、不完整分别展示；畅销榜解析异常独立标记，不伪装空榜。
  店域名入口共用规范化；DTC 批量一步完成，限额整批拒绝、逐输入行回原因，不套卖家替换组的两步流。
- 拉取复用 import_queue 轮询，行认领 SKIP LOCKED；网络异步、仅 DB 写入走线程池，不加全局网络锁。
  本能力不做取关后 GC；不得把竞对普通表纳入三个月事实清理。

### 浏览器插件（extension/）

- manifest 与 package 版本一致，manifest 保留公钥 `key` 固定扩展 ID；私钥不得入库。
- 多页先本地合并，**一次请求上报**；逐页上报会把后续存量当新发现。
- 跨域靠 optional_host_permissions，API 地址按设置申请，不依赖后端 CORS、不写死 host_permissions。
- 插件同步网页的 24 小时全权限 JWT，尚非独立可吊销令牌；不能把同步描述成权限收窄。
- 分发 zip＋加载已解压，`pnpm run pack`；升级覆盖后重新加载，无自动更新。
  DOM 选择器需真实 Amazon 页面验证，旧真机确认不代表所有站点当前已验收。

## 部署与运维：只走授权入口，V1 不动

开始 ECS、OSS 或 ops-bot 工作前先读交接与运维指南；环境记录不是当前在线状态，必要时重新验证。

- V2 只由 `Deploy V2 to ECS` 手动发布。必须为 refs/heads/v2、完整 SHA 位于 origin/v2 历史，
  该 SHA 后端/前端/插件三项必需 CI 均 completed/success；不得放宽门槛或用 SSH 绕开。
- 合并、评审通过、`/close-change` 均不等于发布授权。用户明确要求本次发布时可代触发，不重复索取已有授权。
  先报告 origin/v2 最新目标、无未推送提交、三项 CI、迁移和无进行中导入；有新迁移需明说并等确认。
  CI 缺失/失败不能硬触发；按工作流门槛处理，不以用户知情代替 success。
- 用 `gh workflow run "Deploy V2 to ECS" --ref v2`，跟踪到完成并报告 `/health`。
  失败只报现象/日志，不自行重试或 SSH 修补；源码里的 deploy.sh/start.sh 不是另一个常规发布入口。
- V1 `amazon.liangtong.online`、宿主机 8000 不得修改/停止。V2 `key.liangtong.online` 只绑 127.0.0.1:8001，
  Compose 项目 keywordiq-v2；所有 Compose ports 只绑回环，容器内部 8000 不等于宿主机 V1。
- 自建 PG15 与应用共享 4 vCPU / 8 GiB。连接预算为两个引擎池分别相加再乘 4 worker，业务 84；
  ops-bot 再占最多 12，总计 96，对 PG_MAX_CONNECTIONS=120 留 24，改任何池前重算。
  THREAD_POOL_LIMIT 默认 32，与 DB 池独立，不能跟同步池降到 5；PG 内存与服务同机核算。
- **数据库没有自动备份**（2026-09-20 决定）；保留手动备份，不默认有恢复点，不擅自加回定时备份或逾期告警。
  磁盘、缓存命中等容量信号须基于当前观测；旧 RDS 容量估算不作为现机保证。
- ops_bot 与 app 相互独立，不互 import；重启/发布与导入共用的锁 key 必须两边同步。
  独立角色/ops schema；只做预定义动作，重启/发布走确认卡，导入中拒绝，生产恢复不开放飞书指令。
- OSS/RAM/CORS、飞书回调/确认卡和外部拨测按交接逐项验证；本次文档变更不代表它们已配置或真机通过。

## 流程重量：默认最轻，长期决策才留完整规格

沿用三档；用户已明确范围与方案时直接按授权推进。必要澄清一次问完，不串行多轮追问。

| 档位 | 产物 | 适用 |
|---|---|---|
| 直接做 | 改动＋相关验证 | 样式、局部逻辑、补测试 |
| 先对齐再做 | 一次对齐关键决策后实施 | 分叉有限、形态明确 |
| 完整流程 | OpenSpec 四件套＋独立 worktree＋PR | 新子系统、改契约、需长期追溯的决定 |

取舍看决策以后是否需要翻阅，不按改动大小；一次性脚本、测试辅助或提交信息不展开长篇背景。
提交信息中文 conventional commits，通常 3～5 行；只对不显然且静默失败的取舍展开理由。
OpenSpec CLI 直接使用，不需绑定某个 Agent；更详细流程和旧事故见开发流程指南。

### 跨 Agent 交付：Issue 是契约，PR 是审核包

默认 Codex 讨论、Issue 交接、Claude Code 实现、Codex 独立审核；用户可另行指定执行者。
不要求下一个 Agent 重读完整聊天。Issue 至少含背景目标、可验证验收、非目标、已定约束与待定决策。

1. 完整流程先读 `.claude/skills/start-change/SKILL.md`，认领并建独立仓库外 worktree。
2. PR 指向 `v2`，包含 Issue、OpenSpec 路径、spec delta、实现摘要、已跑验证与未覆盖风险。
3. 审核只读 checkout/worktree，核对 Issue/spec/diff，发现分必须修复、建议、疑问；不能改实现者工作区。
4. 必修全部关闭、相关审核和 CI 通过才可合并；不采纳须说明并获审核者确认，不能当作自动通过。
5. 用户要求收尾才按 `/close-change` 合并、归档和清理；未完成验收转移并保留链接，不靠勾选冒充验收。
6. 默认分支 main 仍是 V1；合入 v2 不自动关闭 Issue，完成范围兑现后手动关闭，未完成保持打开。

#### 代发 @codex review

仅用户当次明确要求“让 codex 评审/再发一轮复审”时可代发，不因 deliver-pr/fix-review 自行触发。
先核 PR base=v2、最新提交已推送、head SHA 正确、CI 已有结果；红灯先报告，由用户决定是否仍发评审。
`gh pr comment <PR号> --body "@codex review"`，只加必要重点，发后报告评论链接，不轮询等结果。
不代发 @claude；之后的复审须有对应轮次授权。

### 工作分支与并行会话

- 所有变更从 v2 建分支，通常 `change/<id>`，不是 main；PR 显式 `--base v2`，创建后核对。
- 先 `openspec list`、`git branch --list 'change/*'`、`git worktree list`、`gh pr list --base v2`。
  spec 存在不等于有人实现；同变更分支已存在先核实归属，不重复开工，不清理其他会话资源。
- 完整变更一个仓库外 worktree，通常 `~/.claude-worktrees/<id>`；不放仓库内部，防 watcher 重载风暴。
  worktree 缺少被忽略的 `.env` 与依赖时按 skill 安全准备，不在输出中暴露配置；文档变更无需复制密钥。
- worktree 隔离文件与索引，不隔离测试库/端口。keywordiq_test 全局共享，drop 前查 pg_stat_activity。
  全量集成夹具会 TRUNCATE 业务表，不往共享测试库存要保留的报表；计数断言必须带作用域。
- 主仓才用默认 dev 端口；worktree 派生独立端口，不抢他人服务。长任务在 tasks 留执行者/时间/进度。
- 提交前看 status 第一列与 cached diff；按本次路径提交，不能裸 commit 卷入他人 staged 文件。
  已 staged 后磁盘变化的内容与实际提交不一致时，先解决再验证，不能用磁盘绿证明另一份索引内容。
- PR 创建后保留分支/worktree 等审核与 CI；仅合并后清理，本地分支用 -d，不为省事 -D/--force。
  合并使用 merge commit，不 squash/rebase；有未跟踪业务文件或新提交保留资源并报告。
- 开工扫描孤儿不等于自动删除；分支缺失、归属、文件、活动会话逐项核实，保护其他会话。
  合并后主工作区必须干净再同步/归档；不允许 worktree 删除自身。

### 手动交付入口

- [`/deliver-pr`](.claude/commands/deliver-pr.md)：检查、提交、推送、创建/更新 PR，保留审核资源，不合并。
- [`/fix-review`](.claude/commands/fix-review.md)：取齐评论/review/行内反馈，逐条核实、修复、验证、回复并核当前 CI。
  不自动发起下一轮审核或合并；相应授权只对当次有效。
- [`/close-change`](.claude/commands/close-change.md)：按当前 SHA 审核证据和 CI 门槛合并、归档、关闭已兑现 Issue、清理。
  包含当前 SHA 的 fix-review 结论例外与已合并重跑规则，**执行前完整读 command，不能靠摘要跳过条件**。

## 验证与开发 harness

Codex 等环境不受 Claude Code PreToolUse hook 保护，必须主动跑检查；详见 AGENTS.md。
按影响面先跑 focused，提交前跑对应全量一次；全量通过后无新改动/失败不反复重跑。

| 改动范围 | 提交前检查 |
|---|---|
| app/scripts/test/alembic 或顶层 Python | `uv run ruff check .` 与 `uv run pytest test/ -q` |
| frontend | `cd frontend && pnpm lint && pnpm tsc && pnpm test` |
| extension | `cd extension && pnpm run typecheck && pnpm run test` |
| 只有文档/OpenSpec | 文档差异、引用/覆盖核对；OpenSpec 有改动时 strict validate，不要求业务全量测试 |

- 前端可用 `vitest related` 缩小影响，但 `.umirc.ts`、vitest.config.ts、setup.ts 不在模块图时不能以 0 用例当通过。
- 单元用小 fixture，不依赖真实 PG/生产 CSV；数据库正确性用真实 PG 集成，导入路径另跑真实体量 verify-import。
  报告 passed/skipped 与数据库是否实际执行，不能把“单元全绿”写成“全量数据库回归通过”。
- Claude Code hook 按实际提交目标仓库与路径取改动；检查并发但报告按输入顺序，300 秒超时算失败。
  hook 仅卡提交，不阻止工作中停下问问题；本地测试库连不上时可跳集成，不能隐瞒跳过。
- 文件内静态规则交 ruff；跨文件一致性、死配置、丢栈日志交 `test/test_conventions.py`，不重复造检查器。
  新手写检查必须有正反 fixture；基线新增违规失败，修好必须移除登记，不成为永久豁免。
- `start-change` 管认领/隔离，`local-db` 管真库回归，`verify-import` 管端到端导入；对应文件在 `.claude/skills/`。
  是否存在自动检查与本次是否实际执行必须分开陈述，测试不能证明真机/线上集成已验收。

## CLAUDE.md 的维护纪律（#142）

主文件目标 700～780 行、上限 800 行；同时检查实际文本量，不靠去空行或长行拼接凑数。
字数预算服从有效约束完整性；超预算时优先去重或迁出细则，并维护开工必读表，不能直接砍红线。

### 经验晋升

一般经验首次记共享 Issue 或版本化 OpenSpec，记录问题/根因/范围/证据；本地 worklog 不作为唯一依据。
同根因两个独立事件才默认晋升，先检索并附两次来源；同次修复的多轮评审/重试不重复计数。
数据损坏、越权、生产误操作及用户明确长期决定首次即可晋升，写来源/理由；推演不能冒充真实事故。
新条目写约束、边界、具体后果及 Issue/change id，优先合并；纠错/过时修正不必等待复发。
详细领域约束写对应指南，主文件保留触发入口；docs/ 默认忽略，新增共享记录须确认未被忽略。

### 审计与处置

用户发起、大版本收尾或发现冲突时审计，不默认建定时任务。记录基准提交/本地差异、范围与未核实项。
每条核对：代码/约束仍有效且无冲突吗；工具覆盖哪些 Agent/路径、何时实际运行/跳过；反例与原失败条件仍成立吗。
无近期事故、低命中或“已有测试”都不单独构成删除依据；Claude 专属 hook 与可能跳过的集成不算全环境兜底。

| 处置 | 判据 |
|---|---|
| 保留/压缩 | 仍影响正确性/授权/决策，或自动覆盖有缺口；保留约束、必要理由、边界与检查入口 |
| 迁出/降级 | 领域细则按需读并留强制入口；历史取舍入共享归档，不继续作为当前参数依据 |
| 删除 | 能力失效或重复且另有唯一有效来源；工具完全覆盖时也不能连带删有效设计理由与边界 |
| 修正/补充 | 内容错误、冲突、边界不足或出现新缺口，按当前证据修正 |

删改清单逐项列原条目、证据、去向、确认状态与执行结果。#142 首轮逐条处置仍需逐条确认；
本次用户已批准的压缩方案授权去重和迁移，已执行项列入变更清单，其他删除建议不得借此自动执行。
记录哪些仅核对文档、哪些验证过实现，不把文档迁移称作生产或业务全量审计。同步核对 AGENTS 审核口径。

### 完整流程的双向覆盖

只约束新完整流程变更，轻量两档与已归档变更不补矩阵；沿用 tasks 内联标注，不新增独立矩阵文件。
行为需求引用 delta 的 Requirement/Scenario，design 只补技术依据；支撑任务引用其服务需求/批准决策。

```text
- [ ] 1.1 <任务>（需求：specs/<能力>/spec.md → <Requirement>/<Scenario>；设计：design.md §<章节>）
  证据：<实现文件/符号或文档位置>；验证：<已跑检查/结果，或待验收项去处>
```

PR 审核、合并前从需求查 task/实际改动/验证，反向从 diff 查批准范围；特别查非目标和砍掉功能是否被实现。
漏做补实现；超纲移除或先获用户批准再同步 Issue/spec/tasks，不能靠事后补 task 获准。
证据对应审核 head SHA，后续修改重核受影响部分；归档再核合并结果与验收转移，不能归档时才首次检查。
发现偏差停止关闭 Issue、留清晰去向；已实现、已验证、待人工验收分别记录。
