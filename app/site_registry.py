"""站点清单 —— 站点码的唯一事实来源。

导入链路、查询链路与分区管理都必须从这里取站点定义，不得各自维护一份。

站点码使用 ISO 3166-1 alpha-2 大写代码。注意英国用 ``GB`` 而非 ``UK``——亚马逊
自身在不同接口里两种写法都出现过，本项目统一为 ISO 标准的 ``GB``，避免同一站点
在库中出现两种写法。

新增站点只需在 :data:`SITE_CATALOG` 中增加一项并把站点码加入 ``ENABLED_SITES``
配置，不需要修改任何表结构（事实表会在导入时按需创建该站点的分区）。

本模块**不导入** ``config``，以免与 ``config`` 对站点码的校验形成循环导入。

``extension/src/site-map.ts``（浏览器插件，issue #56 P3a）手工镜像了这里的站点
集合——它是纯前端 JS，无法直接 import 本模块。新增站点时那份镜像要同步更新，
否则该站点的卖家会在插件侧被判定为"未识别的站点"而无法采集。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Site:
    """一个亚马逊站点的静态元数据。"""

    code: str
    """ISO 3166-1 alpha-2 大写站点码，同时是库中 ``site`` 列的取值。"""

    name_zh: str
    """中文显示名，用于界面。"""

    timezone: str
    """站点所在时区，用于解释报表日期的口径。"""

    domain: str
    """站点域名，用于拼接商品链接。"""

    currency: str
    """站点前台标价币种（ISO 4217），核价的售价以此币种表示。"""

    currency_minor_unit: str
    """该币种的最小货币单位（十进制字符串，如 ``"0.01"``），核价售价按它取整。

    用字符串而非 float：核价全程是 ``Decimal`` 定点运算，浮点字面量会把误差带进去。
    """


SITE_CATALOG: dict[str, Site] = {
    "US": Site(code="US", name_zh="美国", timezone="America/Los_Angeles", domain="amazon.com",
               currency="USD", currency_minor_unit="0.01"),
    "GB": Site(code="GB", name_zh="英国", timezone="Europe/London", domain="amazon.co.uk",
               currency="GBP", currency_minor_unit="0.01"),
    "DE": Site(code="DE", name_zh="德国", timezone="Europe/Berlin", domain="amazon.de",
               currency="EUR", currency_minor_unit="0.01"),
}
"""已知站点的静态目录。在此登记不等于启用——启用与否由 ``ENABLED_SITES`` 决定。"""


class UnknownSiteError(ValueError):
    """站点码不在 :data:`SITE_CATALOG` 中。"""


def normalize_site_code(value: str | None) -> str:
    """把外部传入的站点码规整为目录中的标准写法。

    去除首尾空白并转为大写。空值与未登记的站点码一律抛 :class:`UnknownSiteError`,
    绝不静默回退到默认站点——静默回退会让数据写错归属且无人察觉。
    """
    if value is None:
        raise UnknownSiteError("站点码不能为空")

    code = value.strip().upper()
    if not code:
        raise UnknownSiteError("站点码不能为空")

    if code not in SITE_CATALOG:
        known = ", ".join(sorted(SITE_CATALOG))
        raise UnknownSiteError(f"未知站点码 {value!r}，已登记的站点为: {known}")

    return code


def is_known_site(value: str | None) -> bool:
    """站点码是否已在目录中登记。不抛异常，供条件判断使用。"""
    try:
        normalize_site_code(value)
    except UnknownSiteError:
        return False
    return True


def get_site(code: str) -> Site:
    """按站点码取元数据。站点码会先经过规整。"""
    return SITE_CATALOG[normalize_site_code(code)]


def normalize_site_codes(values: Iterable[str]) -> list[str]:
    """批量规整站点码，去重并按字母序返回。任一非法即抛异常。"""
    return sorted({normalize_site_code(value) for value in values})
