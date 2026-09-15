"""ASIN/品牌悬浮历史的响应 DTO（issue #96）。

请求走 GET 查询参数（与 `/scope`、`/categories`、`/trend` 同风格），不用请求体 schema。
"""
from __future__ import annotations

from datetime import date as _Date
from typing import Literal

from pydantic import BaseModel, Field

#: 身份种类：ASIN 匹配三个商品槽位，BRAND 匹配三个品牌槽位。两者互不推断（design.md 决策 1）。
TopslotKind = Literal["ASIN", "BRAND"]


class TopslotHistoryItem(BaseModel):
    """一个命中的期次：日期 + 当期最佳槽位名次（1/2/3）。"""

    date: _Date
    rank: int


class TopslotHistoryResponse(BaseModel):
    """悬浮历史响应。

    ``total`` 是完整匹配范围内的精确期数，与本页返回的 ``items`` 条数无关——分页翻到
    最后一页时 ``len(items)`` 可能小于 ``page_size``，但 ``total`` 恒等于整体命中数。
    """

    success: bool = True
    total: int
    items: list[TopslotHistoryItem] = Field(default_factory=list)
