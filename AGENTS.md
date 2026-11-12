# AGENTS.md

通用工作指令的唯一入口是 [CLAUDE.md](CLAUDE.md)；先读它，再按影响范围读取 `.claude/rules/`。
这些规则是普通 Markdown；不要假设 Codex 会执行 Claude Code 的 paths 自动加载机制。
本文件只保留 Codex 环境适配和自动审核所需的投影，不维护另一份通用规则或产品功能说明。

## Codex 环境适配

- `.claude/settings.json` 的 PreToolUse 提交 hook 不保护 Codex；按 CLAUDE.md 的命令主动执行相应检查。
- `.claude/skills/` 和 `.claude/commands/` 直接作为 Markdown 阅读，按当前用户授权执行。
- 澄清问题一次问完；AskUserQuestion 只是另一运行环境的工具名，不构成额外流程。
- 专用审核规则必须保留在本文件；相关约束变更时与局部规则/spec 同步核对。

---

## Code Review Rules

> 本节是 Codex 自动审核的运行时配置：Codex 审核只读 AGENTS.md，不读 CLAUDE.md。
> 内容是 CLAUDE.md 对应条目的审核视角浓缩；CLAUDE.md 相关约定变更时**须同步更新本节**，
> 防止两份副本分叉。只标记结构性、数据正确性问题；格式与 lint 归 CI 管，不要报。

### 数据写入纪律

- **事实表（`fact_daily` / `fact_weekly` / `fact_dropped` / `fact_growth`）只追加，不得原地 UPDATE。**
  排名类字段全部在写入时算定，这是按月归档与死元组归零的前提。唯一例外是同一期重复导入
  （先删该期切片再重写）。对事实表的 UPDATE 一律标记。
- **维度/价格数据仅在取值真变时写入。** `dim_product` 的 UPSERT 带
  `WHERE ... IS DISTINCT FROM EXCLUDED.*` 不是冗余；`competitor_store_price` 仅在
  price/compare_at_price/available 变化时追加。删掉这类守卫造成约 30 倍写放大，且测试照样绿。
- **站点码唯一事实来源是 `app/site_registry.py`**（英国是 `GB` 不是 `UK`）。别处硬编码站点
  列表、为站点元数据建库表，都是开第二事实来源。非法/缺失站点码必须报错（422），
  不得回退 `DEFAULT_SITE`。
- **`is_new` 列、DEFAULT false 与其部分索引在 expand/contract 阶段必须保留。** 旧容器仍引用，
  提前 drop 列/索引会让旧版本报错；新请求只用 `first_listed`，删除须走独立 contract 迁移。

### 分区与迁移

- **写事实表前必须调用 `app.table.analysis.partitioning.ensure_fact_partitions`**（幂等，无条件调）。
  新增分区表必须登记进 `PARTITIONED_TABLES`；竞对监控的普通表（`competitor_*`、
  `user_store_watch` 等）**不得登记**——登记即被按月保留清理整月 DROP，而价格与名次历史
  越久越值钱（三个月窗口是为八亿行 ABA 事实表定的）。
- **分区表与物化视图的迁移必须手写。** autogenerate 会把事实表产成普通表并静默丢掉
  `PARTITION BY`。新增模型必须登记进 `migration_scope.py` 的 `V2_TABLES`，漏登的表
  autogenerate 静默忽略、不报错。
- **列注释参与 autogenerate 比对**，模型注释与库不一致会产生常驻假差异，掩盖真差异
  （曾盖住 `user_center` 时间戳丢时区）。
- **保留清理（`retention_service.run_retention`）同步清理
  `site_data_stats`/`category_stats`/`brand_stats` 中超期的残留行，且无条件执行（不
  依赖本次是否真的 DROP 了分区）。** `category_stats`/`brand_stats` 只按
  `report_date` 过滤、不带 `site`/`period_type` 等值条件，靠迁移
  `47df76b2f424` 专门补的 `report_date` 单列索引才不退化成全表扫描——这两张表按
  (站点,周期,日期,id) 分片、单站点单日品牌基数约 16 万行，删掉这个索引会让扫描代价
  变成随整表行数（千万级）同阶，不是随残留月份数（issue #100 PR #101 复审 P2）。
- **保留清理的写入路径（非 dry-run）必须持有与导入共用的咨询锁**
  （`import_service.import_lock`/`IMPORT_LOCK_KEY`）。导入的事实写入与
  `site_data_stats` 回写是两次独立提交，没有这把锁，保留清理可能恰好在两次提交之间
  跑完"移出分区+清理统计+提交"，先 DROP 掉刚写完事实行的分区，再被导入的后续提交
  写入一条全新统计行——事实已经不存在，统计却说有，是 `total>0, data=[]` 的另一条
  触发路径（issue #100 PR #101 复审 P1）。拿不到锁应直接跳过本次运行（`lock_skipped`），
  不阻塞、不重试，交给下一轮调度。

