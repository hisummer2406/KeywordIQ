"""统一响应适配层：把列表数据整形为 Ant Design ProTable 契约 `{data, total, success}`。

见 design 决策 3 与 admin-spa spec「列表接口遵循统一的表格响应契约」：
供表格消费的列表 API 统一走这里，不逐接口散改 amis 形状。阶段四/六的 SPA 列表端点
只需 `return protable(items, total)`；**失败一律抛异常**，由全局处理器产出统一错误体——
列表失败若混在 HTTP 200 里，前端无法把"查询失败"和"没有数据"区分开。
"""
from collections.abc import Sequence
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ProTableResponse(BaseModel, Generic[T]):
    """ProTable 列表契约。**只表达成功**——失败走统一错误契约的非 2xx 路径
    （见 app/common/error_handlers.py），不再以 HTTP 200 + success:false 表达。"""

    success: bool = True
    data: list[T] = []
    total: int = 0
    message: str | None = None


def protable(items: Sequence[T], total: int) -> ProTableResponse[T]:
    """成功：把一页数据与总数整形为 ProTable 契约。"""
    return ProTableResponse[T](success=True, data=list(items), total=total)

