"""把原核价 Excel 的自发货渠道导入为**草稿**（change add-pricing-calculation D7、D14；PRICE-16）。

只导入已登记站点（目前 US/GB/DE）的行，其余国家只计数报告。每条渠道都：

- 保存为草稿，``source`` 与 ``source_cells`` 指回原表工作表与单元格；
- 运费按原表公式 ``=固定费+单价*F行(+附加)`` 解析成单一重量区间；欧盟线路公式里的 ``+24``
  **不是挂号费**（同一公式已有挂号费），作为「性质待核实」的固定附加费单列；
- 包税/不包税/停发与备注、渠道编号与容量限制的冲突都写进审核说明，不自动启用。

原表是业务文件，不进仓库；用参数指定路径。只用标准库读 xlsx（不新增依赖）。

用法：
    uv run python scripts/pricing_import_excel.py "docs/售价计算表--26.8.17(1).xlsx" --dry-run
    uv run python scripts/pricing_import_excel.py "docs/售价计算表--26.8.17(1).xlsx"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

# 让脚本直接运行时也能 import 仓库根的模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

SITE_BY_NAME = {"美国": "US", "英国": "GB", "德国": "DE"}
EFFECTIVE_FROM = date(2026, 8, 17)  # 原表文件名中的日期
SUMMARY_SHEET = "渠道特征汇总"

#: 工作表名前缀 → (承运商, 渠道码前缀)
CARRIERS = {"云途": ("云途", "YT"), "华邮通": ("华邮通", "HYT"), "华翰": ("华翰", "HH"), "通邮": ("通邮", "TY"),
            "CNE": ("CNE", "CNE"), "菜鸟": ("菜鸟", "CN")}

#: 原表「渠道特征汇总」与测算表之间已知的冲突（PRD §2）。键为渠道码中的编号。
KNOWN_CONFLICTS = {
    "1035": "渠道特征汇总 B10 写美国特敏感渠道编号 1077，测算表通邮!B9 写 1035，身份待核实",
    "1327": "渠道特征汇总 C11 写美国液体限 200ml、欧洲限 100ml，测算表渠道名写 100ml，容量限制待核实",
    "PK0312": "渠道特征汇总 C7 写液体 300ml、膏体 500ml，本系统容量上限只有一个值，暂按 300ml",
}

LIQUID_ATTRS = ["general", "liquid", "paste", "cosmetic"]


# ---------------------------------------------------------------- 读 xlsx（标准库）


@dataclass
class Cell:
    value: str
    formula: str | None


def read_workbook(path: Path) -> dict[str, dict[str, Cell]]:
    """返回 {工作表名: {单元格坐标: Cell}}，共享字符串已展开。"""
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        out: dict[str, dict[str, Cell]] = {}
        for sh in wb.find("m:sheets", _NS):  # type: ignore[union-attr]
            target = rels[sh.get(f"{{{_REL}}}id")].lstrip("/")
            target = target if target.startswith("xl/") else f"xl/{target}"
            cells: dict[str, Cell] = {}
            for c in ET.fromstring(z.read(target)).iter(f"{{{_NS['m']}}}c"):
                v = c.find("m:v", _NS)
                f = c.find("m:f", _NS)
                val = v.text if v is not None and v.text is not None else ""
                if c.get("t") == "s" and val:
                    val = shared[int(val)]
                cells[c.get("r")] = Cell(val.strip(), f.text if f is not None and f.text else None)
            out[sh.get("name")] = cells
    return out


# ---------------------------------------------------------------- 解析


_FREIGHT = re.compile(r"^=?\s*(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)\s*\*\s*F\d+\s*(?:\+\s*(\d+(?:\.\d+)?))?\s*$")


def parse_freight(formula: str | None) -> tuple[str, str, str | None] | None:
    """``16+40*F3`` → ("16", "40", None)；``22+55*F4+24`` → ("22", "55", "24")。解析不了返回 None。"""
    if not formula:
        return None
    m = _FREIGHT.match(formula.replace(" ", ""))
    return (m.group(1), m.group(2), m.group(3)) if m else None


def channel_token(name: str) -> str | None:
    """渠道编号：先去掉「100ml」这类容量字样，再按 PK 编号 > 字母编号 > 数字编号取最后一个。"""
    text = re.sub(r"\d+\s*ml", "", name, flags=re.I)
    for pattern in (r"PK\d{3,}", r"[A-Z]{2,}\d*", r"\d{2,}"):
        found = re.findall(pattern, text)
        if found:
            return found[-1]
    return None


def liquid_cap(name: str) -> str | None:
    if re.search(r"不限\s*ml", name, re.I):
        return None
    m = re.search(r"(\d+)\s*ml", name, re.I)
    return m.group(1) if m else None


def attributes_for(sheet: str, name: str) -> list[str]:
    if "带电" in sheet:
        return ["general", "battery"]
    if "化妆品" in sheet:
        return LIQUID_ATTRS
    if any(k in sheet for k in ("特货", "特敏", "特惠")) or "特货" in name or "特敏" in name:
        return LIQUID_ATTRS
    return ["general"]


@dataclass
class Draft:
    site: str
    sheet: str
    row: int
    payload: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    @property
    def scope_key(self) -> str:
        return f"{self.site}:{self.payload['code']}"

    @property
    def source(self) -> str:
        return f"售价计算表--26.8.17(1).xlsx!{self.sheet}!A{self.row}:O{self.row}"


@dataclass
class ImportReport:
    drafts: list[Draft] = field(default_factory=list)
    skipped_sites: dict[str, int] = field(default_factory=dict)
    unparsed: list[str] = field(default_factory=list)


def build_drafts(book: dict[str, dict[str, Cell]]) -> ImportReport:
    report = ImportReport()
    used_codes: dict[str, set[str]] = {}
    for sheet, cells in book.items():
        if sheet == SUMMARY_SHEET:
            continue
        carrier, prefix = next(((c, p) for k, (c, p) in CARRIERS.items() if sheet.startswith(k)), (sheet, "X"))
        rows = sorted({int(re.sub(r"\D", "", k)) for k in cells if k.startswith("A")})
        for row in rows:
            site_name = cells.get(f"A{row}", Cell("", None)).value
            if row < 3 or not site_name or site_name == "站点":
                continue
            site = SITE_BY_NAME.get(site_name)
            if site is None:
                report.skipped_sites[site_name] = report.skipped_sites.get(site_name, 0) + 1
                continue
            name = cells.get(f"B{row}", Cell("", None)).value
            h = cells.get(f"H{row}")
            freight = parse_freight(h.formula if h else None)
            if freight is None:
                report.unparsed.append(f"{sheet}!H{row}（{site_name} {name}）运费公式无法解析")
                continue
            fixed, per_kg, extra = freight
            token = channel_token(name) or ("MP" if "免泡" in sheet else f"R{row}")
            code = f"{prefix}-{token}"
            taken = used_codes.setdefault(site, set())
            if code in taken:
                code = f"{code}-{row}"
            taken.add(code)

            status = cells.get(f"N{row}", Cell("", None)).value
            remark = cells.get(f"O{row}", Cell("", None)).value
            notes = ["允许货物属性按工作表名推断，请核实"]
            if status == "包税":
                duty_mode = "included"
            elif status == "不包税":
                duty_mode = "not_included"
            else:
                duty_mode = "not_included"
                notes.append("原表未注明是否包税，暂按不包税（核价时需填关税税率）")
            if "停发" in status or "不发" in name:
                notes.append(f"原表标注停发（{sheet}!{'N' if '停发' in status else 'B'}{row}），核实前不要启用")
            if remark:
                notes.append(f"原表备注：{remark}")
            if token in KNOWN_CONFLICTS:
                notes.append(KNOWN_CONFLICTS[token])
            notes.append("原表无重量区间与重量上限，按单一区间导入，上限 30 kg 为占位，请按承运商报价改")

            surcharges = []
            if extra:
                surcharges.append({"name": f"原表固定附加 {extra} 元（欧盟线路，性质待核实）", "kind": "fixed",
                                   "value": extra})
                notes.append(f"运费公式含固定 +{extra}（{sheet}!H{row}），与挂号费分列，名称与是否重复计费待核实")
            cap = liquid_cap(name)
            attrs = attributes_for(sheet, name)
            payload: dict[str, Any] = {
                "site": site, "code": code, "name": name, "carrier": carrier, "allowed_attributes": attrs,
                "max_weight_kg": "30",
                "brackets": [{"max_weight_kg": "30", "per_parcel_fee": fixed, "per_kg": per_kg}],
                "surcharges": surcharges, "duty_mode": duty_mode,
                "source_cells": [f"{sheet}!B{row}", f"{sheet}!H{row}"] + (
                    [f"{sheet}!N{row}"] if status else []) + ([f"{sheet}!O{row}"] if remark else []),
            }
            if cap and set(attrs) & {"liquid", "paste"}:
                payload["liquid_cap_ml"] = cap
                payload["liquid_cap_scope"] = "per_bottle"
                notes.append(f"容量上限 {cap}ml 取自渠道名，按单瓶计；计量对象待核实")
            report.drafts.append(Draft(site=site, sheet=sheet, row=row, payload=payload, notes=notes))
    return report


# ---------------------------------------------------------------- 写库


async def _write(report: ImportReport) -> tuple[int, int]:
    from sqlalchemy import select

    from app.table.pricing import rule_service
    from app.table.pricing.pricing_model import PricingRule
    from app.table.pricing.pricing_schemas import RuleCreate
    from database import AsyncSessionFactory

    created = skipped = 0
    async with AsyncSessionFactory() as db:
        for d in report.drafts:
            exists = (await db.execute(select(PricingRule.id).where(
                PricingRule.kind == "fbm_channel", PricingRule.scope_key == d.scope_key,
                PricingRule.source == d.source))).first()
            if exists:
                skipped += 1
                continue
            await rule_service.create_draft(db, None, RuleCreate(  # type: ignore[arg-type]
                kind="fbm_channel", scope_key=d.scope_key, effective_from=EFFECTIVE_FROM, source=d.source,
                review_note="；".join(d.notes), payload=d.payload))
            created += 1
    return created, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("xlsx", type=Path, help="原核价 Excel 路径")
    parser.add_argument("--dry-run", action="store_true", help="只解析与校验，不写库")
    args = parser.parse_args()

    from app.table.pricing.rule_service import validate_payload

    report = build_drafts(read_workbook(args.xlsx))
    for d in report.drafts:
        validate_payload("fbm_channel", d.scope_key, d.payload)
    summary = {
        "drafts": len(report.drafts),
        "by_site": {s: sum(1 for d in report.drafts if d.site == s) for s in ("US", "GB", "DE")},
        "skipped_unregistered_sites": report.skipped_sites,
        "unparsed": report.unparsed,
    }
    if args.dry_run:
        for d in report.drafts:
            print(f"[dry-run] {d.scope_key:<18} {d.payload['name']}  ←  {d.source}")
            for n in d.notes:
                print(f"            · {n}")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    created, skipped = asyncio.run(_write(report))
    summary.update(created=created, already_existed=skipped)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
