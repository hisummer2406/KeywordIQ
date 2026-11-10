## MODIFIED Requirements

### Requirement: 首次入榜筛选与当期数量同一口径

查询接口 SHALL 提供布尔筛选 `first_listed`（取代原 `is_new`），命中口径与 `keyword-first-seen` 的日周映射一致：仅来源为「导入创建」且首次日期晚于站点监测起点的关键词，日视图按日、周视图按 `[w-6, w]` 窗。`first_listed` 为 `true` 时 SHALL 计入“带筛选”（掉榜行随之消失，沿用既有规则）；`false` 与未传 SHALL 等价于不筛。系统 SHALL 提供按 (站点, 视图, 日期) 查询「首次入榜」数量与统计范围的接口（`GET /api/analysis/first-listed`），数量按同一口径实算，与仅施加 `first_listed` 筛选的列表总数一致；所选期次不晚于站点监测起点（日视图 `d ≤ B`；周视图窗口起点 `w-6 ≤ B`）时，数量 SHALL 以“不可判断”表达而不是 0，且 `first_listed` 筛选在该期次上 SHALL 不命中任何词。导出 SHALL 与查询共用同一口径。

查询与导出 SHALL 另提供互斥的首次口径参数 `first_listing`：`MONITOR` 即上述共享口径（首次进入监测），`PERIOD` 为 `keyword-first-seen`「首次进入当前榜单按周期独立派生」（日视图首次进入日榜，周视图首次进入周榜）。`first_listed=true` SHALL 等价于 `first_listing=MONITOR`，两者 SHALL 规范为同一有效口径后参与筛选、总数、带筛选判定、结果缓存键与导出；`first_listed=true` 与 `first_listing=PERIOD` 同时出现 SHALL 返回 422，MUST NOT 取交集或择一。行 SHALL 在原 `first_listed` 与共享首次字段之外，另含当前视图周期的 `period_first_listed` 与周期证据（日期、来源等级），原字段语义不变。数量接口 SHALL 接受 `mode`（`MONITOR` 缺省、`PERIOD`），`PERIOD` 数量与仅施加 `first_listing=PERIOD` 的列表总数一致，不可判断规则按周期监测起点；`PERIOD` 口径下，若所选期次晚于周期监测起点而同站点同周期的上一期（日 `d-1`、周 `w-7`）没有已完成导入，SHALL 返回上一期缺失提示，命中结果不变。导出 SHALL 同时含共享判定列「首次入榜」（列名与含义不变）与当前视图的周期判定列「首次进入日榜」或「首次进入周榜」，口径由列名写明，MUST NOT 只导出一个含混的是/否。

#### Scenario: 筛选与数量一致

- **WHEN** 对某站点某日仅施加 `first_listed=true` 查询
- **THEN** 结果总数等于该日该接口返回的数量

#### Scenario: 周视图计入窗内日来源

- **WHEN** 查看周期 `w` 的周视图，窗 `[w-6, w]` 内有首次记录为 DAY 与 WEEK 的词
- **THEN** 两类词都命中，同一个词只计一次

#### Scenario: 不晚于监测起点的期次不可判断

- **WHEN** 所选日期不晚于站点监测起点 `B`（含回填出来的历史期次）
- **THEN** 接口返回 `judgeable=false` 且 `count` 为空，不返回 0

#### Scenario: 补历史后无需校正

- **WHEN** 补导入更早报告使某些词的首次日期前移，或使监测起点 `B` 前移
- **THEN** 之后的查询与数量立即反映新口径（先到的基线词在 `B` 前移后自动计入），没有预存统计需要重建

#### Scenario: 未知与最早可观测不命中

- **WHEN** 某词来源等级为「未知」或「最早可观测」
- **THEN** 它不被 `first_listed=true` 命中

#### Scenario: 周期口径筛选与数量一致

- **WHEN** 对某站点某日仅施加 `first_listing=PERIOD` 查询
- **THEN** 结果总数等于 `mode=PERIOD` 数量接口返回的数量，结果中每行 `period_first_listed` 为真

#### Scenario: 新旧参数矛盾被拒绝

- **WHEN** 请求同时带 `first_listed=true` 与 `first_listing=PERIOD`（含导出）
- **THEN** 返回 422，不执行查询，导出不开流

#### Scenario: 旧参数与新参数同一缓存键

- **WHEN** 两个请求其余条件相同，一个带 `first_listed=true`，另一个带 `first_listing=MONITOR`
- **THEN** 两者结果相同且命中同一结果缓存键；带 `first_listing=PERIOD` 的请求使用不同的键

#### Scenario: 上一期缺失提示

- **WHEN** 查看 10/31 日视图的 `mode=PERIOD` 数量，而该站点 10/30 日报没有已完成导入
- **THEN** 返回数量的同时给出上一期缺失提示

### Requirement: 品牌排除仅按当前行的前三品牌判断

`exclude_brand_in_keyword` 为真时，系统 SHALL 排除关键词文本整词包含该行前三品牌名任一的行，
不区分大小写，品牌名按字面而非正则；NULL 或少于 2 字符的品牌不参与，不能用全站品牌集合替代。
该开关 SHALL 计入带筛选路径，false 与未传等价。界面默认启用的品牌排除 SHALL 仅在选择了任一首次口径（首次进入当前榜单或首次进入监测）时发送。

#### Scenario: 不误用其他行品牌

- **WHEN** 关键词包含某个品牌名，但该品牌不属于本行前三品牌
- **THEN** 该行不因本开关而被排除

#### Scenario: 两种首次口径都随默认排除品牌词

- **WHEN** 用户选择首次进入日榜且未关闭品牌排除
- **THEN** 请求带 `exclude_brand_in_keyword=true`
