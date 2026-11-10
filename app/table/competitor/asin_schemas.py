"""竞对卖家 ASIN 上报的请求/响应 DTO。Pydantic v2。

一次上报只针对**一个** `(seller_id, site)`——所以「未关注的卖家」是整个请求的问题，
没有"逐条拒绝"可言；而单条 ASIN 格式非法只是一条不可用的数据，逐条拒绝、其余照写。

这两种处理方式并存不是随意，判据是**这样做会不会造出一份无法与正常数据区分的残缺记录**：

- **条数超限 → 整批拒（422）**：超限意味着采集端手上的条目比它送出的多，此时写入即是一次
  沉默的截断，而截断后的记录与"这家店只有这些品"在数据上完全一致，事后无法分辨。
- **单条格式非法 → 逐条拒、其余照写**：丢弃它不制造任何完整性假象，而把原因回传能让采集端
  作者发现自己的选择器取错了元素。
- **文本超长 → 截断不拒绝**：标题/图片地址/页面上下文是展示用的附属属性，一个过长的标题
  不该让整批观测数据丢失。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import settings

#: ASIN 形态。放宽到 10 位字母数字：德国站图书的 ASIN 就是 ISBN-10、不以 `B0` 开头
#: （与 `AnalysisSearchRequest` 那条路由启发式同一个事实）。这里是**校验**不是路由，
#: 所以按长度与字符集判，不按 `B0` 前缀判——按前缀判会把图书类目整个拒掉。
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")

#: 展示属性的长度上限。超长截断而非拒绝，见模块 docstring。
MAX_TITLE_LEN = 512
MAX_IMAGE_URL_LEN = 1024
MAX_PAGE_CONTEXT_LEN = 200

#: 逐条拒绝的原因码。面向采集端作者，不是面向终端用户——它要能指出"你的选择器取错了"。
RejectReason = Literal["asin_invalid", "asin_empty"]


def truncate(value: str | None, limit: int) -> str | None:
    """按上限截断，`None` 原样返回。"""
    if value is None:
        return None
    value = value.strip()
    return value[:limit] if len(value) > limit else value


class AsinReportItem(BaseModel):
    """上报的一个条目。标题与图片是可选的展示属性，缺了不影响这条被接受。"""

    model_config = ConfigDict(extra="ignore")

    asin: str = Field(description="亚马逊商品标识；大小写与首尾空白由服务端抹平")
    title: str | None = Field(default=None, description="见到时的商品标题，超长截断")
    image_url: str | None = Field(default=None, description="见到时的主图地址，超长截断")


class AsinReportRequest(BaseModel):
    """一次上报。

    `extra="ignore"` 是有意的：请求体里塞 `user_id` 之类的身份字段**一律无视**，归属只从
    访问令牌取。用 `extra="forbid"` 会把它变成一个 422 错误，反而暗示"这个字段是被认识的"。
    """

    model_config = ConfigDict(extra="ignore")

    request_id: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "客户端生成的幂等键，**必填**。可选的幂等键等于没有：省略它的客户端得不到保护，"
            "服务端也无从分辨「这个客户端不支持」与「这是一次新观测」"
        ),
    )
    seller_id: str = Field(min_length=1, max_length=24, description="卖家 ID")
    site: str = Field(min_length=1, max_length=8, description="站点码，取自 site_registry")
    captured_at: datetime | None = Field(
        default=None,
        description=(
            "采集端声称的采集时刻。**只作审计**：基线与首次见到时刻一律取服务端事务时间，"
            "客户端时钟不可信也不可验证"
        ),
    )
    ship_to_region: str | None = Field(
        default=None, max_length=8, description="采集时账号的配送地区码，只留档不参与判定"
    )
    page_context: str | None = Field(
        default=None, description="采集时的页面上下文（排序方式、页码等），超长截断"
    )
    # 店名是公开的展示属性（#166 D5）：超长截断、空白视同没给；**不进 request_hash**
    # （与标题、图片同理，易变且不参与任何判定），只在非重放且非空时覆盖共享卖家行。
    seller_name: str | None = Field(
        default=None, description="卖家店名（插件尽力解析，取不到不传），超长截断，不参与任何判定"
    )
    items: list[AsinReportItem] = Field(
        default_factory=list, description="本次见到的条目；不承诺是该卖家的全部商品"
    )

    @field_validator("items")
    @classmethod
    def _cap_items(cls, value: list[AsinReportItem]) -> list[AsinReportItem]:
        # 上限在这里执行而不是在 service 里，是为了让超限在解析阶段就变成 422，一条都不写。
        cap = settings.COMPETITOR_MAX_REPORT_ITEMS
        if len(value) > cap:
            raise ValueError(
                f"单次上报最多 {cap} 条，本次 {len(value)} 条；超限整批拒绝，不会截断后写入"
            )
        return value


class CollectorConfigResponse(BaseModel):
    """采集端配置（competitor-asin-ingest「服务端公布单批上限」）。只含运行时配置，不含任何用户或卖家数据。"""

    max_report_items: int = Field(description="单次上报条目数上限；超限整批 422，不截断")


class RejectedItem(BaseModel):
    """被拒的单个条目及原因。**不静默丢弃**——采集端要能看出是哪一条、为什么。"""

    asin: str = Field(description="原样回显上报值，便于采集端定位是哪个元素取错了")
    reason: RejectReason


class AsinReportResponse(BaseModel):
    """上报结果。

    `duplicate_of` 非空表示这次是**重放**：命中了幂等键且请求摘要一致，服务端没有新增
    任何记录，返回的计数来自那条原始记录。
    """

    report_id: int = Field(description="本次（或被重放的那次）上报记录标识")
    accepted: int = Field(description="通过校验、参与写入的条目数（已去重）")
    new_asins: int = Field(description="其中真正新入库的条目数；重复上报时为 0")
    baseline_established: bool = Field(description="本次是否为该卖家落定了基线")
    duplicate_of: int | None = Field(
        default=None, description="非空即本次是重放，值为原始记录标识；此时未写入任何数据"
    )
    rejected: list[RejectedItem] = Field(
        default_factory=list, description="逐条被拒的条目及原因"
    )
