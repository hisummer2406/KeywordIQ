"""检索法域（design 决策 5）。与销售市场（``site_registry``）相互独立。

``vendor_code`` 是发给 P060 ``country`` 的取值。``verified=False`` 的取值未经真实联调确认，
界面上标「待核实」。市场 → 法域的推荐只用于界面提示，**必须由用户勾选**，服务端不自动加入。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Jurisdiction:
    code: str
    name_zh: str
    vendor_code: str
    note: str = ""
    verified: bool = True


JURISDICTIONS: dict[str, Jurisdiction] = {
    j.code: j
    for j in (
        Jurisdiction("US", "美国", "US"),
        Jurisdiction("GB", "英国", "GB"),
        Jurisdiction("DE", "德国", "DE", note="德国本国注册；不含欧盟外观设计"),
        # 2026-11-11 真实联调：EM 会被智慧芽静默忽略，EUIPO 的取值是 EU
        Jurisdiction("EU", "欧盟外观设计（EUIPO）", "EU",
                     note="覆盖欧盟统一外观设计，不覆盖成员国本国注册"),
        Jurisdiction("WO", "海牙国际注册（WIPO）", "WO",
                     note="海牙体系国际注册，指定国效力以各国为准"),
        Jurisdiction("FR", "法国", "FR"),
        Jurisdiction("IT", "意大利", "IT"),
        Jurisdiction("ES", "西班牙", "ES"),
        Jurisdiction("CN", "中国", "CN"),
        Jurisdiction("JP", "日本", "JP"),
        Jurisdiction("CA", "加拿大", "CA"),
    )
}

#: 销售市场 → 推荐勾选的法域（仅提示）。
MARKET_RECOMMENDATIONS: dict[str, tuple[str, ...]] = {
    "US": ("US",),
    "GB": ("GB", "WO"),
    "DE": ("DE", "EU", "WO"),
}

PAGE_SIZE_OPTIONS = (10, 20, 50)
DEFAULT_PAGE_SIZE = 20
#: P060 offset 上限（官方 0..1000）。
MAX_OFFSET = 1000
#: 一次补充详情最多选几件（P011/P013 支持 100，界面按 20 控制单次消耗）。
MAX_DETAIL_BATCH = 20
#: P010 图片上限 4096 KB。
MAX_IMAGE_BYTES = 4096 * 1024
