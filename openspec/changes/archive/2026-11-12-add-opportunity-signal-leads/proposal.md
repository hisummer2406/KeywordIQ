## Why

机会档案（change `add-opportunity-dossier`，PR #187 已合并 v2）解决了「研究一个想法」，但档案从哪来仍靠运营自己想词或先去词库建分组。持续增长榜每天按确定性规则选出一批走强的词（生产 US 站 11-10 为 43 个），它们现在停在「关键词工具」里，看完没有下一步；同一个词有没有人已经在研究，也看不出来。

2026-11-12 用户验收机会档案后要求「继续其他专家的开发」，顺序与范围由实现方判断。先做「信号洞察专家」：它不需要新数据源，也不需要新口径。

历史方案（`docs/opportunity-center/`、#54、#122、#138）里的 Signal 收件箱、去重冷却、三类新信号、事件推送都不在本变更内：新口径尚未确认，本变更只把**已有的持续增长榜**接到档案上。

## What Changes

- 机会中心新增「线索」视图（信号洞察专家）：按站点与锚定日列出持续增长榜的词，带起止排名、净提升、窗口走势与触发口径；窗口内有缺期时明示。榜单只读取导入时算定的结果，不重新判定。
- 每条线索标出它已经在哪些未归档的机会档案里，避免重复研究。
- 勾选一个或多个词：一键新建档案（带入站点与关键词），或并入一份已有档案。
- 专家页名片更新：信号洞察改为已接入并指向线索视图；「选品机会」「验证跟踪」两张未接入名片合并为已接入的「选品研究」（指向机会档案）；新增未接入的「商业可行性」名片；商标名片保留未接入。

## Capabilities

### New Capabilities
- `opportunity-leads`：持续增长线索列表、档案归属标注、建档与并入档案的入口、专家名片状态。

### Modified Capabilities

（无。`opportunity-center` 中关于专家名片的要求由尚未归档的 `add-opportunity-ip-agent` 修改，本变更把名片状态写进新能力，不再改同一条。）

## Impact

- 后端：新增 `app/table/opportunity/leads_service.py`、`leads_api.py`（`GET /api/opportunity/leads/growth`，挂 `opportunity:view`）；复用 `growth_service.search` 与 `get_scope`，不改增长榜口径与表。无迁移。
- 前端：`pages/Opportunity/Leads/`、左栏「线索」、`experts.ts` 与专家页；新建档案页支持带入关键词；`services/opportunityLeads.ts`。
- 不产生外部调用；不写 ABA 事实表。

Refs #172、#122
