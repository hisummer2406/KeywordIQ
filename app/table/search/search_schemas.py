"""站点作用域查询（site-scoped-query）的请求/响应 DTO。

作用域三元组 (site, view, date)：任一查询只落在单一站点、单一视图（日/周）、单一日期。
行数据按视图区分；搜索趋势由日、周两视图共有，回溯单位不同。
列表出口保持 ProTable 契约 {data, total, success}，并回显生效作用域。

注意：日期类型统一用别名 ``_Date`` 引用。存在字段 ``date: Optional[_Date]``——若直接用
``date`` 作类型，字段名 date 会在类命名空间遮蔽类型 date，pydantic v2 用 get_type_hints
重解析注解时把 date 当成 FieldInfo，导致递归爆栈。别名规避了这一遮蔽。
"""
from datetime import date as _Date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

View = Literal["DAY", "WEEK"]
Include = Literal["ALL", "LISTED", "DROPPED"]


class AnalysisSearchRequest(BaseModel):
    """分析查询请求。site/view/date 缺省时由服务层解析（默认站点 / 最新日期）。"""

    site: str | None = Field(None, description="站点码，缺省用配置默认站点")
    view: View = Field("DAY", description="视图：DAY 查日报，WEEK 查周报")
    date: _Date | None = Field(None, description="报告日期，缺省取该站点该视图最新可用日期")

    # 分页
    page: int = Field(1, ge=1, description="页码")
    perPage: int = Field(50, ge=1, le=200, description="每页数量")

    # 是否要算总数。
    #
    # 总数是**精确计数**（去掉封顶之后），代价与命中行数同阶：实测日报筛选约 0.2–0.45s、
    # 周报约 0.7–0.9s。而同一组条件下翻页、改排序、改每页条数都**不会改变总数**——事实
    # 切片只追加不原地更新，查询又锁死在单一 (站点,视图,日期) 上。每页重算一遍是纯浪费，
    # 且浪费的正好是这条路径上最贵的那部分。
    #
    # 故由调用方声明：条件变了传 true 重算，只翻页/改排序传 false 沿用上一次的值。
    # 传 false 时响应的 ``total`` 是 ``None``（"本次没算"），不是 0——0 会被 ProTable
    # 当成"没有数据"而把翻页器清空，那是一种看不出原因的故障。
    include_total: bool = Field(
        True, description="是否计算总数；仅翻页/改排序时传 false 以复用上次结果"
    )

    # 行集合维度。**不是筛选条件**——筛选一律以锚定期那一行为准，而掉榜行在锚定期没有
    # 行可供匹配，把它做成筛选条件会与那条规则自相矛盾。
    #
    # 非法取值按参数不合法拒绝，不静默回落：回落到 ALL 会让用户以为自己在看「仅掉榜」
    # 而实际看的是全部，而这两者在界面上区分不出来。
    include: Include = Field(
        "ALL", description="行集合：ALL 全部 / LISTED 仅在榜 / DROPPED 仅掉榜"
    )

    # 排序（字段按视图白名单校验，非法回落默认；见 crud.SORTABLE_FIELDS）
    orderBy: str | None = Field(None, description="排序字段")
    orderDir: Literal["asc", "desc"] | None = Field(None, description="排序方向")

    # 子串筛选（走维度表 trgm 索引）
    keyword: str | None = Field(None, description="关键词子串")
    asin: str | None = Field(None, description="ASIN 子串（top1 商品）")
    product_title: str | None = Field(None, description="标题子串（top1 商品）")

    # 品牌与类目：**精确多选，前三任一命中**。语义是「这个词的前三品牌里有 X」。
    #
    # 品牌此前是子串筛选（`brand ILIKE '%x%'`），随品牌维度化一并改为精确多选，与类目
    # 对称——这是一次用户可感知的能力收窄（搜 anker 不再匹配 Anker Direct），补偿是下拉
    # 带计数，让用户能在相近品牌名之间做判断。
    #
    # 上限 100：无上限的 IN 列表会让计划器在参数很长时放弃索引扫描，而勾满上百个已等价
    # 于不筛。品牌基数（13.5 万）远高于类目（104），上限对品牌尤其必要。
    brands: list[str] | None = Field(
        None, max_length=100, description="品牌精确匹配集合，前三任一命中；空集合视同不筛"
    )
    categories: list[str] | None = Field(
        None, max_length=100, description="类目精确匹配集合，前三任一命中；空集合视同不筛"
    )

    # 区间筛选
    ranking_min: int | None = Field(None, description="当期排名最小值")
    ranking_max: int | None = Field(None, description="当期排名最大值")
    change_min: int | None = Field(None, description="排名变化最小值")
    change_max: int | None = Field(None, description="排名变化最大值")
    click_share_min: float | None = Field(None, description="点击份额最小值")
    click_share_max: float | None = Field(None, description="点击份额最大值")
    conversion_share_min: float | None = Field(None, description="转化份额最小值")
    conversion_share_max: float | None = Field(None, description="转化份额最大值")

    # 布尔筛选
    is_new: bool | None = Field(None, description="是否本周期新词")


