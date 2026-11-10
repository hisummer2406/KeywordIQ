"""生成 US/GB/DE 的核价费用规则**草稿**（change add-pricing-calculation D14）。

只写草稿，不启用：费率数值必须由维护人员在「费用配置」页逐项对照来源核对后再启用。
不生成汇率——汇率每天在变，由维护人员按实际结汇价录入。

数据来源（脚本运行时不联网，数值抄录自下列文档，2026-11-10 整理）：

- GB/DE FBA 配送费、仓储费、佣金：Amazon Europe Rate Card，Effective 1 July 2026（官方 PDF）
  https://m.media-amazon.com/images/G/02/sell/images/260630-FBA-Rate-Card-EN1.pdf
  第 6 页（本地配送非旺季）、第 7 页（2026-10-15～2027-01-14 旺季）、第 21 页（仓储）、
  第 28～30 页（佣金）。德国取「DE Only」列（未加入中欧计划 CEP）。
- US 佣金：https://sell.amazon.com/pricing （官方页面）
- US FBA 配送费、仓储费、旺季费：**二手来源** https://amzprep.com/amazon-fba-fees/ 与
  https://amzprep.com/holiday-peak-fulfillment-fees/ （2026-11-06 更新，称引自 Amazon
  2026-01-15 费率卡）。官方帮助页未能公开读取，启用前务必用卖家后台收入计算器复核。

幂等：同一 (kind, scope_key, effective_from, source) 已存在则跳过。

用法：
    uv run python scripts/pricing_seed_drafts.py --dry-run   # 只校验并打印将创建的条目
    uv run python scripts/pricing_seed_drafts.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path
from typing import Any

# 让脚本直接运行时也能 import 仓库根的模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EU_CARD = "Amazon Europe Rate Card, Effective 1 July 2026（官方 PDF 260630-FBA-Rate-Card-EN1.pdf）"
US_REFERRAL = "https://sell.amazon.com/pricing（官方，2026-11-10 读取）"
US_FBA = ("二手来源 amzprep.com/amazon-fba-fees 与 /holiday-peak-fulfillment-fees（2026-11-06 更新，"
          "称引自 Amazon 2026-01-15 费率卡）；启用前须以卖家后台收入计算器复核")
DEFAULTS_NOTE = ("广告 10%、退货 3%、结汇 1%、在仓 2 个月为 design Open Questions 的建议初值，"
                 "请按团队实际数据修改")


def _brackets(pairs: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"max_weight": w, "fee": f} for w, f in pairs]


# ---------------------------------------------------------------- 市场参数


def market_params() -> list[dict[str, Any]]:
    common = {"default_ads_rate": "0.10", "default_return_rate": "0.03", "default_payout_rate": "0.01",
              "default_storage_months": "2"}
    return [
        {"kind": "market_params", "scope_key": "US", "effective_from": date(2026, 1, 1),
         "source": "行业惯例：美国前台标价不含销售税（平台代收代缴）", "review_note": DEFAULTS_NOTE,
         "payload": {"price_includes_vat": False, "vat_rate": "0", **common}},
        {"kind": "market_params", "scope_key": "GB", "effective_from": date(2026, 1, 1),
         "source": "英国 VAT 标准税率 20%（含税标价）；Amazon 对英国卖家费用加收 2% 数字服务税附加",
         "review_note": DEFAULTS_NOTE + "；数字服务税附加 2% 请对照卖家后台费用发票核实",
         "payload": {"price_includes_vat": True, "vat_rate": "0.20", "import_vat_recoverable": True,
                     "amazon_fee_surcharge_rate": "0.02", **common}},
        {"kind": "market_params", "scope_key": "DE", "effective_from": date(2026, 1, 1),
         "source": "德国 VAT 标准税率 19%（含税标价）", "review_note": DEFAULTS_NOTE,
         "payload": {"price_includes_vat": True, "vat_rate": "0.19", "import_vat_recoverable": True, **common}},
    ]


# ---------------------------------------------------------------- 类目佣金


def _whole(key, name, tiers, min_fee):
    return {"key": key, "name": name, "mode": "whole", "min_fee": min_fee,
            "tiers": [{"up_to": u, "rate": r} if u else {"rate": r} for u, r in tiers]}


def _portion(key, name, tiers, min_fee):
    return {**_whole(key, name, tiers, min_fee), "mode": "portion"}


def _flat(key, name, rate, min_fee):
    return _whole(key, name, [(None, rate)], min_fee)


def referral() -> list[dict[str, Any]]:
    m = "0.30"
    us = [
        _flat("everything_else", "其他类目（Everything Else）", "0.15", m),
        _flat("home_kitchen", "家居厨房（Home and Kitchen）", "0.15", m),
        _whole("beauty_health", "美妆个护健康", [("10", "0.08"), (None, "0.15")], m),
        _whole("baby", "母婴", [("10", "0.08"), (None, "0.15")], m),
        _whole("clothing", "服装配饰", [("15", "0.05"), ("20", "0.10"), (None, "0.17")], m),
        _portion("electronics_accessories", "电子配件", [("100", "0.15"), (None, "0.08")], m),
        _flat("consumer_electronics", "消费电子", "0.08", m),
        _flat("computers", "电脑", "0.08", m),
        _flat("sports_outdoors", "运动户外", "0.15", m),
        _flat("toys_games", "玩具游戏", "0.15", m),
        _flat("tools_home_improvement", "工具家装", "0.15", m),
        _flat("pet_supplies", "宠物用品", "0.15", m),
        _flat("office_products", "办公用品", "0.15", m),
        _flat("lawn_garden", "园艺", "0.15", m),
        _flat("automotive", "汽车摩托", "0.12", m),
        _flat("luggage", "箱包", "0.15", m),
        _flat("footwear", "鞋靴", "0.15", m),
        _portion("furniture", "家具", [("200", "0.15"), (None, "0.10")], m),
        _portion("jewelry", "珠宝", [("250", "0.20"), (None, "0.05")], m),
    ]

    def eu(min_fee: str, auto_cut: str) -> list[dict[str, Any]]:
        return [
            _flat("everything_else", "其他类目（Everything else）", "0.15", min_fee),
            _flat("kitchen", "厨房（Kitchen）", "0.15", min_fee),
            _whole("home_products", "家居（Home Products）", [("20", "0.08"), (None, "0.15")], min_fee),
            _whole("beauty_health", "美妆个护健康", [("10", "0.08"), (None, "0.15")], min_fee),
            _whole("baby", "母婴", [("10", "0.08"), (None, "0.15")], min_fee),
            _whole("clothing", "服装配饰（不含 Prime 高价分段）", [("15", "0.05"), ("20", "0.10"), (None, "0.15")],
                   min_fee),
            _portion("electronics_accessories", "电子配件", [("100", "0.15"), (None, "0.08")], min_fee),
            _flat("consumer_electronics", "消费电子", "0.07", min_fee),
            _flat("computers", "电脑", "0.07", min_fee),
            _flat("sports_outdoors", "运动户外", "0.15", min_fee),
            _flat("toys_games", "玩具游戏", "0.15", min_fee),
            _flat("tools_home_improvement", "工具家装", "0.13", min_fee),
            _flat("pet_supplies", "宠物用品", "0.15", min_fee),
            _flat("office_products", "办公用品", "0.15", min_fee),
            _flat("lawn_garden", "园艺", "0.15", min_fee),
            _portion("automotive", "汽车摩托（其他）", [(auto_cut, "0.15"), (None, "0.09")], min_fee),
            _flat("luggage", "箱包", "0.15", min_fee),
            _flat("footwear", "鞋靴", "0.15", min_fee),
        ]

    note_eu = "服装类 Prime 售价 >£40/€45 的分段（15%/7%）未建模；家具、珠宝分段未录入"
    return [
        {"kind": "referral_schedule", "scope_key": "US", "effective_from": date(2026, 1, 15), "source": US_REFERRAL,
         "review_note": "仅录入常用类目；最低佣金 $0.30（Grocery 等无最低佣金的类目未录入）",
         "payload": {"categories": us}},
        {"kind": "referral_schedule", "scope_key": "GB", "effective_from": date(2026, 7, 1),
         "source": EU_CARD + " 第 28～30 页", "review_note": note_eu + "；最低佣金 £0.25",
         "payload": {"categories": eu("0.25", "45")}},
        {"kind": "referral_schedule", "scope_key": "DE", "effective_from": date(2026, 7, 1),
         "source": EU_CARD + " 第 28～30 页", "review_note": note_eu + "；最低佣金 €0.30",
         "payload": {"categories": eu("0.30", "50")}},
    ]


# ---------------------------------------------------------------- FBA：欧洲（GB/DE）


def _eu_tiers() -> list[dict[str, Any]]:
    """EU 尺寸段（第 6 页）。信封按实重，包裹与大件按实重/体积重较大者，体积重 = cm³ / 5000。"""
    return [
        {"key": "light_envelope", "name": "轻型信封", "max_longest": "33", "max_median": "23",
         "max_shortest": "2.5", "max_unit_weight": "0.1", "use_dim_weight": False},
        {"key": "standard_envelope", "name": "标准信封", "max_longest": "33", "max_median": "23",
         "max_shortest": "2.5", "max_unit_weight": "0.46", "use_dim_weight": False},
        {"key": "large_envelope", "name": "大号信封", "max_longest": "33", "max_median": "23",
         "max_shortest": "4", "max_unit_weight": "0.96", "use_dim_weight": False},
        {"key": "xl_envelope", "name": "超大号信封", "max_longest": "33", "max_median": "23",
         "max_shortest": "6", "max_unit_weight": "0.96", "use_dim_weight": False},
        {"key": "small_parcel", "name": "小号包裹", "max_longest": "35", "max_median": "25", "max_shortest": "12",
         "max_unit_weight": "3.9", "max_dim_weight": "2.10"},
        {"key": "standard_parcel", "name": "标准包裹", "max_longest": "45", "max_median": "34",
         "max_shortest": "26", "max_unit_weight": "11.9", "max_dim_weight": "7.96"},
        {"key": "small_oversize", "name": "小号大件", "max_longest": "61", "max_median": "46", "max_shortest": "46",
         "max_unit_weight": "1.76", "max_dim_weight": "25.82", "storage_group": "oversize"},
        {"key": "std_oversize_light", "name": "标准大件（轻）", "max_longest": "101", "max_median": "60",
         "max_shortest": "60", "max_unit_weight": "15", "max_dim_weight": "72.72", "storage_group": "oversize"},
        {"key": "std_oversize_heavy", "name": "标准大件（重）", "max_longest": "101", "max_median": "60",
         "max_shortest": "60", "max_unit_weight": "23", "max_dim_weight": "72.72", "storage_group": "oversize"},
        {"key": "std_oversize_large", "name": "标准大件（大）", "max_longest": "120", "max_median": "60",
         "max_shortest": "60", "max_unit_weight": "23", "max_dim_weight": "86.40", "storage_group": "oversize"},
        {"key": "bulky_oversize", "name": "大件（超尺寸）", "max_longest": "175", "max_length_girth": "360",
         "max_unit_weight": "23", "max_dim_weight": "126", "storage_group": "oversize"},
        {"key": "heavy_oversize", "name": "重型大件", "max_longest": "175", "max_length_girth": "360",
         "max_unit_weight": "31.5", "max_dim_weight": "126", "storage_group": "oversize"},
    ]


#: 列顺序：(重量上限 kg, UK 非旺季 £, DE Only 非旺季 €, UK 旺季 £, DE Only 旺季 €)
_EU_FEES: dict[str, list[tuple[str, str, str, str, str]]] = {
    "light_envelope": [("0.02", "1.83", "2.33", "1.83", "2.33"), ("0.04", "1.87", "2.37", "1.87", "2.37"),
                       ("0.06", "1.89", "2.39", "1.89", "2.39"), ("0.08", "2.07", "2.52", "2.07", "2.52"),
                       ("0.1", "2.08", "2.54", "2.08", "2.54")],
    "standard_envelope": [("0.21", "2.10", "2.57", "2.10", "2.57"), ("0.46", "2.16", "2.68", "2.16", "2.68")],
    "large_envelope": [("0.96", "2.72", "3.04", "2.79", "3.04")],
    "xl_envelope": [("0.96", "2.94", "3.42", "3.01", "3.42")],
    "small_parcel": [("0.15", "2.91", "3.38", "3.02", "3.59"), ("0.4", "3.00", "3.39", "3.11", "3.60"),
                     ("0.9", "3.04", "3.40", "3.15", "3.61"), ("1.4", "3.05", "3.41", "3.16", "3.63"),
                     ("1.9", "3.25", "3.43", "3.37", "3.65"), ("3.9", "3.27", "4.54", "3.39", "4.83")],
    "standard_parcel": [("0.15", "2.94", "3.39", "3.05", "3.60"), ("0.4", "3.01", "3.42", "3.12", "3.64"),
                        ("0.9", "3.06", "3.44", "3.18", "3.66"), ("1.4", "3.26", "3.93", "3.38", "4.18"),
                        ("1.9", "3.48", "3.95", "3.61", "4.20"), ("2.9", "3.49", "4.55", "3.62", "4.84"),
                        ("3.9", "3.54", "5.09", "3.67", "5.42"), ("5.9", "3.56", "5.22", "3.69", "5.56"),
                        ("8.9", "3.57", "6.03", "3.70", "6.42"), ("11.9", "3.58", "6.65", "3.71", "7.09")],
}
#: 大件：(基础重量 kg, UK 基础 £, UK 每 kg £, DE 基础 €, DE 每 kg €)，旺季与非旺季相同，
#: 唯一例外是小号大件 UK 非旺季 +0.25/kg、旺季 +0.22/kg（第 6、7 页原文如此）。
_EU_OVERSIZE: dict[str, tuple[str, str, str, str, str]] = {
    "small_oversize": ("0.76", "3.49", "0.25", "4.56", "0.18"),
    "std_oversize_light": ("0.76", "4.35", "0.15", "4.59", "0.18"),
    "std_oversize_heavy": ("15.76", "6.58", "0.08", "7.25", "0.07"),
    "std_oversize_large": ("0.76", "5.67", "0.07", "6.06", "0.08"),
    "bulky_oversize": ("0.76", "10.20", "0.24", "8.24", "0.27"),
    "heavy_oversize": ("31.5", "13.04", "0.09", "13.00", "0.15"),
}


def _eu_card(site: str, peak: bool) -> dict[str, Any]:
    col = {("GB", False): 1, ("DE", False): 2, ("GB", True): 3, ("DE", True): 4}[(site, peak)]
    fees = []
    for tier, rows in _EU_FEES.items():
        fees.append({"tier": tier, "brackets": _brackets([(r[0], r[col]) for r in rows])})
    for tier, (base_w, uk_base, uk_kg, de_base, de_kg) in _EU_OVERSIZE.items():
        base, per_kg = (uk_base, uk_kg) if site == "GB" else (de_base, de_kg)
        if site == "GB" and tier == "small_oversize" and peak:
            per_kg = "0.22"
        fees.append({"tier": tier, "brackets": _brackets([(base_w, base)]),
                     "overflow": {"above": base_w, "base_fee": base, "step_weight": "1", "step_fee": per_kg}})
    if site == "GB":  # 第 21 页：英国按立方英尺
        storage = [
            {"storage_group": "standard", "months": list(range(1, 10)), "fee_per_volume": "0.76"},
            {"storage_group": "standard", "months": [10, 11, 12], "fee_per_volume": "1.51"},
            {"storage_group": "oversize", "months": list(range(1, 10)), "fee_per_volume": "0.55"},
            {"storage_group": "oversize", "months": [10, 11, 12], "fee_per_volume": "0.87"},
        ]
        unit = "cuft"
    else:
        storage = [
            {"storage_group": "standard", "months": list(range(1, 10)), "fee_per_volume": "27.54"},
            {"storage_group": "standard", "months": [10, 11, 12], "fee_per_volume": "52.20"},
            {"storage_group": "oversize", "months": list(range(1, 10)), "fee_per_volume": "21.78"},
            {"storage_group": "oversize", "months": [10, 11, 12], "fee_per_volume": "34.49"},
        ]
        unit = "m3"
    return {"unit_system": "metric", "dim_divisor": "5000", "tiers": _eu_tiers(), "fees": fees,
            "fuel_surcharge_rate": "0.015", "storage_volume_unit": unit, "storage_rates": storage}


def eu_fba() -> list[dict[str, Any]]:
    note = ("本地配送（非 EFN、非低价 FBA）；超大件每 kg 加收按「不足 1 kg 按 1 kg」处理，请核实；"
            "特殊大件（>31.5kg 或最长边 >175cm）未录入，可手填配送费；仓储为非服装类标准尺寸费率，"
            "未含仓储利用率附加费与超龄库存附加费；燃油物流附加 1.5% 自 2026-04-17 起")
    de_note = "；德国取「DE Only」列（未加入中欧计划 CEP，含 €0.26 附加），加入 CEP 的卖家每件少 €0.26"
    out = []
    for site in ("GB", "DE"):
        n = note + (de_note if site == "DE" else "")
        out += [
            {"kind": "fba_rate_card", "scope_key": site, "effective_from": date(2026, 7, 1),
             "effective_to": date(2026, 10, 14), "source": EU_CARD + " 第 6、21 页", "review_note": n,
             "payload": _eu_card(site, peak=False)},
            {"kind": "fba_rate_card", "scope_key": site, "effective_from": date(2026, 10, 15),
             "effective_to": date(2027, 1, 14), "source": EU_CARD + " 第 7、21 页（旺季）", "review_note": n,
             "payload": _eu_card(site, peak=True)},
            {"kind": "fba_rate_card", "scope_key": site, "effective_from": date(2027, 1, 15),
             "source": EU_CARD + " 第 7 页：2027-01-15 起恢复 2026 非旺季费率",
             "review_note": n + "；2027 年如有新费率卡请改为新版本", "payload": _eu_card(site, peak=False)},
        ]
    return out


# ---------------------------------------------------------------- FBA：美国


def _us_tiers() -> list[dict[str, Any]]:
    return [
        {"key": "small_standard", "name": "小号标准", "max_longest": "15", "max_median": "12",
         "max_shortest": "0.75", "max_unit_weight": "1", "use_dim_weight": False},
        {"key": "large_standard", "name": "大号标准", "max_longest": "18", "max_median": "14", "max_shortest": "8",
         "max_unit_weight": "20"},
        {"key": "small_bulky", "name": "小号大件", "max_longest": "37", "max_median": "28", "max_shortest": "20",
         "max_length_girth": "130", "max_unit_weight": "50", "storage_group": "oversize"},
        {"key": "large_bulky", "name": "大号大件", "max_longest": "59", "max_median": "33", "max_shortest": "33",
         "max_length_girth": "130", "max_unit_weight": "50", "storage_group": "oversize"},
        {"key": "xl_50", "name": "超大件 0-50 lb", "max_unit_weight": "50", "storage_group": "oversize"},
        {"key": "xl_70", "name": "超大件 50-70 lb", "max_unit_weight": "70", "storage_group": "oversize"},
        {"key": "xl_150", "name": "超大件 70-150 lb", "max_unit_weight": "150", "storage_group": "oversize"},
        {"key": "xl_150_plus", "name": "超大件 150 lb 以上", "storage_group": "oversize"},
    ]


_US_SMALL = ["0.125", "0.25", "0.375", "0.5", "0.625", "0.75", "0.875", "1"]
_US_LARGE = ["0.25", "0.5", "0.75", "1", "1.25", "1.5", "1.75", "2", "2.25", "2.5", "2.75", "3"]
#: 价格段 <$10、$10–50、>$50 各一列
_US_NONPEAK = {
    "small_standard": [("2.43", "3.32", "3.58"), ("2.49", "3.42", "3.68"), ("2.56", "3.45", "3.71"),
                       ("2.66", "3.54", "3.80"), ("2.77", "3.68", "3.94"), ("2.82", "3.78", "4.04"),
                       ("2.92", "3.91", "4.17"), ("2.95", "3.96", "4.22")],
    "large_standard": [("2.91", "3.73", "3.99"), ("3.13", "3.95", "4.21"), ("3.38", "4.20", "4.46"),
                       ("3.78", "4.60", "4.86"), ("4.22", "5.04", "5.30"), ("4.60", "5.42", "5.68"),
                       ("4.75", "5.57", "5.83"), ("5.00", "5.82", "6.08"), ("5.10", "5.92", "6.18"),
                       ("5.28", "6.10", "6.36"), ("5.44", "6.26", "6.52"), ("5.85", "6.67", "6.93")],
    "large_standard_3lb": ("6.15", "6.97", "7.23"),
}
_US_PEAK = {
    "small_standard": [("2.62", "3.51", "3.77"), ("2.68", "3.61", "3.87"), ("2.76", "3.65", "3.91"),
                       ("2.86", "3.74", "4.00"), ("2.98", "3.89", "4.15"), ("3.03", "3.99", "4.25"),
                       ("3.14", "4.13", "4.39"), ("3.17", "4.18", "4.44")],
    "large_standard": [("3.15", "3.97", "4.23"), ("3.39", "4.21", "4.47"), ("3.66", "4.48", "4.74"),
                       ("4.07", "4.89", "5.15"), ("4.52", "5.34", "5.60"), ("4.91", "5.73", "5.99"),
                       ("5.07", "5.89", "6.15"), ("5.33", "6.15", "6.41"), ("5.47", "6.29", "6.55"),
                       ("5.67", "6.49", "6.75"), ("5.84", "6.66", "6.92"), ("6.26", "7.08", "7.34")],
    "large_standard_3lb": ("6.69", "7.51", "7.77"),
}
#: 大件：(基础重量 lb, 每 lb 加收, 非旺季三档, 旺季三档)
_US_BULKY = {
    "small_bulky": ("1", "0.38", ("6.78", "7.55", "7.55"), ("7.82", "8.59", "8.59")),
    "large_bulky": ("1", "0.38", ("8.58", "9.35", "9.35"), ("9.62", "10.39", "10.39")),
    "xl_50": ("1", "0.38", ("25.56", "26.33", "26.33"), ("28.29", "29.06", "29.06")),
    "xl_70": ("51", "0.75", ("36.55", "37.32", "37.32"), ("39.36", "40.13", "40.13")),
    "xl_150": ("71", "0.75", ("50.55", "51.32", "51.32"), ("54.97", "55.74", "55.74")),
    "xl_150_plus": ("151", "0.19", ("194.18", "194.95", "194.95"), ("202.69", "203.46", "203.46")),
}
_BANDS = [(None, "10"), ("10", "50"), ("50", None)]


def _us_card(peak: bool) -> dict[str, Any]:
    table = _US_PEAK if peak else _US_NONPEAK
    fees = []
    for i, (lo, hi) in enumerate(_BANDS):
        band = {k: v for k, v in (("price_min", lo), ("price_max", hi)) if v is not None}
        fees.append({"tier": "small_standard", **band,
                     "brackets": _brackets([(w, row[i]) for w, row in zip(_US_SMALL, table["small_standard"],
                                                                           strict=True)])})
        base3 = table["large_standard_3lb"][i]
        fees.append({"tier": "large_standard", **band,
                     "brackets": _brackets([(w, row[i]) for w, row in zip(_US_LARGE, table["large_standard"],
                                                                           strict=True)]),
                     "overflow": {"above": "3", "base_fee": base3, "step_weight": "0.25", "step_fee": "0.08"}})
        for tier, (base_w, per_lb, nonpeak, peak_fees) in _US_BULKY.items():
            base = (peak_fees if peak else nonpeak)[i]
            fees.append({"tier": tier, **band, "brackets": _brackets([(base_w, base)]),
                         "overflow": {"above": base_w, "base_fee": base, "step_weight": "1", "step_fee": per_lb}})
    storage = [
        {"storage_group": "standard", "months": list(range(1, 10)), "fee_per_volume": "0.78"},
        {"storage_group": "standard", "months": [10, 11, 12], "fee_per_volume": "2.40"},
        {"storage_group": "oversize", "months": list(range(1, 10)), "fee_per_volume": "0.56"},
        {"storage_group": "oversize", "months": [10, 11, 12], "fee_per_volume": "1.40"},
    ]
    return {"unit_system": "imperial", "dim_divisor": "139", "tiers": _us_tiers(), "fees": fees,
            "fuel_surcharge_rate": "0.035", "storage_volume_unit": "cuft", "storage_rates": storage,
            "placement_fees": {"amazon_optimized": {}}}


def us_fba() -> list[dict[str, Any]]:
    note = ("非服装、非危险品费率；小号/大号大件尺寸门槛按 Amazon 2024 起的尺寸分段，未含大件 2 英寸最小宽高假设；"
            "仓储为基础月费，未含仓储利用率附加费；入库配置费只录入「Amazon 优化拆分」（无费用），"
            "最小拆分费用按地区浮动未录入；燃油物流附加 3.5% 自 2026-04-17 起")
    return [
        {"kind": "fba_rate_card", "scope_key": "US", "effective_from": date(2026, 4, 17),
         "effective_to": date(2026, 10, 14), "source": US_FBA, "review_note": note,
         "payload": _us_card(peak=False)},
        {"kind": "fba_rate_card", "scope_key": "US", "effective_from": date(2026, 10, 15),
         "effective_to": date(2027, 1, 14), "source": US_FBA + "（旺季）",
         "review_note": note + "；大件旺季每 lb 加收来源未列出，沿用非旺季值；2027-01-15 起 Amazon 已宣布新费率，未录入",
         "payload": _us_card(peak=True)},
    ]


def all_drafts() -> list[dict[str, Any]]:
    return market_params() + referral() + eu_fba() + us_fba()


# ---------------------------------------------------------------- 写库


async def _run(dry_run: bool) -> int:
    from sqlalchemy import select

    from app.table.pricing import rule_service
    from app.table.pricing.pricing_model import PricingRule
    from app.table.pricing.pricing_schemas import RuleCreate
    from database import AsyncSessionFactory

    drafts = all_drafts()
    for d in drafts:  # 先全部校验，任何一条不合法都不写库
        rule_service.validate_payload(d["kind"], d["scope_key"], d["payload"])
    if dry_run:
        for d in drafts:
            print(f"[dry-run] {d['kind']:<18} {d['scope_key']:<4} {d['effective_from']}～{d.get('effective_to') or ''}")
        print(f"共 {len(drafts)} 条，全部通过载荷校验")
        return 0

    created = skipped = 0
    async with AsyncSessionFactory() as db:
        for d in drafts:
            exists = (await db.execute(select(PricingRule.id).where(
                PricingRule.kind == d["kind"], PricingRule.scope_key == d["scope_key"],
                PricingRule.effective_from == d["effective_from"], PricingRule.source == d["source"],
            ))).first()
            if exists:
                skipped += 1
                continue
            await rule_service.create_draft(db, None, RuleCreate(**d))  # type: ignore[arg-type]
            created += 1
    print(f"新建草稿 {created} 条，已存在跳过 {skipped} 条。请在「定价核算 → 费用配置」逐项核对后启用；汇率需手工录入。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="只校验并打印，不写库")
    args = parser.parse_args()
    return asyncio.run(_run(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
