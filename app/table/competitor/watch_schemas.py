"""竞对监控（关注清单）的请求/响应 DTO。Pydantic v2。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 主表格状态三态。本变更（P0）没有数据获取层，baseline_at 恒为 NULL，因此只会产出
# PENDING_BASELINE；MONITORING/PAUSED 是为后续数据获取层预留的取值，界面渲染逻辑
# 按三态实现，避免那时还要改这一列。
WatchStatus = Literal["PENDING_BASELINE", "MONITORING", "PAUSED"]

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
