> 认领：2026-11-10，分支 `change/add-pricing-calculation`，worktree `~/.claude-worktrees/add-pricing-calculation`。提案已完成，等待用户确认后开始实现。

## 1. 站点与权限底座（D11、D12）

- [ ] 1.1 `app/site_registry.py` 的 `Site` 增加 `currency`、`currency_minor_unit`（US=USD、GB=GBP、DE=EUR，三者最小单位均为 0.01），更新调用方的构造与测试
- [ ] 1.2 `app/auth/permissions.py` 新增 `pricing:view`、`pricing:manage-rates`，按 D11 的角色矩阵授权；对应的 Alembic 种子同步写入
- [ ] 1.3 测试：权限矩阵与种子迁移一致；admin 获得两项且两项都不在 `SUPER_ONLY_PERMISSIONS` 中；`uv run pytest test/ -k "permission or site_registry"`

## 2. 计算引擎（纯函数，D1–D7、D13；Req「单一正算模型」「反推与保本价」「十进制定点」）

- [ ] 2.1 `engine/types.py`：场景输入、已解析规则、方案结果、状态枚举、费用明细项（金额、币种、计费基础、规则引用）；定义 `MODEL_VERSION`
- [ ] 2.2 `engine/money.py`：Decimal 上下文、入参转换（拒绝 float）、按最小单位向上或向下取整
- [ ] 2.3 `engine/tax.py`：含税拆分、佣金阶梯（整价、超出部分、最低佣金）、平台费用附加、广告、退货、结汇预留
- [ ] 2.4 `engine/fba.py`：尺寸分段判定（三边排序、周长、体积重）、配送费查表（重量区间、价格段、续重）、燃油附加、旺季费、仓储费、入库配置费、头程、手填配送费兜底
- [ ] 2.5 `engine/fbm.py`：渠道适用性（属性、重量、尺寸、容量计量对象、停用）、计费重（起重、进位、体积重）、区间运费、附加费、关税方式
- [ ] 2.6 `engine/duty.py`：申报价值、关税、不可抵扣的进口增值税；税率未填写时为待补资料
- [ ] 2.7 `engine/forward.py`：正算模型，输出利润、利润率、投资回报率和逐项明细；分母为 0 时不给百分比
- [ ] 2.8 `engine/solve.py`：枚举分段点、段内线性求解、取最小可行解、取整、复核循环；采购价上限求解；无解状态
- [ ] 2.9 `engine/compare.py`：按市场分组，可用、完整、达标三种状态分别判定，推荐与目标差距，竞品价格可比性
- [ ] 2.10 测试 `test/test_pricing_engine_unit.py`：PRICE-01～04、GB 含税场景、PRICE-05/06/07/12/13（进位、起重、体积重、价格段跳变两侧）、PRICE-14（无解、零收入）、float 被拒绝；每个反推用例都断言代回正算达标；`uv run pytest test/test_pricing_engine_unit.py -q`
- [ ] 2.11 性质测试：随机生成的规则和场景下，「反推售价代回正算 ≥ 目标」且「售价减一个最小单位 < 目标」恒成立（用标准库随机数加固定种子，不新增依赖）

## 3. 存储与迁移（D8、D9；迁移方案见 design）

- [ ] 3.1 `pricing_model.py`：5 张表，注释与 design 一致，登记到 `V2_TABLES`
- [ ] 3.2 手写 Alembic 迁移：建表、索引、`(kind, scope_key, version)` 唯一、`(sheet_id, client_token)` 唯一、权限种子；downgrade 完整回退
- [ ] 3.3 `rule_schemas.py`：五类载荷的 Pydantic 判别联合（严格模式、拒绝多余字段、金额用十进制字符串），以及 `payload_schema` 版本
- [ ] 3.4 真库验证：迁移到 head 后对比模型与库无差异；上下行各跑一次（按 `local-db` skill，库为 `keywordiq_test`）

## 4. 服务与接口（D10；Req「规则版本」「启用校验与互斥」「核价记录私有」「草稿与不可变版本」「选定方案」「导出」）

- [ ] 4.1 `rule_service`：草稿增删改查、启用（`FOR UPDATE` 后检查重叠和完整性）、停用、删除未被引用的草稿、按核价日期解析规则
- [ ] 4.2 `sheet_service`：试算、草稿（`lock_version`）、保存版本（服务端重算、`client_token` 幂等）、复制、重新核价、规则更新标记、选定和选定日志
- [ ] 4.3 `pricing_api.py`：端点逐个校验权限；错误码 `pricing.*` 走 `APIError`；列表分页；挂到 `api_router`
- [ ] 4.4 CSV 导出（UTF-8 BOM，流式，在开流前完成错误判定）
- [ ] 4.5 测试 `test/test_pricing_api_integration.py`（真库）：PRICE-08/09/10/11（后端部分）/14（重复提交、并发冲突）/15；重叠启用返回 409；修改已启用规则返回 409；选定不可选方案返回 422；他人记录返回 404；`uv run pytest test/test_pricing_api_integration.py -q`

## 5. 初始数据脚本（D14；Req「原表导入只生成草稿」）

- [ ] 5.1 `scripts/pricing_import_excel.py`：读取原表的自发货渠道，写入草稿和来源单元格，冲突写进审核说明；支持 `--dry-run`
- [ ] 5.2 `scripts/pricing_seed_drafts.py`：按官方费率卡生成 US、GB、DE 的市场参数、标准类目佣金、FBA 费率草稿（2026 当期版本，`source` 填官方 URL 和生效日），以及汇率草稿；执行前逐项和官方 PDF 核对
- [ ] 5.3 测试：PRICE-16（原表导入的条数、来源位置、冲突说明、全部为草稿）；种子载荷都能通过 3.3 的校验

## 6. 前端工作台（Req「工作台页面」「结果过期提示」）

- [ ] 6.1 `services/pricing.ts` 与类型；`access.ts` 增加 `canViewPricing`、`canManagePricingRates`；`routes.ts` 换成新权限，并更新 `routes.test.ts`
- [ ] 6.2 快速核价：产品与成本、包裹、货物属性、市场多选、核价模式、场景覆盖项、关税、FBA 手填配送费、竞品价格；按模式校验
- [ ] 6.3 结果区：按市场分组的比较表、状态和原因、展开明细、调价并重新正算、过期提示、保存、选定、导出
- [ ] 6.4 核价记录：分页列表、搜索和筛选、详情和版本差异、复制、重新核价、规则更新提示
- [ ] 6.5 费用配置：五类规则的列表、版本和状态；按类别提供载荷表单（FBA 费率与渠道区间用可编辑表格）；启用、停用、复制为新草稿；按权限显示按钮
- [ ] 6.6 前端单元测试覆盖过期提示、状态展示、权限显隐（前端检查由用户运行：`pnpm lint && pnpm tsc && pnpm test`）

## 7. 文档、规格与交付

- [ ] 7.1 更新 `docs/pricing/README.md` 与 PRD 状态：记录 D3～D7 的默认口径及来由，PRD §10 的待确认项改为指向 design 中对应的决策
- [ ] 7.2 `openspec validate --strict --all`；`uv run ruff check .`；后端定向测试；全量测试由提交门禁执行
- [ ] 7.3 PR（`--base v2`，Refs #171 #172），列出 spec delta
- [ ] 7.4 人工验收（用户在浏览器中进行）：在费用配置中核对草稿并启用 US 的一套规则，完成一次 FBA 和一次自发货核价，结果和卖家后台收入计算器对比，误差不超过 $0.05
