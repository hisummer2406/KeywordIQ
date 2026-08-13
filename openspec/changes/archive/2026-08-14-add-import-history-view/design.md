## Context

导入批次记录（`analysis.import_batch`）目前只有一个读取出口：`GET /api/upload/processing-status`，
写死 `.limit(5)`、无分页参数、响应形状是 `{"items": [...]}`（没有 `total`）。前端上传屏用
`pagination={false}` 的表格渲染它，每 5 秒整表轮询覆盖。

这套东西作为「盯进度的看板」是合适的，作为「查历史」则完全不可用：第 6 条以后够不着，
且界面不告诉用户列表被截断了。

同时 `import_batch.object_key` 已经持久保存了每次导入的原始文件位置（OSS-first 导入的既定
设计，见 `openspec/specs/oss-first-ingestion/spec.md` 的「OSS 对象即原始归档」），但界面上
没有任何途径消费它——修一次失败导入要重传一遍 GB 级文件。

现状还有一处与仓库分层约定不符：`/processing-status` 的 `select(ImportBatch)` 直接写在
`upload_api.py` 的路由函数体里，没有经过 crud/service 层。

**约束**：`import_batch` 行数很小（每天个位数导入），查询代价可忽略；重导必须复用既有导入
内核（串行锁 + 排队接手），不得另起一套。

## Goals / Non-Goals

**Goals:**

- 导入历史可分页、可按站点/周期/状态筛选，任意一条历史记录都够得着
- 失败批次可凭已留存的 OSS 对象重导，不必重传原始文件
- 补齐 `import_batch` 的 crud/service 分层，两个读取入口共用一份查询代码
- 上传屏看板行为完全不变

**Non-Goals:**

- 不做批次详情视图（错误信息在列表里读得到即可）
- 不做删除批次记录
- 不做 `COMPLETED` 批次的重导（见决策 3）
- 不做报告日期/导入时间的范围筛选与文件名搜索（三项枚举筛选已覆盖主要诉求；真需要时再加）
- 不做 `import_batch` 的保留清理（行数小，且事实表的三个月窗口不覆盖它——已知，本次不处理）
- 不改导入内核 `run_import` / `import_report` 的任何逻辑

## Decisions

### 决策 1：新增 crud + service 两层，把既有内联查询一并归位

新增：

- `app/table/upload/import_batch_crud.py` —— 纯查询组装，不 commit。
  `list_batches(session, *, site, period, status, page, per_page) -> tuple[list[ImportBatch], int]`、
  `recent_batches(session, *, period, limit)`（`/processing-status` 现有查询搬过来）、
  `get_batch(session, batch_id)`。
- `app/table/upload/import_history_service.py` —— 参数规整与校验（站点码经
  `normalize_site_code`、周期与状态按 `VALID_PERIOD_TYPES` / `VALID_STATUSES`）、
  重导的准入判定与触发。事务边界在这一层。

`/processing-status` 改为调用 `recent_batches`。**替代方案**：只为新端点写 crud、
让 `/processing-status` 保持内联。否决——同一张表两处各写一份查询，改字段时漏改一处
的表现是「两个屏显示的东西对不上」，而这正是把状态文案抽成共享组件要解决的同类问题。

### 决策 2：列表走 ProTable 契约，与用户管理列表同形

`GET /api/upload/batches` 用既有的 `app/common/responses.protable()` 适配器返回
`{data,total,success}`，参数命名沿用 `/api/user/table` 的 `page` / `per_page`。
失败走统一错误契约的非 2xx（错误契约一节的既定要求：列表契约只表达成功）。

`per_page` 上限 100，超限**拒绝**而非截断。截断产出的分页与请求的分页不一致，界面据此
算页数会错位——与导出的「超上限拒绝、不静默截断」是同一条理由。

### 决策 3：只允许重导 `FAILED`，且新建记录

**只允许 `FAILED`**：重导会先删除该 (站点, 周期, 期次) 的现有事实切片再重写（同一期重复
导入的既定行为）。对 `COMPLETED` 批次做这件事，意味着一份正在被查询使用的数据先消失、
再花几分钟（周报 300 万行更久）重建。收益是「源文件更新了想重灌」，这个诉求可以由重新
上传满足，而重新上传不会误触。`QUEUED` / `PROCESSING` 本就在执行中，重导无意义。

因此重导**不需要二次确认**：失败导入整体回滚，该切片本就没有数据，重导不删任何东西。
破坏性确认是为 `COMPLETED` 准备的，而它已被排除。

**新建记录而非改写原记录**：批次记录的是「一次执行」。改写原记录会抹掉失败原因，而失败
原因是判断「该不该重导」「重导后是否真修好了」的唯一依据。代价是同一份文件在历史里占多行
——这正是历史该有的样子，与事实表只追加同源。

### 决策 4：重导前的准入判定全部在触发后台任务之前完成

