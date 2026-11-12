> 认领：change/automate-search-terms-ingest，2026-11-10 建立方案；第 1、2 组可先做，第 3/4 组只实施闸门选中的一组。

## 1. 路线选定闸门（外部前置，需用户操作）

- [ ] 1.1 用户在卖家后台确认品牌备案状态，注册 SP-API 开发者并申请品牌分析角色；记录审批结果与日期（design 决策 1）
- [ ] 1.2 权限到位后，在本机用一次性脚本（不入生产）分别拉 US/GB/DE 日报、周报样本各一份，记录报表生成耗时、文件大小与日数据延迟（Requirement「自动获取路线须经选定闸门」）
- [ ] 1.3 与同期人工 CSV 对账：行数、搜索词集合、排名、ASIN 槽位、品牌/类目/标题可得性，结论写入 design 决策 2（Scenario「对账发现关键字段缺失」）
- [ ] 1.4 实地确认卖家后台热门搜索词的下载流程（同步/异步生成、文件命名）与条款风险，供路线 B 评估（design 决策 5）
- [ ] 1.5 与用户确认选定路线、截止时间、人工是否完全停传，写回 design 的 Open Questions；未完成本项不进入第 3/4 组

## 2. 共用部分（两条路线都需要）

- [ ] 2.1 Alembic 迁移：`analysis.import_batch` 加 `source` 列（默认 `MANUAL`，CHECK 约束），模型同步；在全新 `keywordiq_test` 上跑迁移升降级（design 决策 3，Requirement「批次记录来源」）
- [ ] 2.2 失败重导继承原批次 `source`；补测试覆盖 Scenario「重导继承来源」「历史批次显示为人工上传」，`uv run pytest test/` 定向通过
- [ ] 2.3 service 层新增应到期次计算（日报当天、周报周六结束日）与去重判定函数，人工路径不调用；补单元测试覆盖 Requirement「按应到期次补齐，不重复导入」各 Scenario
- [ ] 2.4 导入历史接口加来源筛选（白名单校验）与返回字段；补接口测试覆盖 import-history-view delta 三个 Scenario
- [ ] 2.5 导入历史屏展示来源标签与来源筛选（前端检查由用户运行）
- [ ] 2.6 ops_bot 新增缺期检查：截止时间判定、按期次去重告警、补齐后恢复；告警状态表登记 `OPS_TABLES`；补单元测试覆盖 Requirement「缺期与连续失败告警」各 Scenario（design 决策 6）
- [ ] 2.7 配置项：自动获取总开关（默认关闭）、启用的站点 × 周期、截止时间、回看窗口，同步 `.env.example`（Scenario「未完成对账时不启用调度」）
- [ ] 2.8 第 2 组提交前：`uv run ruff check .`、后端测试、`openspec validate --strict --all`；按 deliver-pr 开 PR（base v2）

## 3. 路线 A：SP-API（仅闸门选中时实施）

- [ ] 3.1 迁移新增 `analysis.spapi_report_request`（站点、周期、期次、报表 ID、状态、重试次数、最近错误类别）（design 决策 4）
- [ ] 3.2 LWA 鉴权与 Reports API 客户端：创建报表、查询状态、获取文档；日志与异常不含凭证；补单元测试（Requirement「自动获取的凭证不外泄」）
- [ ] 3.3 流式转换器：gzip JSON → 长表中间 CSV（`ijson`），内存有界；用样本子集做单元测试
- [ ] 3.4 SQL 透视为 21 列暂存布局，品牌/类目/标题留空，空标题不覆盖 `dim_product.title`；集成测试在真库验证
- [ ] 3.5 轮询 job：独立咨询锁单点执行、每轮推进一步、阻塞工作走 `to_thread`；产物落 OSS 归档前缀后登记 `SPAPI` 批次并交 `run_import`；集成测试覆盖 Requirement「自动获取复用现有导入管线」
- [ ] 3.6 FATAL/CANCELLED/数据未产出的退避重试，覆盖 Scenario「数据源尚未产出时不判失败」
- [ ] 3.7 品牌/类目「来源未提供」在查询与界面的口径：先补 `site-scoped-query` 等 delta，再实现，并跑 verify-analysis-contract
- [ ] 3.8 跑 verify-import，确认 SPAPI 批次的日周分区、批次统计、首次入榜与人工批次一致

## 4. 路线 B：插件自动下载（仅闸门选中时实施）

- [ ] 4.1 后端 `upload-credentials` 接受 `source`（白名单）并在 `EXTENSION` 时调用去重；新增只读接口返回应到但缺失的期次；补接口测试
- [ ] 4.2 插件：`chrome.alarms` 定时 + 卖家后台下载模块，新增 host 权限并更新 extension README 与安装文档
- [ ] 4.3 插件直传：分片、续签带 object_key、断点续传、放弃时回收分片，语义与 `frontend/src/services/ossUpload.ts` 一致；插件测试覆盖（用户运行插件检查）
- [ ] 4.4 插件失败上报：在 KeywordIQ 记录失败类别（不含会话信息），供缺期告警引用最近失败原因
- [ ] 4.5 跑 verify-import，确认 EXTENSION 批次与人工批次结果一致

## 5. 试运行与开启

- [ ] 5.1 选定路线在一个站点、一个周期试运行一周，人工照常上传，对比自动批次与人工批次（design Migration Plan 第 3 步）
- [ ] 5.2 用户确认后逐站点开启；人工上传保留为兜底，记录开启日期
- [ ] 5.3 更新运维手册：开关位置、缺期告警的处理步骤、人工兜底方法
