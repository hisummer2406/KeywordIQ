"""竞对监控（关注清单）的请求/响应 DTO。Pydantic v2。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Amazon 卖家行状态**三态**（#166，competitor-storefront-watch「Amazon 卖家行展示手动采集与三态
# 数据状态」），由服务端判定：无基线 NOT_COLLECTED；有基线且最近上报在新发现窗口内 COLLECTED；
# 否则 STALE。此前的两态「待建立基线 / 监控中」里，「监控中」会被读成后台在自动检查，而 Amazon
# 侧只随人工采集更新。
#
# 曾经还有一个 PAUSED，是 P0 为「后续数据获取层」预留的占位取值。它被删掉不是因为没用上，
# 而是因为**没有任何字段或端点能让一行进入该状态**——一个永远取不到值的状态比没有更糟，
# 它让读界面的人以为系统能暂停某个卖家。预留一个取值的成本看起来是零，实际成本是它会一直
# 谎报一项不存在的能力，且没有任何地方会报错。
#
# 真要做暂停，它属于**私有层** `user_seller_watch`（「我暂时不想看这家」是个人意愿，不是
# 卖家的客观属性），连同暂停/恢复端点是另一次变更。
WatchStatus = Literal["NOT_COLLECTED", "COLLECTED", "STALE"]

# 单次批量操作（移除/改分组）的条数上限，防止一次请求体过大。
MAX_ITEMS_PER_REQUEST = 500


class GroupCreateRequest(BaseModel):
    """建组。不含 site——分组不绑定站点，允许跨站点。"""

    name: str = Field(min_length=1, max_length=50, description="分组名，同一用户下唯一")


class GroupRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50, description="新分组名")


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_id: int
    name: str
    watch_count: int = 0
    created_at: datetime
    updated_at: datetime


class WatchListRequest(BaseModel):
    """主表格查询。group_id 为 None 表示不按分组过滤（全部关注）。"""

    group_id: int | None = Field(None, description="按分组过滤，缺省返回全部关注")
    page: int = Field(1, ge=1)
    perPage: int = Field(50, ge=1, le=200)


class WatchUpdateRequest(BaseModel):
    """改分组 / 改备注。字段缺省表示不改该字段（PATCH 语义）。"""

    group_id: int | None = Field(
        default=None, description="新分组，传 null 表示移出分组（未分组）"
    )
    note: str | None = Field(default=None, max_length=200)


class WatchRemoveRequest(BaseModel):
    watch_ids: list[int] = Field(min_length=1, max_length=MAX_ITEMS_PER_REQUEST)


class WatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    watch_id: int
    seller_id: str
    site: str
    group_id: int | None
    group_name: str | None
    priority: int
    note: str | None
    status: WatchStatus
    created_at: datetime

    new_discoveries_7d: int | None = Field(
        default=None,
        description=(
            "近 7 日新发现数。**None 表示该卖家还没有基线（从未被采集过），界面必须显示"
            "占位符而不是 0**——0 会被读成「这家店没有新发现」。有基线时是真实条数，含 0，"
            "此时 0 的含义才是「已采集范围内没有新发现」。"
            "MUST NOT 读作「上新数」：被动采集证明不了商品何时上架"
        ),
    )
    last_reported_at: datetime | None = Field(
        default=None,
        description=(
            "最近一次被采集的时刻，无上报时为 None。界面必须展示它并在陈旧时提示——"
            "「很久没采过」与「确实没有新发现」在界面上长得一模一样"
        ),
    )
    last_report_item_count: int | None = Field(
        default=None,
        description="最近一次上报的条目数，无上报时为 None。**不代表全店总量**，界面必须注明",
    )
    seller_name: str | None = Field(
        default=None, description="卖家店名（插件尽力解析），取不到时为 None，界面回落显示 seller_id"
    )


class SellerAsinsRequest(BaseModel):
    page: int = Field(1, ge=1)
    perPage: int = Field(50, ge=1, le=200)


class SellerAsinOut(BaseModel):
    asin: str
    title: str | None
    image_url: str | None
    first_seen_at: datetime = Field(description="首次发现于；不是上架时间")
    is_baseline: bool = Field(description="属于首次采集时就已存在的基线批次")


class SellerAsinsResponse(BaseModel):
    """一个卖家已采集到的商品。``total`` 是已采集到的条数，**不是该卖家的全部在售商品**。"""

    data: list[SellerAsinOut]
    total: int
    success: bool = True


class AsinDetailResponse(BaseModel):
    """一个 Amazon ASIN 的详情。两个时刻的可信度不同，字段文档必须说清楚。"""

    first_seen_at: datetime = Field(description="该卖家名下首次见到这个 ASIN 的时刻，真实数据")
    seller_last_reported_at: datetime | None = Field(
        default=None,
        description=(
            "该卖家最近一次被采集的时刻——**卖家级近似值，不是这个 ASIN 专属的**。"
            "同一卖家名下其他 ASIN 共享同一个值。当前 schema 没有条目级的"
            "「每次出现在哪些上报里」记录，只能给到这个粒度。界面展示时必须带上"
            "这句说明，不能只给一个裸时间戳"
        ),
    )
    success: bool = True


class WatchListResponse(BaseModel):
    """ProTable 契约 {data,total,success} + 配额回显。"""

    success: bool = True
    data: list[WatchOut] = Field(default_factory=list)
    total: int = 0

    watch_count: int = Field(description="当前用户的关注总数（不受 group_id 过滤影响）")
    max_watch: int = Field(description="MAX_WATCH_PER_USER 配额")


# ---------- 预览 / 确认两步 ----------


class PreviewRequest(BaseModel):
    """预览请求。``raw_text``/文件二选一——文件走 multipart，在 API 层单独接收，不进这个
    schema（Pydantic 模型不直接承载 UploadFile 与普通字段的混合表单）。"""

    raw_text: str | None = Field(default=None, description="粘贴的文本，一行一个链接或裸 ID")
    fallback_site: str | None = Field(
        default=None, description="站点信息缺失时的兜底站点码（裸 ID 必须提供）"
    )


class UnparsableEntry(BaseModel):
    text: str
    reason: str


class PreviewSample(BaseModel):
    seller_id: str
    site: str
    is_new: bool
    group_name: str | None = None


class PreviewResponse(BaseModel):
    preview_token: str
    expires_at: datetime

    parsed: int = Field(description="解析出有效卖家标识的条数（new + existing）")
    new: int = Field(description="其中系统未关注过的条数")
    existing: int = Field(description="其中已关注过的条数")
    unparsable: list[UnparsableEntry] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list, description="从书签文件夹拍平出的候选分组名")
    samples: list[PreviewSample] = Field(default_factory=list, max_length=20)


class CommitRequest(BaseModel):
    preview_token: str
    mode: Literal["append", "replace_group"] = "append"
    target_group_id: int | None = Field(
        default=None, description="replace_group 模式必填；append 模式可选（新建/已存在的分组归属该组）"
    )


class CommitResult(BaseModel):
    created_sellers: int = Field(description="新建的 competitor_seller 行数")
    created_watches: int = Field(description="新建的关注关系数")
    updated_watches: int = Field(description="已存在、本次更新了 priority 的关注关系数")
    removed_watches: int = Field(default=0, description="replace_group 模式下被清空的旧关注数")
    watch_count: int = Field(description="操作后当前用户的关注总数")
