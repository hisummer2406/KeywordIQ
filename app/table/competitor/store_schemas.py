"""独立站监控的请求/响应 DTO（Pydantic v2）。

列表响应走 ProTable 契约 ``{data,total,success}``——``success`` 只表达成功，失败一律
走非 2xx（失败混在 200 里，前端无法把「查询失败」与「没有数据」区分开）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# 站点状态。与 store_fetcher 的 FETCH_* 一一对应，但这是**面向用户**的一层，
# 措辞由前端决定；这里只保证三类失败不被合并成一种。
STATUS_NEVER_FETCHED = "NEVER_FETCHED"
STATUS_OK = "OK"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_FAILED = "FAILED"
STATUS_NO_CATALOG_API = "NO_CATALOG_API"


class StoreAddRequest(BaseModel):
    """加入一个独立站。``domain`` 接受用户粘贴的任意形态，服务端规范化。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    domain: str = Field(min_length=1, max_length=500)
    group_id: int | None = None
    note: str | None = Field(default=None, max_length=200)


class StoreBatchAddRequest(BaseModel):
    """一次加入多个独立站。

    条数上限见 ``MAX_BATCH_DOMAINS``：**超限整批拒绝而不截断**（截断会让用户以为全部
    加进去了）。有了这个上限，最坏耗时即有界，因此本能力不需要引入异步任务。
    """

    domains: list[str] = Field(min_length=1, max_length=200)
    group_id: int | None = None


MAX_BATCH_DOMAINS = 200


class BatchAddedItem(BaseModel):
    """加入成功的一条。``input`` 回指用户粘的**原文那一行**。

    只回规范化后的域名的话，用户无法把它与自己粘的那行对上——
    ``https://www.example.com/products/abc?utm=x`` 与 ``example.com`` 看起来不像同一个东西。
    """

    input: str
    domain: str
    name: str | None
    currency: str | None


class BatchRejectedItem(BaseModel):
    """被拒绝的一条，**必须带原因**。

    只报「N 条失败」等于让用户自己去猜是哪几条、为什么。原因复用单条路径已有的分类，
    两边分叉的表现是「单个粘贴能进，放进批量里同样的链接却被拒」。
    """

    input: str
    reason: str


class StoreBatchAddResponse(BaseModel):
    """三段结果，按**输入行**组织而不是按站点组织。

    请求整体返回 200 即使全部被拒——那不是错误，是「这一批都不受支持」这个事实。
    """

    added: list[BatchAddedItem]
    already_watched: list[BatchAddedItem]
    rejected: list[BatchRejectedItem]
    success: bool = True


class StoreUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    group_id: int | None = None
    note: str | None = Field(default=None, max_length=200)
    clear_group: bool = Field(
        default=False, description="置为未分组。与 group_id=null 区分，否则无法表达「清空」"
    )


class StoreListRequest(BaseModel):
    """关注中的独立站清单。分页必填、有上限——不返回全表。"""

    group_id: int | None = None
    ungrouped_only: bool = False
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)


class StoreOut(BaseModel):
    domain: str
    name: str | None
    currency: str | None
    country: str | None
    group_id: int | None
    group_name: str | None
    note: str | None
    watched_at: datetime

    status: str
    catalog_complete: bool
    rank_ok: bool
    consecutive_failures: int
    last_fetch_at: datetime | None
    baseline_at: datetime | None = Field(
        description="上新基线：首次完整拿到目录的时刻；为空表示还没完整拉取过，此时不产生上新"
    )

    product_count: int = Field(description="在售商品数（未标记下架）")
    new_in_7d: int = Field(
        description="近 7 日上架数，按数据源给的 published_at 算——首刷当天即准确"
    )


class StoreListResponse(BaseModel):
    """ProTable 契约。``success`` 恒为 True，失败走非 2xx。"""

    data: list[StoreOut]
    total: int
    success: bool = True


class ProductListRequest(BaseModel):
    domain: str
    include: str = Field(
        default="LISTED",
        description="LISTED（在售）/ DELISTED（已下架）/ ALL。这是行集合维度，不是筛选条件",
    )
    new_only: bool = Field(default=False, description="只看近 7 日上架（按 published_at）")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)


class ProductOut(BaseModel):
    product_id: int
    handle: str
    title: str
    vendor: str | None
    product_type: str | None
    image_url: str | None
    published_at: datetime | None
    first_seen_at: datetime
    delisted_at: datetime | None
    current_price: Decimal | None
    compare_at_price: Decimal | None
    available: bool | None
    latest_rank: int | None = Field(
        default=None, description="最近一次店内畅销名次。**相对名次，不是销量**"
    )


class ProductListResponse(BaseModel):
    data: list[ProductOut]
    total: int
    success: bool = True
    currency: str | None = Field(
        default=None, description="该站币种。价格数字本身不带币种，界面必须靠它显示符号"
    )


class NewArrivalOut(BaseModel):
    """上新流的一行。

    两类来源的时间语义不同，故 ``time_kind`` 是必填的：独立站是数据源给的上架时间
    （``PUBLISHED``），Amazon 侧只能是我们首次见到的时刻（``DISCOVERED``）。把后者标成
    「上架于」，等于拿「我们发现得晚」冒充「他上得晚」。
    """

    source: str = Field(description="DTC / AMAZON")
    source_key: str = Field(description="独立站为域名，Amazon 为 seller_id")
    source_name: str | None
    site: str | None = Field(
        default=None,
        description=(
            "站点码，仅 AMAZON 行非空。共享层的真实粒度是 (seller_id, site, asin)——"
            "同一 seller_id 跨站点卖同一 ASIN 时，缺了这一列就无法在响应与 rowKey 里"
            "区分两行"
        ),
    )
    product_key: str = Field(description="独立站为 product_id，Amazon 为 ASIN")
    title: str
    image_url: str | None
    at: datetime
    time_kind: str = Field(description="PUBLISHED（上架于）/ DISCOVERED（发现于）")


class NewArrivalRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)


class NewArrivalResponse(BaseModel):
    data: list[NewArrivalOut]
    total: int
    success: bool = True


class PricePointOut(BaseModel):
    observed_at: datetime
    variant_id: int
    sku: str | None
    price: Decimal | None
    compare_at_price: Decimal | None
    available: bool


class RankPointOut(BaseModel):
    captured_on: date
    rank: int


class ProductHistoryResponse(BaseModel):
    """一个商品的调价序列与名次走势。

    ``currency`` 随行返回而不是让前端去别处查——价格数字不带币种，两者分开取迟早会
    出现「拿 A 店的币种显示 B 店的价格」。
    """

    domain: str
    product_id: int
    currency: str | None
    prices: list[PricePointOut]
    ranks: list[RankPointOut]
    success: bool = True
