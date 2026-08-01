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
    infer_period,
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


class TestPeriodInference:
    """周期从文件名推断。

    判定必须是**双向显式**的：日报关键词命中才算日报，周报关键词命中才算周报，都不命中
    就是"判定不出"。此前的写法是 `"day" in filename else WEEK`——关键词缺失的日报会被
    静默写进周报事实表，落错表且界面上无从察觉。
    """

    @pytest.mark.parametrize("name", [
        "US_热门搜索词_简单_Day_2026_07_18.csv",
        "us_daily_2026_07_18.csv",
        "US_日报_2026_07_18.csv",
    ])
    def test_infers_day(self, name):
        assert infer_period(name) == "DAY"

    @pytest.mark.parametrize("name", [
        "GB_weekly_2026_01_05.csv",
        "GB_Week_2026_01_05.csv",
        "GB_周报_2026_01_05.csv",
    ])
    def test_infers_week(self, name):
        assert infer_period(name) == "WEEK"

    def test_no_keyword_is_undecidable(self):
        # 关键：不再兜底成 WEEK
        assert infer_period("US_热门搜索词_2026_07_18.csv") is None

    def test_glued_word_does_not_count(self):
        # "Weekday" 里既有 week 也有 day，但都不是独立词，判定不出而非猜一个
        assert infer_period("US_Weekday_2026_07_18.csv") is None

    def test_both_keywords_is_undecidable(self):
        assert infer_period("US_day_and_week_2026_07_18.csv") is None


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
