> 认领：2026-11-10，分支 `change/add-pricing-calculation`，worktree `~/.claude-worktrees/add-pricing-calculation`。
> 进度：2026-11-10 任务 1～6 已实现，PR #175；7.4 人工验收未做。

## 1. 站点与权限底座（D11、D12）

- [x] 1.1 `app/site_registry.py` 的 `Site` 增加 `currency`、`currency_minor_unit`（US=USD、GB=GBP、DE=EUR，最小单位均为 0.01）
- [x] 1.2 `app/auth/permissions.py` 新增 `pricing:view`、`pricing:manage-rates`，按 D11 的角色矩阵授权；种子写在迁移 `140193107685` 中
- [x] 1.3 测试：`test/test_rbac_auth_unit.py::test_pricing_permissions_matrix` 及 operator 权限集合断言；真库核对 role_permission 为 admin×2、operator×1、analyst×1

## 2. 计算引擎（纯函数，D1–D7、D13；Req「单一正算模型」「反推与保本价」「十进制定点」）

实现位置与计划拆分不同：税费、佣金、关税、正算、求解、比较合在 `engine/calculate.py`（单一正算模型 `_OptionModel.evaluate` 是唯一的计算入口，拆开反而要在模块之间传递中间状态）；FBA 与自发货分别在 `engine/fba.py`、`engine/fbm.py`。

- [x] 2.1 `engine/types.py`：场景输入、已解析规则、方案结果、状态枚举、费用明细项；`MODEL_VERSION = "1"`
- [x] 2.2 `engine/money.py`：Decimal 转换（拒绝 float 与 bool）、按最小单位向上或向下取整、分母为 0 返回 None
- [x] 2.3 含税拆分、佣金阶梯（整价、超出部分、最低佣金）、平台费用附加、广告、退货、结汇预留（`engine/calculate.py`）
- [x] 2.4 `engine/fba.py`：尺寸分段（三边排序、长度＋周长、体积重及体积重上限）、配送费（重量区间、价格段、续重）、燃油附加、旺季费、仓储费、入库配置费、头程；手填配送费兜底
- [x] 2.5 `engine/fbm.py`：渠道适用性（属性、重量、尺寸、容量计量对象）、计费重（起重、进位、体积重）、区间运费、附加费
- [x] 2.6 关税与不可抵扣的进口增值税；税率未填为待补资料（`engine/calculate.py`）
- [x] 2.7 正算：利润、利润率（不含税收入为分母）、投资回报率、逐项明细
- [x] 2.8 求解：枚举分段点、段内线性求解、取整后复核并向下/向上确认最小售价与最大采购价；无解状态
- [x] 2.9 比较：按市场分组推荐，可用、完整、达标分别判定，竞品价可比性
- [x] 2.10 `test/test_pricing_engine_unit.py`：PRICE-01～04、GB 含税、PRICE-05/06/07/12/13、价格段跳变、整价阶梯跳变、无解、零收入、拒绝 float。反向变异：段内解改为段起点 44 例失败、去掉向下确认 19 例失败、佣金减半 10 例失败、不扣增值税 1 例失败
- [x] 2.11 性质测试 40 个种子：反推售价代回正算 ≥ 目标，且减一个最小单位 < 目标

## 3. 存储与迁移（D8、D9）

- [x] 3.1 `app/table/pricing/pricing_model.py`：5 张表，登记 `V2_TABLES`；`scripts/verify_selfhosted_migration.py` 的核对清单同步纳入
- [x] 3.2 迁移 `alembic/versions/140193107685_定价核算表与权限点.py`：建表、索引、唯一约束、检查约束、权限种子；downgrade 删除表与种子
- [x] 3.3 `rule_schemas.py`：五类载荷严格校验（拒绝多余字段与 float，TypeError 转为校验错误，避免穿透成 500），`payload_schema = 1`
- [x] 3.4 真库（pgbase 5433 `keywordiq_test`）：upgrade → downgrade -1 → upgrade 通过，`alembic check` 无差异

## 4. 服务与接口（D10）

- [x] 4.1 `rule_service.py`：草稿增删改查、启用（`FOR UPDATE` 后检查重叠）、设定失效日、停用、复制为新草稿、按核价日期解析
- [x] 4.2 `sheet_service.py`：试算、草稿（乐观锁）、保存版本（服务端重算、`client_token` 幂等）、复制、规则更新标记、选定与选定日志
- [x] 4.3 `pricing_api.py`：逐端点挂权限；错误码 `pricing.*`；列表分页；挂到 `/api/pricing`
- [x] 4.4 CSV 导出（UTF-8 BOM，开流前完成校验与取数）
- [x] 4.5 `test/test_pricing_api_integration.py`（真库 14 例）与 `test/test_pricing_api_unit.py`（逐路由权限、输入校验 3 例）：PRICE-08/09/10/14/15、重叠 409、已启用不可改、不可选方案 422、他人记录 404

