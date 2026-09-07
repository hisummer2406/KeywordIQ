## Context

issue #86 把产品范围与口径都定完了（时间轴按真实日期、不补 0、不跨缺口连线；区分「未导入」
「已导入未收录」「掉榜」「超保留范围」；两个入口权限不能互相扩大或收窄）。留给这里的是
"怎么落地"：接口形状、五态分类怎么算、日/周切换与"从历史行打开"时的锚点怎么定、两个入口
怎么复用同一份代码而不互相污染权限或数据。

## Decision 1：查询单元是「一个 keyword_id 的历史索引扫描」，不是列表查询的放大版

`analysis_crud.trends_stmt` 是为**一页几十个 keyword_id**批量回溯设计的，回溯窗口固定
（天 6 天/周 21 天）。详情要的是**一个** keyword_id 但窗口可以到"全部可用历史"（三个月
保留窗口，天最多约 90 期、周最多约 13 期）。两者的形状不同，硬塞进同一个函数会把"批量、
窗口小"的假设污染成"单个、窗口可变"，索引选择也不同——批量回溯靠 `keyword_id IN (...)`
走位图扫描，单个详情就是最朴素的 `(site, keyword_id, 日期范围)` 索引范围扫描。

**决定**：新建 `trend_crud.py`，独立的语句构造函数（`keyword_history_stmt` /
`keyword_dropped_stmt`），复用 `analysis_crud.fact_and_datecol` 拿事实表与日期列，
不改动 `analysis_crud.py` 本身。两条语句都天然走既有索引：

- `idx_fact_{daily,weekly}_keyword_history (site, keyword_id, 日期列)`——为批量趋势建的
  索引，单关键词查询同样命中，前缀 `(site, keyword_id)` 定位到几十/几百行，日期范围再收窄。
- `fact_dropped` 的主键 `(site, period_type, anchor_date, keyword_id)`——按
  `site, period_type` 等值 + `anchor_date` 范围做索引扫描，`keyword_id` 等值在扫描内做
  filter。`fact_dropped` 比事实表小一到两个数量级，这个 filter 代价可忽略。

## Decision 2：五态分类算法，且不信任 `site_data_stats` 在保留下界之前的行

产品口径要求区分：**在榜有排名 / 掉榜 / 报表已导入但未收录该词 / 该期报表未导入 / 已超出
保留范围**。判定顺序（对每个"期望存在的期次"）：

```
if 该期次日期 < retention_cutoff:
    OUT_OF_RETENTION          # 不看 site_data_stats，见下方理由
elif 该期次在 fact_{daily,weekly} 中有这个 keyword_id 的行:
    RANKED（带 current_ranking）
elif 该期次在 fact_dropped 中有这个 keyword_id 的行（anchor_date = 该期次）:
    DROPPED（带 last_ranking，仅供提示，不进区间统计）
elif 该期次在 site_data_stats 中存在（即该期报表已导入）:
    NO_DATA                   # 报表导入了，但这份报表没收这个词
else:
    NOT_IMPORTED              # 报表本身没导
```

**为什么 `< retention_cutoff` 的判断在最前面、且不查 `site_data_stats`**：`retention_service`
只 `DROP` 两张事实表与 `fact_dropped`/`fact_growth` 的月度分区，**从不清理
`site_data_stats` 的对应行**（这是既有代码里一个尚未触发问题的缺口，本变更不修——修保留
清理不在 issue #86 范围内）。如果按"是否在 `site_data_stats` 里"来判断"是否导入过"，
一个保留窗口之前、分区早被 `DROP` 但统计行还留着的月份会被误判成"已导入但未收录该词"
（NO_DATA），而真相是那份数据物理上已经不存在——用户会以为"这词那几个月确实没上榜"，
而不是"系统已经不保留那几个月的数据"。`retention_cutoff` 是唯一不依赖 `site_data_stats`
是否清理及时的判据，故在最前面短路。

`retention_cutoff` 复用 `app.table.analysis.partitioning.retention_cutoff(date.today(),
settings.HOT_WINDOW_MONTHS)`——与保留清理脚本用的是同一个函数，不重新定义"多久算超期"。

