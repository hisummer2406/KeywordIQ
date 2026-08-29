"""持续增长市场的请求/响应 DTO。

作用域是 **单一站点 + 单一锚定日**，没有视图这一维——只有日报有「连续 7 天」的概念，
留一个恒为 DAY 的 view 字段等于给调用方一个不存在的选择。

日期类型统一用别名 ``_Date``：本模块有名为 ``date`` 的字段，直接用 ``date`` 作注解会被
字段名遮蔽（pydantic v2 重解析注解时把它当成 FieldInfo，递归爆栈）。
"""
from __future__ import annotations

from datetime import date as _Date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# 排序白名单。走势与窗口首期排名不在其中：前者是数组，后者排序对用户没有意义
# （「七天前排第几」不是选品会问的问题），而每多一个可排序列就多一种要验的执行计划。
GrowthSortField = Literal["rank_gain", "min_growth_rate", "end_ranking"]


class GrowthSearchRequest(BaseModel):
    """榜单查询请求。site/date 缺省时由服务层解析（默认站点 / 最新可用日期）。"""

    site: str | None = Field(None, description="站点码，缺省用配置默认站点")
    date: _Date | None = Field(None, description="锚定日，缺省取该站点最新可用日期")

    page: int = Field(1, ge=1, description="页码")
    perPage: int = Field(50, ge=1, le=200, description="每页数量")

    # 非法排序字段由 pydantic 直接挡在 Literal 上（400），不静默回落到默认——回落会让
    # 用户以为自己按某列排了序，看到的却是另一种顺序。
    orderBy: GrowthSortField = Field("rank_gain", description="排序字段")
    orderDir: Literal["asc", "desc"] = Field("desc", description="排序方向")


class GrowthRowItem(BaseModel):
    """榜单行。

    起止排名都给：只给净增的话，「从 10 万涨到 6 万」与「从 5 万涨到 1 万」在界面上一模
    一样，而这两件事在选品上完全不是一回事。

    ``min_growth_rate`` 是 0~1 的小数（0.1234 = 12.34%），**不在接口侧换算成百分数**——
    份额那一路已经因为「界面、档位、接口各换算一次」踩过坑，这里保持一处换算：展示层。
    """

    model_config = ConfigDict(from_attributes=True)

    keyword_id: int
    keyword: str
    start_ranking: int
    end_ranking: int
    rank_gain: int = Field(description="累计净增名次，正数为名次前进")
    min_growth_rate: float = Field(description="窗口内每日环比的最小值，0~1 的小数")

    trend: list[int] = Field(default_factory=list, description="窗口内各期排名，按期次升序")
    trend_dates: list[_Date] = Field(default_factory=list, description="走势各点对应的期次日期")


class GrowthSearchResponse(BaseModel):
    """列表响应：ProTable 契约 {data,total,success} + 作用域回显 + 缺期。"""

    success: bool = True
    data: list[Any] = Field(default_factory=list)
    total: int = 0
    site: str
    date: _Date | None = None

    # 窗口内未导入的期次。**必须回传**：缺期时所有词都凑不满窗口，榜单据实为空，而
    # 「今天没有增长词」与「有两天的数据没导进来」在界面上长得一模一样。
    missing_dates: list[_Date] = Field(
        default_factory=list, description="窗口内没有数据的期次；非空时界面须明示"
    )
    window_days: int = Field(description="窗口期数，供界面拼「近 N 天」文案")


class GrowthScopeInfo(BaseModel):
    """作用域元信息 + 判定口径。

    三个阈值随作用域一起下发，界面的口径说明条据此拼文案。**前端不得硬编码这些数值**
    ——两份副本迟早分叉，而分叉后用户看到的口径说明与后端实际用的口径不是同一个。
    """

    site: str
    latest_date: _Date | None = None
    available_dates: list[_Date] = Field(default_factory=list)

    window_days: int
    min_rank_gain: int
    min_daily_rate: float = Field(description="每日环比下限，0~1 的小数")