## 5. 初始数据脚本（D14；Req「原表导入只生成草稿」）

- [x] 5.1 `scripts/pricing_import_excel.py`：只用标准库读 xlsx；导入 US/GB/DE 共 36 条渠道草稿，其余国家 46 行计数报告（合计 82 行，与 PRD 一致）；冲突、停发、+24 写进审核说明；支持 `--dry-run`
- [x] 5.2 `scripts/pricing_seed_drafts.py`：14 条草稿（3 个市场参数、3 份佣金、GB/DE 各 3 个 FBA 生效期版本、US 2 个）；来源见脚本头部，US FBA 为二手来源须复核；不预置汇率
- [x] 5.3 `test/test_pricing_seed_unit.py`（8 例，含按官方表抽查的配送费）、`test/test_pricing_import_excel_unit.py`（3 例，构造 xlsx，不依赖业务原表）；两个脚本在 `keywordiq_test` 上各跑两次，第二次全部跳过

## 6. 前端工作台（Req「工作台页面」「结果过期提示」）

- [x] 6.1 `services/pricing.ts`（含十进制字符串工具）；`access.ts` 增加 `canViewPricing`、`canManagePricingRates`；`routes.ts` 改挂 `canViewPricing`，`routes.test.ts`、`access.test.ts` 已更新
- [x] 6.2 快速核价表单（`pages/Pricing/CalculatorTab.tsx`、`formInput.ts`）
- [x] 6.3 结果区（`ResultPanel.tsx`）：按市场分组、状态与原因、明细、调价重算、过期提示、保存、选定、导出
- [x] 6.4 核价记录（`RecordsTab.tsx`）：分页、搜索筛选、版本抽屉、复制、删除、规则更新提示
- [x] 6.5 费用配置（`RatesTab.tsx`、`RuleEditor.tsx`）：汇率、市场参数、渠道用结构化表单；佣金与 FBA 费率用 JSON 编辑（design 决策 15，与原计划的「FBA 用可编辑表格」不同）
- [x] 6.6 `formInput.test.ts`、`ResultPanel.test.tsx`：百分数换算、未填项不发送、过期提示、状态展示、试算结果不可选定。费用配置按钮显隐只在 `access.test.ts` 覆盖权限派生，没有写 RatesTab 的渲染测试。932d06f 的门禁结果不可靠（PR CI 首轮发现 eslint `no-use-before-define`，且两个测试文件未 mock `@umijs/max` 导致无法加载），5a925e4 修复后门禁的 eslint、tsc、vitest 真正执行并通过；未手动运行

## 7. 文档、规格与交付

- [x] 7.1 `docs/pricing` 不在 git 中（#171 已说明本地文档不作为交付物），D3～D7 的默认口径及来由、实现中的修正统一记录在 design.md
- [x] 7.2 `openspec validate --strict --all`；全量后端测试由提交门禁执行（已在 7224f7d、79b45dd 通过）
- [x] 7.3 PR #175（`--base v2`，Refs #171 #172），列出 spec delta
- [ ] 7.4 人工验收（用户在浏览器中进行）：运行两个脚本生成草稿，录入 USD/GBP/EUR 汇率，在费用配置中核对并启用 US 一套规则，完成一次 FBA 和一次自发货核价，FBA 配送费和卖家后台收入计算器对比，误差不超过 $0.05

## 8. 导航统一为左侧二级菜单（design 决策 16，Req「模块内互不相关的功能以左侧二级菜单组织」「工作台页面」）

- [x] 8.1 `routes.ts`：定价核算拆成三个子路由，`/pricing` 重定向到快速核价；`routes.test.ts` 更新（子菜单、继承权限、重定向；定价核算移出「单页顶层」名单）
- [x] 8.2 页面拆分：`Calculate.tsx`、`Records.tsx`、`Rates.tsx`、`usePricingMeta.tsx`；组件改名为 `CalculatorView`、`RecordsList`、`RatesManager`；删除带页签的 `index.tsx`
- [x] 8.3 打开记录改为地址参数 `?sheet=&version=`；保存后把记录写回地址但不触发重新加载；不带版本号时表单取草稿、结果取最新版本
- [x] 8.4 规格：`specs/top-nav-structure` 新增全系统约定，`specs/pricing-records` 修改「工作台页面」；`.claude/rules/frontend.md` 补一行
- [ ] 8.5 浏览器核对（用户）：顶栏「定价核算」→ 左侧三项可折叠；从核价记录打开后刷新页面仍是同一条；新建核价后地址里的记录参数被清空

