"""统一响应适配层：把列表数据整形为 Ant Design ProTable 契约 `{data, total, success}`。

见 design 决策 3 与 admin-spa spec「列表接口遵循统一的表格响应契约」：
供表格消费的列表 API 统一走这里，不逐接口散改 amis 形状。阶段四/六的 SPA 列表端点
只需 `return protable(items, total)`；失败用 `protable_error(msg)`。
"""
from typing import Generic, Optional, Sequence, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ProTableResponse(BaseModel, Generic[T]):
    """ProTable 列表契约。`success=False` 时前端据此统一提示，`data/total` 置空。"""

    success: bool = True
    data: list[T] = []
    total: int = 0
    message: Optional[str] = None


def protable(items: Sequence[T], total: int) -> ProTableResponse[T]:
    """成功：把一页数据与总数整形为 ProTable 契约。"""
    return ProTableResponse[T](success=True, data=list(items), total=total)


def protable_error(message: str) -> ProTableResponse:
    """失败：统一形状标识失败，data 空、total 0。"""
    return ProTableResponse(success=False, data=[], total=0, message=message)
