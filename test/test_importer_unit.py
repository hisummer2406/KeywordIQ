"""导入模块的纯单元测试（不依赖数据库）。

覆盖文件名解析、周期校验、数值清洗 SQL 片段、结构校验——这些不需要真库就能验。
"""

from datetime import date

import pytest

from app.table.upload.import_source import LocalFileSource
from app.table.upload.importer import (
    ImportError_,
    _num,
    _validate_period,
    parse_report_filename,
    validate_structure,
)


class TestFilenameParsing:
    @pytest.mark.parametrize("name,site,d", [
        ("US_热门搜索词_简单_Day_2026_07_18.csv", "US", date(2026, 7, 18)),
        ("de_report_2026-07-16.csv", "DE", date(2026, 7, 16)),  # 小写前缀→大写
        ("GB_weekly_2026_01_05.csv", "GB", date(2026, 1, 5)),
    ])
    def test_parses_site_and_date(self, name, site, d):
        assert parse_report_filename(name) == (site, d)

    def test_missing_prefix_and_date(self):
        assert parse_report_filename("random.csv") == (None, None)

    def test_invalid_date_returns_none(self):
        # 13 月，非法日期应返回 None 而非抛错
        assert parse_report_filename("US_2026_13_40.csv")[1] is None


class TestPeriodValidation:
    def test_valid_periods(self):
        assert _validate_period("DAY")[0] == "fact_daily"
        assert _validate_period("WEEK")[0] == "fact_weekly"

    def test_invalid_period_raises(self):
        with pytest.raises(ImportError_):
            _validate_period("MONTH")


class TestNumericCleanSQL:
    def test_num_snippet_shape(self):
        # 只验 SQL 片段包含关键清洗（coerce + 去非数字），真值验证在集成测试
        s = _num("s.c0")
        assert "regexp_replace" in s and "COALESCE" in s and "::numeric" in s


class TestStructureValidation:
    def test_too_few_lines(self, tmp_path):
        p = tmp_path / "x.csv"
        p.write_text("only one line\n", encoding="utf-8")
        with pytest.raises(ImportError_):
            validate_structure(LocalFileSource(str(p)))

    def test_missing_metadata_keyword(self, tmp_path):
        p = tmp_path / "x.csv"
        p.write_text("no meta\nheader\ndata,a,b\n", encoding="utf-8")
        with pytest.raises(ImportError_):
            validate_structure(LocalFileSource(str(p)))

    def test_valid_structure_passes(self, tmp_path):
        p = tmp_path / "x.csv"
        p.write_text("报告范围=US\n搜索频率排名,搜索词,x\n1,kw,brand\n", encoding="utf-8")
        validate_structure(LocalFileSource(str(p)))  # 不抛即通过