## Decision 3：「期望存在的期次」按已知的期次步长枚举，不假设自然日历

天视图步长 1 天没有争议。周视图的步长**不是"自然周边界"**（issue 明确要求不发明新的周
定义），而是复用 `app/table/upload/importer.py` 里 `_PERIOD_CONFIG["WEEK"]` 已经在用的
"上一期偏移 7 天"——这正是 `previous_ranking`/`ranking_change` 在导入时使用的同一个偏移量，
不是本变更新造的概念。

枚举范围：

- **锚定日期** `on`：来自请求（见决策 4），不落到"今天"。
- **`RECENT_30D`**：`[on - 29, on]` 的自然日历窗口（两个视图都用日历天数，不是"30 个
  周期"——周视图落进去自然只有约 4~5 个整周期点，这是预期行为，不是 bug）。
- **`ALL`**：`[max(retention_cutoff, 该 site+view 在 site_data_stats 里的最早日期), on]`。
  site 该 view 完全没有数据（`site_data_stats` 一行都没有）时，直接返回空历史而非报错——
  与 `search_stmt` 对空作用域的既有处理一致。

天视图步长 1 天时枚举出的期次数上限约 90（三个月），周视图约 13——枚举本身是纯 Python
循环，不产生额外查询，代价可忽略。

## Decision 4：锚点语义——保留来源日期语境，不静默跳到今天

issue 要求"从历史列表打开应保留来源日期语境"。两个入口打开抽屉时都带上**当前正在看的那
一期**（分析屏是 `appliedRef.current.date`——已确定生效的作用域日期，不是草稿；词库屏是
`date` 状态——当前盯盘请求已解析出的期次），而不是重新去问"最新一期是哪天"。这样用户在
查 7 月 10 日的历史数据时打开详情，"近 30 天"给的是"以 7 月 10 日为右端"的 30 天，不是
以今天为右端——否则详情里会出现用户在列表页根本没选过的、更新的数据，且没有任何提示，
正是 issue 说的"静默跳到最新期而让用户误读"。

**日 / 周切换时的锚点映射**：两个视图的日期列独立，不存在"同一天"的直接对应。改为
**取目标视图里 `<= 来源锚点` 的最新一期**；若目标视图完全没有 `<= 来源锚点` 的数据（比如
来源锚点早于目标视图上线的时间），退化为目标视图的全局最新一期，并且响应里的 `on` 字段
如实回显解析后的锚点——前端用它更新抽屉标题里的日期，不是"改了参数抽屉却不告诉你换了
哪天"。这满足"不得静默跳到最新期"：**允许跳，但必须可见**，抽屉标题本来就要显示
"实际使用的锚定期"（issue 的验收条件之一）。

## Decision 5：接口契约

```
GET /api/keyword/trend
    ?keyword_id=123&site=US&view=DAY&range=RECENT_30D|ALL&on=2026-07-10(可选)

200 KeywordTrendResponse:
  keyword_id, keyword, site, view, range
  on: date | null              # 实际使用的锚定期，null 表示该 site+view 无任何数据
  coverage: { start: date, end: date } | null   # 实际数据覆盖范围（RANKED/DROPPED/NO_DATA 期次的边界）
  points: [{ date, status: RANKED|DROPPED|NO_DATA|NOT_IMPORTED|OUT_OF_RETENTION, ranking: int|null }]
  best_ranking: { date, ranking } | null        # 选定范围内 RANKED 点中的最小排名
  latest_available: { date, ranking } | null    # 选定范围内最靠后的 RANKED 点，与 on 是否为当期无关
```

`best_ranking`/`latest_available` 只统计 `RANKED` 点；`points` 里没有任何 RANKED 点时两者
均为 `null`，前端据此显示"无数据"而不是 0 或空字符串（0 是合法排名附近的普通数字，不能
拿它当"没有"的编码，与事实表 `previous_ranking` 默认值 0 的既有教训一致）。