### 查询与分页

- **分页排序必须带完整次级键**（如上新流的 `source, source_key, site, product_key`）。
  `published_at` 精度有限、同一时刻多条是常态，无次级键时同一行会在两页各出现一次——
  小数据量下测不出来，须对 `ORDER BY` 子句做结构性断言。
- **`fact_weekly` 的分区列是 `report_date_week`，不是 `report_date`。**
  照日报列名写周报查询直接 `UndefinedColumn`。

### 排名趋势详情

- **单关键词历史查询走 `idx_fact_dropped_keyword_history`（`site, period_type, keyword_id, anchor_date`），不得走 `fact_dropped` 主键。** 主键把 `anchor_date` 排在 `keyword_id` 之前，range 查询下 `anchor_date` 是范围而非等值，`keyword_id` 退化成扫描内 filter，代价随该站点/视图窗口内的掉榜行数增长，不随目标关键词的历史行数增长。
- **判定"报表已导入但未收录"前必须先排除 `report_date < retention_cutoff`，且不信任 `site_data_stats` 在保留下界之前的行。** 保留清理现在会同步清理 `site_data_stats`/`category_stats`/`brand_stats` 中报告日期早于保留下界的残留行（issue #100），但判定顺序仍不查 `site_data_stats`——这是双重保险，防的是清理调度尚未执行、或统计短暂残留的间隔；判定顺序反了会把早已被清理的月份误判成"这份数据还在，只是没收这个词"。
- **来源锚点 `on` 必须是打开详情那一刻来源页面正在看的期次，不是"今天"**；切换日/周视图复用同一个 `on`，由后端统一解析映射（取 `<= on` 的最新一期，取不到退化为全局最新一期），不在前端重算。
- **权限接受 `analysis:view` 或 `keyword:view` 任一，仅限"端点跨域共用且不携带任何域私有数据"这种情形。** 读取用户私有数据的筛选不得套用这个放宽，仍需按需叠加校验（原例 `competitor_new` 已于 #166 下架，规则保留给将来的私有筛选）。

### 竞对监控

- **下架判定只在 `catalog_complete` 为真时做**（Shopify：翻页自然终止 + 取到的商品数不少于
  `/meta.json` 声称的 `published_products_count`）。被动 ASIN 上报是推模型，
  "这次没见到"≠"已下架"，不得据此判定下架。
- **新品判定看 `product_id` 在不在库里，不看 `published_at`**——商户批量重新发布会让日期
  整体跳变，据此判定会伪造一批假上新。
- **零条有效 ASIN 的上报整批拒绝（422），不得落定空基线**——否则下一次正常上报会把该卖家
  全部存量商品误判成新发现，界面表现只是数字突然涌出一批，不报错。

### 认证与会话

- **登录成功后的会话签发（生成 `session_id`、写入 `active_session_id`、提交）只有一个入口
  `UserCenterCRUD.rotate_active_session`，本地密码登录与钉钉扫码登录共用。** 两处各写一份
  会在其中一处改动时静默产生行为分歧。
- **`rotate_active_session` 的返回值必须是本地生成的 `session_id` 变量，不得读
  `commit()`/`refresh()` 之后的 `user.active_session_id`。** 并发登录下，`commit` 与
  `refresh` 之间的事件循环让出会让后者读到另一个并发事务写入的值，把错误的会话编号
  签进令牌。
- **`get_current_user` 的会话校验（`session_id` 缺失/为空/不匹配）必须抛 `APIError` 子类
  （`SessionInvalidError`），不得用裸 `HTTPException`。** 后者会被统一映射成
  `auth.unauthorized`，与"令牌过期"用同一个错误码，前端做不出"账号已在其他浏览器登录"
  这句区分文案。
- **主动退出必须是条件更新（`active_session_id` 仍等于本次令牌的会话编号才清空），不得
  无条件清空。** 否则旧会话的退出请求会把并发中已经登录成功的新会话一起清掉。
- **`/logout` 的鉴权依赖不得复用 `get_current_user`，必须用不校验会话匹配的
  `get_current_user_for_logout`。** `get_current_user` 会在会话已不匹配时就直接抛
  `SessionInvalidError`（401），端点函数体永远执行不到，上面那条条件更新因此只在
  同一个请求内部两次数据库往返之间的极窄窗口才真正生效——"旧会话事后才发起退出
  请求"这个更常见、本该由条件更新兜底的场景反而先在鉴权层被拒。判定该不该清、清不清
  得掉，必须整段留给端点里的条件更新，鉴权层只做身份识别。