class _FactRowBase(BaseModel):
    """两视图共有的事实字段（来自 fact_daily / fact_weekly + 前三商品维度）。

    三个商品槽位与点击量名次一一对应。某个槽位在源报表中缺失时，该组字段为空
    （ASIN/标题/品牌/类目为 None，两个份额为 0），**不由后一名顶替**——名次与槽位的
    对应关系是这些字段的全部意义。

    注意：这三组字段只是**展示**数据。品牌/类目/ASIN/标题/份额的筛选与排序仍只作用于
    top1，见 crud.\\_apply_filters 与 crud.SORTABLE_FIELDS。
    """

    model_config = ConfigDict(from_attributes=True)

    keyword_id: int
    keyword: str
    # 掉榜行的当期字段全部为空：当期排名、商品槽位、品牌、类目、份额。**不用最后在榜
    # 那一期的值顶替**——顶替会让同一列混入不同期次的数据，用户无从判断某一格属于哪天。
    current_ranking: int | None = None
    previous_ranking: int | None = None
    ranking_change: int | None = None
    is_new: bool = False

    # 掉榜行独有：它唯一能携带的跨期信息，且期次明确标注。
    is_dropped: bool = False
    last_seen_date: _Date | None = Field(None, description="掉榜行最后一次在榜的期次")
    last_ranking: int | None = Field(None, description="掉榜行最后一次在榜时的排名")

    top1_asin: str | None = None
    top1_title: str | None = None
    top1_click_share: float = 0
    top1_conversion_share: float = 0

    top2_asin: str | None = None
    top2_title: str | None = None
    top2_click_share: float = 0
    top2_conversion_share: float = 0

    top3_asin: str | None = None
    top3_title: str | None = None
    top3_click_share: float = 0
    top3_conversion_share: float = 0

    # 关键词级的前三品牌与前三类目。**不是商品属性**——源报表里它们是与商品榜单彼此
    # 独立的排行，商品槽位不携带这两个字段。名次由字段位置承载。
    top1_brand: str | None = None
    top2_brand: str | None = None
    top3_brand: str | None = None
    top1_category: str | None = None
    top2_category: str | None = None
    top3_category: str | None = None

    # 搜索趋势：两个视图共有，故放基类。回溯窗口按视图取（日 7 天 / 周 7 期），
    # 但字段本身是同一个东西——在两个子类里各声明一遍会让描述与默认值各自漂移。
    trend: list[int] = Field(
        default_factory=list, description="截至所选日期的最近若干期排名，按期次升序"
    )
    trend_dates: list[_Date] = Field(
        default_factory=list, description="搜索趋势各点对应的期次日期"
    )

    # 三个历史参照点的排名，顺序固定为 [上期, 4 期前, 12 期前]（日视图即昨天/3 天前/
    # 7 天前）。取自与趋势**同一次**回溯查询，不额外发查询。
    # 参照期缺失时该位为 None 而非 0——0 已经是"无上期"的编码，两个含义不能挤在一起。
    #
    # **排名变化率不在这里**：它可由 ranking_change 与 previous_ranking 整除得出，
    # 多下发一份只会在两处口径漂移时产生互相矛盾的数字。前端在展示层算。
    history: list[int | None] = Field(
        default_factory=list, description="历史排名 [上期, 4 期前, 12 期前]"
    )


class DayRowItem(_FactRowBase):
    """天视图行。趋势渲染 7 点，历史参照点为昨天 / 3 天前 / 7 天前。"""


class WeekRowItem(_FactRowBase):
    """周视图行。趋势渲染 12 点，历史参照点为上周 / 4 周前 / 12 周前。"""


class AnalysisSearchResponse(BaseModel):
    """列表响应：ProTable 契约 {data,total,success} + 作用域回显。

    ProTable 只读 data/total/success；site/view/date 供前端回显与选择器默认。
    """

    success: bool = True
    data: list[Any] = Field(default_factory=list)
    # ``None`` 表示"本次没算总数"（请求带了 include_total=false），调用方应沿用上一次的值。
    # 与 0 严格区分：0 是"确实没有命中行"。混淆这两者会让翻页器在翻页时突然清空。
    total: int | None = 0
    site: str
    view: View
    date: _Date | None = None
    message: str | None = None
    # 该切片的品牌/类目尚未重建（历史数据需按批次从 OSS 重导才有这两个字段）。
    # 前端据此明确提示，**不能静默返回空**——用户会以为是自己筛选条件写错了。
    pending_rebuild: bool = False


class ScopeInfo(BaseModel):
    """作用域元信息：日期选择器与默认解析用。"""

    site: str
    view: View
    latest_date: _Date | None = None
    available_dates: list[_Date] = Field(default_factory=list)


class CategoryOption(BaseModel):
    """类目下拉项。"""

    category: str
    count: int


class BrandOption(BaseModel):
    """品牌下拉项。

    ``count`` 不是装饰：品牌改为精确多选后，面对 Anker / Anker Direct / Ankermann
    这类相近选项，计数是用户判断该选哪个的唯一依据。
    """

    brand: str
    count: int