**`latest_available` 与 `on` 当期可能不是同一天**——这正是 issue 强调的"不能把历史最后一次
在榜排名标成当前期排名"：`on` 当期若是 `DROPPED`/`NO_DATA`/`NOT_IMPORTED`，前端必须分别
展示这两个信息而不是互相顶替。

## Decision 6：权限——任一满足，不新增权限点

两个入口各自的权限矩阵不同（分析屏 `analysis:view`，词库屏 `keyword:view`），且不能互相
扩权：只有 `keyword:view` 的用户不能借这个端点获得 `analysis:view` 覆盖的能力，反之亦然。
但这个端点本身只读一个关键词的历史排名，不携带任何域特有的额外信息（不像 `competitor_new`
读的是竞对私有数据）——**两个权限点里有一个即可访问是恰当的边界**，不是"取并集绕过检查"。

`app/auth/simple_auth.py` 新增：

```python
@staticmethod
def require_any_permission(*permissions: str):
    """依赖工厂：拥有其中任一权限点即放行。is_super 照常兜底。"""
```

与 `require_permission` 同构，只是判定改成 `any(p in user.permission_codes for p in
permissions)`。这是通用的权限组合原语，不是给这一个端点特制的分支判断。

`keyword_id` 与请求 `site` 的一致性校验**独立于权限**：查 `dim_keyword.site`，不一致返回
422（`keyword.site_mismatch`）而不是 404——id 存在，只是调用方传的 site 与它实际所属的
站点对不上，这是调用方的作用域状态错了（比如切站点后残留了旧站点的 keyword_id），不是
"这个词不存在"。

## Decision 7：两个入口共用同一份前端组件，加入词库按钮按来源隐藏重复操作

`KeywordTrendDrawer` 只接收 `{ keywordId, keyword, site, view, on }` 与一个
`hideAddToLibrary?: boolean`。从词库屏打开时传 `hideAddToLibrary`：这个词本来就在当前
分组里，抽屉里再放一次"加入词库"是对同一个操作的重复入口，且它此刻属于哪个分组、要不要
再选一次分组，在词库屏的语境下没有清楚的答案（`AddToLibraryModal` 是给"发现一个新词"设计
的，不是给"我正在盯的词"设计的）。从分析屏打开则正常显示，复用现有 `AddToLibraryModal`
的分组选择与配额校验，不重新实现一遍。

## Decision 8：抽屉尺寸与图表实现

底部抽屉（`Drawer placement="bottom"`），高度固定 480px（覆盖时间轴 + 统计条 + 图例，
不需要随内容变化）。图表用内联 SVG 手写（与 `TrendSparkline` 同一路线，不引入图表库）——
真实时间轴下 x 坐标按日期线性映射而非等距分点，缺口用**不连线**（在该 x 位置断开
`polyline`，而不是省略该点）表达，"未导入/超保留/无收录"三态用不同的图标/纹理标记在缺口
处而不是随便留白，避免用户以为是渲染漏了。

## 验证计划

- **单元**：五态分类的边界条件（正好在 `retention_cutoff` 上、`site_data_stats` 有行但
  `fact_daily` 无行、`fact_dropped` 有行、两者都无行）；`RECENT_30D`/`ALL` 的范围计算；
  日/周锚点映射（含"目标视图无 `<= on` 数据"的退化分支）；`require_any_permission` 的
  三种组合（只有 A、只有 B、都没有）；跨站点 `keyword_id` 422。
- **集成**：真实建三个月分区数据（含至少一个整月已被 `DROP`、但保留 `site_data_stats`
  残留行，复现决策 2 要防的那个假阳性），验证 `OUT_OF_RETENTION` 优先于 `NO_DATA` 判定；
  验证 `EXPLAIN` 走 `idx_fact_*_keyword_history` 而非 Seq Scan。
- **前端**：vitest 覆盖抽屉在四种数据形态下的渲染（连续/单点/全无/含缺口），两个入口
  分别挂载后点击列内入口能打开抽屉且参数正确，`hideAddToLibrary` 生效。