- **前端任何"收到失败响应就清本地令牌"的路径，清理前都必须比对这次失败请求发起时用的
  令牌是否仍是当前令牌，且这次比对必须紧贴在真正执行清理动作之前（异步操作之后）。**
  不止 `app.tsx` 的全局 `errorHandler`——`getInitialState.fetchUserInfo`、
  `exportAnalysis`（裸 fetch，绕过全局 errorHandler）都各自独立发生这段逻辑，缺一处
  都会在网络延迟下把刚取得的新会话删掉；`await res.json()` 这类比对之后的异步间隙
  必须再判一次，只判一次不够。

### 部署与运维

- **V2 只经 `Deploy V2 to ECS` 工作流发布**（`.github/workflows/deploy-v2.yml`，手动触发）。
  三道门不得放宽或绕开：只允许 `refs/heads/v2`；该 SHA 的 v2 push CI 必须通过：受影响项 `completed/success`；未受影响项仅经同一运行的
  「发布就绪（按范围校验）」门禁核验后允许 skipped，未知范围全量，缺失/失败/取消拒绝；SHA 必须在 `origin/v2` 历史上。工作流、脚本、文档里
  不得出现 SSH 私钥、`.env`、AccessKey、飞书 App Secret 等任何密钥。
- **V1 与 V2 同机隔离，V1 不得被碰。** V1 是裸部署（`amazon.liangtong.online`、8000）；V2 应用
  只绑 `127.0.0.1:8001`（域名 `key.liangtong.online`，Compose 项目 `keywordiq-v2`）。部署
  脚本、健康检查、nginx、logrotate 里出现 8000 或 V1 域名/目录，多半是照抄 V1 的错误。
  `docker-compose.yml` 里所有 `ports` 只能绑 `127.0.0.1`，不得对公网暴露。
- **数据库没有自动备份**（2026-09-20 决定）。方案与文档不得默认"出事可从备份恢复"，也不得
  把 ops-bot 的定时备份/「备份逾期」告警加回去而不经用户决定。
- **ops-bot（`ops_bot/`）与业务 `app` 相互独立。** `重启`/`发布` 必须走确认卡片、只做预定义
  动作，生产库恢复不得作为飞书指令开放；发布只接受完整 40 位 SHA 且 CI 发布门禁通过，导入进行中
  一律拒绝。`import_service._OPS_ACTION_LOCK_KEY` 与 `ops_bot/executor` 的锁 key 是两边各自
  定义的同一个值，只改一边会让重启/发布与导入的互斥静默失效。ops 表登记进
  `migration_scope.OPS_TABLES`，不是 `V2_TABLES`。
- **连接池算式要把 ops-bot 算进去**：业务 84 + ops-bot 最多 12 = 96，对 `PG_MAX_CONNECTIONS`
  （120）的余量只剩 24。上调任一组池之前先重算。

### 浏览器插件（`extension/`）

- **`manifest.json` 与 `package.json` 的版本必须一致，且 manifest 必须保留 `key` 字段**——
  前者是打包脚本与 `test/manifest.test.ts` 拦的；后者固定扩展 ID，删掉它运营挪动解压目录
  后设置与 host 权限整体丢失，且不报错。私钥不得入库。
- **插件的跨域请求靠 `optional_host_permissions` 放行，不依赖后端 CORS。** 不得因"要收紧
  CORS"而给插件开白名单，也不得在 manifest 里写死某个 `host_permissions`（API 地址是运营在
  设置页填的，按需弹权限框申请）。
- **一次上报必须是多页合并后的一次请求**，不得改成逐页上报——第一页会单独落定该卖家的基线，
  其后的存量商品被误判成「新发现」（与「竞对 ASIN 上报」的基线约束同源）。

---

## 维护

通用约定维护在 `CLAUDE.md`，局部约束维护在 `.claude/rules/`，业务行为维护在 OpenSpec。**本文件不新增通用约定**；
只有当某条约定确实只对某一种运行环境成立时，才在本文件加一条差异说明。
（「Code Review Rules」一节属后者：它是 Codex 审核运行时的专属配置，随 CLAUDE.md
对应局部规则/spec 同步维护。）

## 当前部署交接

执行或修改 V2 ECS、OSS、飞书运维机器人相关工作前，先阅读
[`docs/ops/v2-ecs-handoff.md`](./docs/ops/v2-ecs-handoff.md)。该文件记录当前环境状态与接手顺序；密钥不记录在仓库。
