"""原核价 Excel 导入为草稿（PRICE-16）。用构造的最小 xlsx 测解析，不依赖业务原表。"""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

from app.table.pricing.rule_service import validate_payload

_spec = importlib.util.spec_from_file_location(
    "pricing_import_excel", Path(__file__).resolve().parent.parent / "scripts" / "pricing_import_excel.py")
imp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = imp  # dataclass 需要按模块名回查
_spec.loader.exec_module(imp)


def _sheet_xml(rows: dict[int, dict[str, tuple[str, str | None]]]) -> str:
    out = []
    for r, cols in rows.items():
        cells = []
        for col, (val, formula) in cols.items():
            f = f"<f>{formula}</f>" if formula else ""
            cells.append(f'<c r="{col}{r}" t="str">{f}<v>{val}</v></c>')
        out.append(f'<row r="{r}">{"".join(cells)}</row>')
    return ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            + "".join(out) + "</sheetData></worksheet>")


def _xlsx(tmp_path: Path, sheets: dict[str, dict]) -> Path:
    path = tmp_path / "t.xlsx"
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' \
         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        entries = "".join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(sheets, 1))
        z.writestr("xl/workbook.xml", f"<workbook {ns}><sheets>{entries}</sheets></workbook>")
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="t" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}'
                   "</Relationships>")
        for i, rows in enumerate(sheets.values(), 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows))
    return path


def test_parse_freight():
    assert imp.parse_freight("16+40*F3") == ("16", "40", None)
    assert imp.parse_freight("=22+55*F4+24") == ("22", "55", "24")
    assert imp.parse_freight("G3+H3") is None
    assert imp.parse_freight(None) is None


def test_channel_token_ignores_capacity():
    assert imp.channel_token("华邮通挂号特货-PK0298（100ml）") == "PK0298"
    assert imp.channel_token("通邮-亚马逊专线特货(100ml)-1327") == "1327"
    assert imp.channel_token("CNE全球经济-CNE8") == "CNE8"
    assert imp.channel_token("菜鸟经济挂号-普货(05)") == "05"
    assert imp.channel_token("义乌云途") is None


def test_build_drafts_from_minimal_workbook(tmp_path):
    header = {"A": ("站点", None), "B": ("渠道", None)}
    path = _xlsx(tmp_path, {
        "云途-普货": {
            2: header,
            3: {"A": ("英国", None), "B": ("云途特惠普货-THPHR", None), "H": ("20", "16+40*F3")},
            4: {"A": ("德国", None), "B": ("云途特惠普货-THPHR", None), "H": ("51.5", "22+55*F4+24")},
            5: {"A": ("法国", None), "B": ("云途特惠普货-THPHR", None), "H": ("53", "23+60*F5+24")},
            8: {"A": ("美国", None), "B": ("云途特惠普货-THPHR", None), "H": ("33.5", "24+95*F8"),
                "N": ("包税", None)},
        },
        "通邮": {
            9: {"A": ("美国", None), "B": ("通邮-美国专线特敏感（不限ml)-1035", None), "H": ("35", "25+100*F9"),
                "N": ("包税", None)},
        },
        "华邮通-挂号特货": {
            8: {"A": ("美国", None), "B": ("华邮通挂号特货-PK0298（100ml）", None), "H": ("37", "25+120*F8"),
                "N": ("停发", None)},
        },
        "渠道特征汇总": {2: {"A": ("云途", None)}},
    })
    report = imp.build_drafts(imp.read_workbook(path))
    assert report.skipped_sites == {"法国": 1}
    by_scope = {d.scope_key: d for d in report.drafts}
    assert set(by_scope) == {"GB:YT-THPHR", "DE:YT-THPHR", "US:YT-THPHR", "US:TY-1035", "US:HYT-PK0298"}

    de = by_scope["DE:YT-THPHR"]
    assert de.payload["brackets"] == [{"max_weight_kg": "30", "per_parcel_fee": "22", "per_kg": "55"}]
    assert de.payload["surcharges"][0]["value"] == "24"  # +24 单列为附加费，不并入挂号费
    assert de.payload["duty_mode"] == "not_included"
    assert any("性质待核实" in n or "待核实" in n for n in de.notes)

    assert by_scope["US:YT-THPHR"].payload["duty_mode"] == "included"
    assert any("1077" in n for n in by_scope["US:TY-1035"].notes)
    assert "liquid_cap_ml" not in by_scope["US:TY-1035"].payload  # 不限 ml
    stopped = by_scope["US:HYT-PK0298"]
    assert any("停发" in n for n in stopped.notes)
    assert stopped.payload["liquid_cap_ml"] == "100"
    assert "云途-普货!H4" in de.payload["source_cells"]
    for d in report.drafts:
        validate_payload("fbm_channel", d.scope_key, d.payload)