顺序：取批次 → 状态必须 `FAILED`（否则 409）→ `object_key` 非空（否则 422）→
`oss_client.is_key_within_allowed_prefix`（否则 400）→ `oss_client.stat_object` 确认对象
仍在且未超 `MAX_FILE_SIZE`（否则 4xx）→ 交后台。

后三步与 `POST /oss/import` 是同一套校验，**提取为共用函数**而不是复制。`stat_object` 是
阻塞的 oss2 调用，走 `run_in_threadpool`（与 `/oss/import` 一致）。

判定必须前置的理由：全局错误处理器不拦截 `BackgroundTasks`。校验放到后台任务里，用户会
看到一个「已触发」然后什么也不发生，失败只留在日志里。

### 决策 5：重导不引入新的并发机制

重导调用同一个 `import_service.run_import`，因此自动继承串行锁：拿不到锁置 `QUEUED`，
由 `import_queue` 的轮询任务接手。重导批次带 `object_key`，接手时能重建 `OSSObjectSource`
——不会落入「重建不出数据源的批次留在 QUEUED」那个死标签陷阱。

**替代方案**：给重导单开一条快速通道。否决——两个 GB 级导入并发会争抢 WAL 与索引构建，
这正是串行锁存在的原因，重导没有豁免的理由。

### 决策 6b：导入历史是「数据上传」的二级菜单，布局改 `mix` + `splitMenus`

上传与查历史是同一件事的两个动作，做成两个平级的一级菜单会让它们看起来互不相干。
所以「数据上传」成为带子路由的父级，左侧二级菜单为「上传文件」`/upload/file` 与
「导入历史」`/upload/history`；`/upload` 重定向到前者，旧书签不落空。

这要求布局从 `top` 改为 `mix` + `splitMenus: true`（写在 `src/app.tsx` 的运行时配置里
——`.umirc.ts` 的 `layout` 只有 title/locale/formatMessage 三个键会生效）。

**当初选 `top` 的理由并未被推翻**：分析查询那张宽表有 8 列定宽约 790px，常驻侧栏吃掉的
约 208px 会直接换成横向滚动条。`mix` 下只有带子菜单的一级项才展开侧栏，分析查询没有
子项，进去时左侧不占地方。**替代方案**：保持 `top`，在上传屏内部做页内子导航。否决——
那不是菜单，用户在顶栏看不出「数据上传」下面还有东西。

### 决策 6：前端历史屏不轮询，状态文案抽成共享组件

历史屏是查询屏：条件筛选 + 分页 + 重导后 `actionRef.reload()`，另有手动刷新。盯进度的
职责留在上传屏那块 5 秒轮询的看板——重导新建的记录是最新的一条，本就会出现在看板前 5 条里。
两个屏各司其职，不重复造轮询，也避免「翻到第 3 页被轮询刷回第 1 页」。

`pages/Upload/index.tsx` 里的 `STATUS_META`（四状态的中文名/颜色/`QUEUED` 说明）抽到
`frontend/src/components/ImportStatus/`，两屏引用同一份。不抽的话同一个状态会在两个屏上
叫不同名字。

### 决策 7：数据库不变更

`import_batch` 表结构不动，**无 Alembic 迁移**。现有三个索引已够用：

- 列表默认排序与分页走 `idx_import_batch_created`（`created_at`）
- 按站点/周期筛选走 `idx_import_batch_site_period`
- 按状态筛选走 `idx_import_batch_status`

三项筛选组合时规划器可能只用其一再过滤，但该表行数是千级以内，全表扫描也在毫秒级，
不值得为此加复合索引。

## Risks / Trade-offs

- **[重导按钮成为误操作入口]** → 限定 `FAILED` 使误操作的最坏后果是「重跑一次本就没成功的
  导入」，不破坏任何现有数据。这是不加二次确认的前提，两者绑定：若将来放开 `COMPLETED`，
  必须同时加确认。

- **[同一文件在历史里占多行，看起来像重复]** → 列表展示导入时间与状态，多行是不同次执行的
  如实记录。若将来行数变多影响可读，再考虑按 (站点, 周期, 期次) 分组折叠——但那是展示层
  的事，不改记录方式。

- **[`stat_object` 让重导端点多一次网络往返]** → 走线程池，不占事件循环；换来的是「不产生
  注定失败的后台任务」。重导是低频操作，这个代价可以忽略。

- **[连接池占用]** → 列表查询走异步引擎（与其余查询端点一致），单次查询、无游标、无长事务。
  重导端点自身只做 `head_object` 与一次批次写入；真正的导入在后台任务里，占用与既有导入
  完全相同（一条同步连接 + 锁专属连接一条），不因本变更增加。

- **[内存占用]** → 列表每页至多 100 行的小对象，与导入路径无关。重导不读文件内容，
  只 `head_object`。

- **[`import_batch` 无限增长]** → 已知，本次不处理。历史屏会让这件事变得可见，届时按需
  补一个保留策略即可。行数量级（每天个位数）距离成为问题还很远。
