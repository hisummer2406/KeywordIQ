"""十进制定点工具（design D2）。

全程 ``Decimal``，禁止 float：``Decimal(0.1)`` 会把二进制误差原样带进来，而达标判断
恰恰卡在分位上。入参边界统一走 :func:`to_decimal`，float 直接拒绝。
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext

CENT = Decimal("0.01")
ZERO = Decimal("0")
ONE = Decimal("1")


def to_decimal(value: object) -> Decimal:
    """把字符串/整数/Decimal 转成 Decimal；float 与 bool 一律拒绝。"""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("核价金额不接受浮点数或布尔值，请传十进制字符串")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("金额必须是有限数")
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        d = Decimal(value.strip())
        if not d.is_finite():
            raise ValueError("金额必须是有限数")
        return d
    raise TypeError(f"无法转换为金额：{type(value).__name__}")


def money(value: Decimal) -> Decimal:
    """单项收费舍到分（四舍五入），各币种最小单位都是 0.01 时使用。"""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def ceil_to(value: Decimal, unit: Decimal) -> Decimal:
    """向上取到 ``unit`` 的整数倍（售价按最小货币单位、计费重按进位粒度）。"""
    with localcontext() as ctx:
        ctx.prec = 28
        steps = (value / unit).to_integral_value(rounding=ROUND_CEILING)
        return (steps * unit).quantize(unit) if unit.as_tuple().exponent < 0 else steps * unit


def floor_to(value: Decimal, unit: Decimal) -> Decimal:
    """向下取到 ``unit`` 的整数倍（采购价上限向下取分）。"""
    with localcontext() as ctx:
        ctx.prec = 28
        steps = (value / unit).to_integral_value(rounding=ROUND_FLOOR)
        return (steps * unit).quantize(unit) if unit.as_tuple().exponent < 0 else steps * unit


def ratio(numer: Decimal, denom: Decimal) -> Decimal | None:
    """分母为 0 返回 ``None``——界面据此不显示百分比，而不是显示 0% 或无穷。"""
    if denom == 0:
        return None
    return numer / denom
