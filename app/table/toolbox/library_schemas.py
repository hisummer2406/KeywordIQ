"""词库（我的词库）的请求/响应 DTO。

盯盘行**继承分析查询屏的 ``FactRowBase``**，不另抄一份字段：同一个词在两屏必须是同一
套口径，而两份平行的行定义分叉时不会报错，只会让两屏显示不同的数字。

日期类型统一用别名 ``_Date``：本模块有名为 ``date`` 的字段，直接用 ``date`` 作注解会被
字段名遮蔽（pydantic v2 重解析注解时把它当成 FieldInfo，递归爆栈）——与 growth 同因。
"""
from __future__ import annotations

from datetime import date as _Date
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.table.search.search_schemas import FactRowBase

# 盯盘行的三种状态。三态而不是两个布尔：两个布尔能编出四种组合，其中一种（既掉榜又有
# 当期数据）不存在，而"不存在的状态"迟早会有人去处理它。
LibraryRowStatus = Literal["LISTED", "DROPPED", "MISSING"]

# 排序白名单。走势与历史排名不在其中（它们是数组/附加信息），与分析屏同一条约定。
LibrarySortField = Literal[
    "current_ranking",
    "ranking_change",
    "previous_ranking",
    "top1_click_share",
    "top1_conversion_share",
    "added_at",
]

# 单次批量加词的条数上限。与"每组条目数上限"是两回事：后者是清单能有多大（配置项），
# 这里限的是**单个请求体**能有多大，防的是一次传十万个 id 把内存与 SQL 语句撑爆。
MAX_ITEMS_PER_REQUEST = 500


class GroupCreateRequest(BaseModel):
    """建组。站点建后不可改，故只有这里能指定它。"""

    site: str = Field(description="站点码，建后不可修改")
    name: str = Field(min_length=1, max_length=50, description="分组名，同用户同站点下唯一")


class GroupRenameRequest(BaseModel):
    """改名。**不含 site**——放开改站点等于把整组内容换掉，那不如新建一个组。"""

    name: str = Field(min_length=1, max_length=50, description="新分组名")


class GroupOut(BaseModel):
    """分组列表项。带条目数，界面据此显示「(37)」并判断是否已到上限。"""

    model_config = ConfigDict(from_attributes=True)

    group_id: int
    site: str
    name: str
    item_count: int = 0
    created_at: datetime
    updated_at: datetime


class ItemsRequest(BaseModel):
    """批量加/删条目。关键词以 id 给出——id 由分析查询屏的结果行直接带出，
    不必按文本反查，也就没有错别字与跨站点同名词的歧义。"""

    keyword_ids: list[int] = Field(
        min_length=1, max_length=MAX_ITEMS_PER_REQUEST, description="关键词 id 列表"
    )


class ItemsAddResult(BaseModel):
    """加词结果。

    **新增与已存在分开回传**：批量加词是幂等的，只回一个「成功」会让用户以为 20 个词
    都是新加的，而实际可能 18 个早就在组里了。
    """

    added: int = Field(description="本次真正新增的条数")
    existed: int = Field(description="本就在组内、被幂等跳过的条数")
    item_count: int = Field(description="操作后该分组的条目总数")


class ItemsRemoveResult(BaseModel):
    removed: int
    item_count: int


class LibraryScopeInfo(BaseModel):
    """盯盘的可选期次。

    **没有站点参数**：站点由分组决定。给盯盘屏一个站点选择器就会出现「选了 DE 却在看
    US 分组」这种自相矛盾的状态。
    """

    site: str
    view: Literal["DAY", "WEEK"]
    dates: list[_Date] = Field(default_factory=list, description="该站点该视图的可选期次，降序")


class WatchRequest(BaseModel):
    """盯盘查询。site 不在请求里——它取自 group_id 所属的分组。"""

    group_id: int
    view: Literal["DAY", "WEEK"] = "DAY"
    date: _Date | None = Field(None, description="锚定期，缺省取该站点该视图最新一期")

    page: int = Field(1, ge=1)
    perPage: int = Field(50, ge=1, le=200)

    # 非法排序字段由 Literal 挡在参数校验层（400），不静默回落——回落会让用户以为按某列
    # 排了序，看到的却是另一种顺序。
    orderBy: LibrarySortField = "current_ranking"
    orderDir: Literal["asc", "desc"] = "asc"


class LibraryWatchRow(FactRowBase):
    """盯盘行 = 分析屏的行 + 两个词库自己的字段。

    ``status`` 是权威的三态判据；继承来的 ``is_dropped`` 由它派生（在 service 里一处
    构造），保留是因为行的形状与分析屏共用，前端的掉榜标签复用同一段渲染。
    """

    status: LibraryRowStatus = Field(description="LISTED 在榜 / DROPPED 掉榜 / MISSING 该期无数据")
    added_at: datetime = Field(description="加入词库的时间")


class WatchResponse(BaseModel):
    """列表响应：ProTable 契约 {data,total,success} + 作用域回显。

    ``total`` 是**分组条目数**，不是命中事实表的行数——盯盘的行集合由清单决定，
    掉榜与无数据的词同样占一行。
    """

    success: bool = True
    data: list[Any] = Field(default_factory=list)
    total: int = 0

    group_id: int
    site: str
    view: Literal["DAY", "WEEK"]
    date: _Date | None = None
