"""站点作用域查询（site-scoped-query）的请求/响应 DTO。

作用域三元组 (site, view, date)：任一查询只落在单一站点、单一视图（日/周）、单一日期。
行数据按视图区分：日视图含 7 天走势与当周排名变化，周视图不含。
列表出口保持 ProTable 契约 {data, total, success}，并回显生效作用域。

注意：日期类型统一用别名 ``_Date`` 引用。存在字段 ``date: Optional[_Date]``——若直接用
``date`` 作类型，字段名 date 会在类命名空间遮蔽类型 date，pydantic v2 用 get_type_hints
重解析注解时把 date 当成 FieldInfo，导致递归爆栈。别名规避了这一遮蔽。
"""
from datetime import date as _Date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

View = Literal["DAY", "WEEK"]


class AnalysisSearchRequest(BaseModel):
    """分析查询请求。site/view/date 缺省时由服务层解析（默认站点 / 最新日期）。"""

    site: str | None = Field(None, description="站点码，缺省用配置默认站点")
    view: View = Field("DAY", description="视图：DAY 查日报，WEEK 查周报")
    date: _Date | None = Field(None, description="报告日期，缺省取该站点该视图最新可用日期")

    # 分页
    page: int = Field(1, ge=1, description="页码")
    perPage: int = Field(50, ge=1, le=200, description="每页数量")

    # 排序（字段按视图白名单校验，非法回落默认；见 crud.SORTABLE_FIELDS）
    orderBy: str | None = Field(None, description="排序字段")
    orderDir: Literal["asc", "desc"] | None = Field(None, description="排序方向")

    # 子串筛选（走维度表 trgm 索引）
    keyword: str | None = Field(None, description="关键词子串")
    brand: str | None = Field(None, description="品牌子串（top1 商品）")
    category: str | None = Field(None, description="类目子串（top1 商品）")
    asin: str | None = Field(None, description="ASIN 子串（top1 商品）")
    product_title: str | None = Field(None, description="标题子串（top1 商品）")

    # 精确类目多选（下拉，选项走 category_stats）。命中其一即可；空集合视同不筛。
    # 上限 100：类目数可达数百，无上限的 IN 列表会让计划器在参数很长时放弃索引扫描，
    # 而勾满上百个类目已等价于不筛。
    categories: list[str] | None = Field(
        None, max_length=100, description="类目精确匹配集合，命中其一即可；空集合视同不筛"
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
    """两视图共有的事实字段（来自 fact_daily / fact_weekly + top1 维度）。"""

    model_config = ConfigDict(from_attributes=True)

    keyword_id: int
    keyword: str
    current_ranking: int
    previous_ranking: int
    ranking_change: int
    is_new: bool

    top1_asin: str | None = None
    top1_title: str | None = None
    top1_brand: str | None = None
    top1_category: str | None = None
    top1_click_share: float = 0
    top1_conversion_share: float = 0


class DayRowItem(_FactRowBase):
    """日视图行：额外含近 7 天走势与当周排名变化（B 期填充）。"""

    trend: list[int] = Field(default_factory=list, description="截至所选日期的近 7 天排名走势")
    trend_dates: list[_Date] = Field(default_factory=list, description="近 7 天走势各点对应日期")
    week_change: int | None = Field(None, description="当前日期所属周的周排名变化，缺失为空")


class WeekRowItem(_FactRowBase):
    """周视图行：不含走势与当周变化。"""


class AnalysisSearchResponse(BaseModel):
    """列表响应：ProTable 契约 {data,total,success} + 作用域回显。

    ProTable 只读 data/total/success；site/view/date 供前端回显与选择器默认。
    """

    success: bool = True
    data: list[Any] = Field(default_factory=list)
    total: int = 0
    site: str
    view: View
    date: _Date | None = None
    message: str | None = None


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
