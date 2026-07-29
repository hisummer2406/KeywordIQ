"""V2 数据模型的单元测试。

不依赖真实 PostgreSQL——全部基于模型元数据与纯函数。真库层面的验证（分区裁剪、
唯一约束的实际行为、跨月自动建分区）属于 tasks 7.1–7.5，需要 V2 实例。

这里重点守住几个"改错了也不会立刻报错"的地方：
- 事实表的主键必须包含分区键，否则 PostgreSQL 根本不接受建表；
- ``keyword_history`` 索引的列序与主键相反，是 7 天走势查询的唯一依靠，
  被"顺手对齐"成主键列序就会静默变慢；
- 站点码规整必须对未知站点抛错而不是回退默认。
"""

from datetime import date

import pytest

from app.site_registry import (
    SITE_CATALOG,
    UnknownSiteError,
    get_site,
    is_known_site,
    normalize_site_code,
    normalize_site_codes,
)
from app.table.analysis import partitioning
from app.table.analysis.dimension_model import DimKeyword, DimProduct, SiteRef
from app.table.analysis.fact_model import FactDaily, FactWeekly, SiteDataStats
from app.table.upload.import_batch_model import (
    PERIOD_DAY,
    PERIOD_WEEK,
    VALID_PERIOD_TYPES,
    VALID_STATUSES,
    ImportBatch,
)


# --------------------------------------------------------------------------
# 站点清单
# --------------------------------------------------------------------------


class TestSiteRegistry:
    def test_catalog_keys_match_site_codes(self):
        """目录的键必须与 Site.code 一致，否则按键查到的元数据会张冠李戴。"""
        for key, site in SITE_CATALOG.items():
            assert key == site.code

    def test_catalog_codes_are_uppercase_alpha2(self):
        """站点码必须是 ISO 3166-1 alpha-2 大写形式。"""
        for code in SITE_CATALOG:
            assert code.isupper()
            assert len(code) == 2
            assert code.isalpha()

    def test_uses_gb_not_uk(self):
        """英国统一用 GB。亚马逊两种写法都出现过，混用会让同一站点在库里裂成两个。"""
        assert "GB" in SITE_CATALOG
        assert "UK" not in SITE_CATALOG

    @pytest.mark.parametrize("raw,expected", [
        ("us", "US"),
        ("  gb  ", "GB"),
        ("De", "DE"),
        ("US", "US"),
    ])
    def test_normalize_accepts_case_and_whitespace(self, raw, expected):
        assert normalize_site_code(raw) == expected

    @pytest.mark.parametrize("raw", ["XX", "UK", "", "   ", None, "U"])
    def test_normalize_rejects_unknown(self, raw):
        """未知站点必须抛错，绝不静默回退——回退会让数据写到查不到的归属下。"""
        with pytest.raises(UnknownSiteError):
            normalize_site_code(raw)

    def test_is_known_site_does_not_raise(self):
        assert is_known_site("us") is True
        assert is_known_site("XX") is False
        assert is_known_site(None) is False

    def test_normalize_codes_dedupes_and_sorts(self):
        assert normalize_site_codes(["us", "GB", "us", "de"]) == ["DE", "GB", "US"]

    def test_normalize_codes_rejects_any_invalid(self):
        with pytest.raises(UnknownSiteError):
            normalize_site_codes(["US", "XX"])

    def test_get_site_returns_metadata(self):
        site = get_site("gb")
        assert site.code == "GB"
        assert site.domain == "amazon.co.uk"


# --------------------------------------------------------------------------
# 分区
# --------------------------------------------------------------------------


class TestPartitionBounds:
    def test_month_bounds_normal(self):
        assert partitioning.month_bounds(date(2026, 7, 23)) == (date(2026, 7, 1), date(2026, 8, 1))

    def test_month_bounds_crosses_year(self):
        """12 月的下界是次年 1 月——月份加一的朴素写法会在这里崩掉。"""
        assert partitioning.month_bounds(date(2026, 12, 15)) == (date(2026, 12, 1), date(2027, 1, 1))

    def test_month_bounds_on_first_and_last_day(self):
        assert partitioning.month_bounds(date(2026, 2, 1))[0] == date(2026, 2, 1)
        assert partitioning.month_bounds(date(2026, 2, 28))[1] == date(2026, 3, 1)

    def test_bounds_are_half_open(self):
        """RANGE 分区上界是开区间：本月的上界必须等于下月的下界，不能重叠也不能有缝。"""
        july_end = partitioning.month_bounds(date(2026, 7, 10))[1]
        august_start = partitioning.month_bounds(date(2026, 8, 10))[0]
        assert july_end == august_start


class TestPartitionNaming:
    def test_month_partition_name(self):
        assert partitioning.month_partition_name("fact_daily", date(2026, 7, 23)) == "fact_daily_2026_07"

    def test_month_partition_name_pads_month(self):
        assert partitioning.month_partition_name("fact_daily", date(2026, 1, 5)) == "fact_daily_2026_01"

    def test_site_partition_name_is_lowercase(self):
        assert partitioning.site_partition_name("fact_weekly", date(2026, 7, 23), "GB") == \
            "fact_weekly_2026_07_gb"

    def test_site_partition_name_normalizes_input(self):
        """大小写不同的站点码必须落到同一个分区名，否则会建出两个分区。"""
        a = partitioning.site_partition_name("fact_daily", date(2026, 7, 23), "gb")
        b = partitioning.site_partition_name("fact_daily", date(2026, 7, 23), "  GB ")
        assert a == b

    def test_site_partition_name_rejects_unknown_site(self):
        with pytest.raises(UnknownSiteError):
            partitioning.site_partition_name("fact_daily", date(2026, 7, 23), "XX")

    def test_unknown_fact_table_rejected(self):
        """表名白名单是 DDL 拼接的唯一防线——标识符无法参数化。"""
        with pytest.raises(partitioning.UnknownFactTableError):
            partitioning._validate_table("users; DROP TABLE x")

    def test_registered_tables_cover_both_facts(self):
        assert set(partitioning.FACT_TABLE_PARTITION_KEYS) == {"fact_daily", "fact_weekly"}
        assert partitioning.FACT_TABLE_PARTITION_KEYS["fact_daily"] == "report_date"
        assert partitioning.FACT_TABLE_PARTITION_KEYS["fact_weekly"] == "report_date_week"


# --------------------------------------------------------------------------
# 模型结构
# --------------------------------------------------------------------------


class TestFactTableStructure:
    @pytest.mark.parametrize("model,date_column", [
        (FactDaily, "report_date"),
        (FactWeekly, "report_date_week"),
    ])
    def test_primary_key_contains_partition_keys(self, model, date_column):
        """PostgreSQL 要求分区表的主键包含全部分区键（一级 RANGE + 二级 LIST）。

        漏掉任何一个，建表时就会直接失败。
        """
        pk_columns = [c.name for c in model.__table__.primary_key]
        assert date_column in pk_columns, "缺少一级 RANGE 分区键"
        assert "site" in pk_columns, "缺少二级 LIST 分区键"
        assert pk_columns == ["site", date_column, "keyword_id"]

    @pytest.mark.parametrize("model,date_column", [
        (FactDaily, "report_date"),
        (FactWeekly, "report_date_week"),
    ])
    def test_declared_as_range_partitioned(self, model, date_column):
        """分区声明一旦丢失，表会被静默建成普通表——最危险的一类'看起来成功了'。"""
        assert model.__table__.kwargs.get("postgresql_partition_by") == f"RANGE ({date_column})"

    @pytest.mark.parametrize("model,table_name,date_column", [
        (FactDaily, "fact_daily", "report_date"),
        (FactWeekly, "fact_weekly", "report_date_week"),
    ])
    def test_keyword_history_index_column_order(self, model, table_name, date_column):
        """7 天走势索引的列序必须是 (site, keyword_id, 日期)。

        它刻意与主键 (site, 日期, keyword_id) 相反：走势查询按关键词定位后对日期做
        范围扫描，主键的列序服务不了它。把这个索引"顺手"对齐成主键列序，功能不会坏，
        只会静默变慢——所以用测试钉死。
        """
        indexes = {idx.name: [c.name for c in idx.columns] for idx in model.__table__.indexes}
        name = f"idx_{table_name}_keyword_history"
        assert name in indexes, "缺少 7 天走势索引"
        assert indexes[name] == ["site", "keyword_id", date_column]

    @pytest.mark.parametrize("model", [FactDaily, FactWeekly])
    def test_every_index_leads_with_site(self, model):
        """站点过滤要走上索引，site 必须是前导列。"""
        for idx in model.__table__.indexes:
            assert [c.name for c in idx.columns][0] == "site", f"{idx.name} 未以 site 开头"

    @pytest.mark.parametrize("model,table_name", [
        (FactDaily, "fact_daily"),
        (FactWeekly, "fact_weekly"),
    ])
    def test_is_new_partial_index_exists(self, model, table_name):
        """新词筛选是高频功能（V1 实测 5.9 万次），必须有部分索引。

        必须是 partial（带 WHERE is_new），否则会索引全部行、失去"只索引少数新词"
        的体积优势。
        """
        idx = {i.name: i for i in model.__table__.indexes}.get(f"idx_{table_name}_new")
        assert idx is not None, "缺少新词筛选索引"
        where = idx.dialect_options["postgresql"].get("where")
        assert where is not None, "is_new 索引必须是部分索引（带 WHERE），否则失去体积优势"

    @pytest.mark.parametrize("model", [FactDaily, FactWeekly])
    def test_no_speculative_range_partial_indexes(self, model):
        """不建 V1 那种实测 0 命中的份额'范围'部分索引。

        V1 的 idx_shares_range / *_share_range 三个部分索引实测扫描次数为 0，
        是投机建的废索引。份额只保留全量排序索引。
        """
        for idx in model.__table__.indexes:
            name = idx.name or ""
            if "share" in name:
                where = idx.dialect_options["postgresql"].get("where")
                assert where is None, f"{name} 不应是部分索引（V1 同类实测 0 命中）"

    @pytest.mark.parametrize("model", [FactDaily, FactWeekly])
    def test_no_redundant_trend_column(self, model):
        """时序化之后趋势 JSONB 是纯冗余，不得复活。"""
        columns = set(model.__table__.columns.keys())
        assert "ranking_trend_day" not in columns
        assert not any("trend" in name for name in columns)

    @pytest.mark.parametrize("model", [FactDaily, FactWeekly])
    def test_no_foreign_keys(self, model):
        """8 亿行的批量灌入下，每行一次外键检查是纯粹的导入税。"""
        assert not list(model.__table__.foreign_keys)

    @pytest.mark.parametrize("model", [FactDaily, FactWeekly])
    def test_each_fact_owns_its_product_references(self, model):
        """两个周期各自拥有 Top 商品，不共用——V1 正是因为共用而互相覆盖。"""
        columns = model.__table__.columns.keys()
        for rank in (1, 2, 3):
            assert f"top{rank}_product_id" in columns
            assert f"top{rank}_click_share" in columns
            assert f"top{rank}_conversion_share" in columns


class TestDimensionStructure:
    def test_keyword_unique_per_site(self):
        """同一字符串在不同站点是两条独立记录。"""
        constraints = {c.name: sorted(col.name for col in c.columns)
                       for c in DimKeyword.__table__.constraints
                       if hasattr(c, "columns") and c.name}
        assert constraints.get("uq_dim_keyword_site_keyword") == ["keyword", "site"]

    def test_keyword_search_index_is_trigram_on_dimension(self):
        """界面搜索是子串 ILIKE，必须用 trigram（非 tsvector），且建在维度表而非事实表。

        钉死索引类型：tsvector GIN 对 '%x%' 子串匹配无效，用错了搜索会静默退化成
        全表扫描——正是 V1 的老毛病。
        """
        idx = {i.name: i for i in DimKeyword.__table__.indexes}.get("idx_dim_keyword_trgm")
        assert idx is not None, "缺少关键词 trigram 搜索索引"
        assert idx.dialect_options["postgresql"]["using"] == "gin"
        assert idx.dialect_options["postgresql"]["ops"] == {"keyword": "gin_trgm_ops"}
        # 事实表不得背任何关键词搜索索引。
        for model in (FactDaily, FactWeekly):
            assert not any("keyword" in (i.name or "") and "trgm" in (i.name or "")
                           for i in model.__table__.indexes)

    def test_product_unique_by_asin(self):
        constraints = {c.name: sorted(col.name for col in c.columns)
                       for c in DimProduct.__table__.constraints
                       if hasattr(c, "columns") and c.name}
        assert constraints.get("uq_dim_product_asin") == ["asin"]

    def test_all_searchable_product_fields_have_trigram(self):
        """title/brand/category/asin 四个可搜索字段都要有 trigram 索引，全在维度表。"""
        names = {idx.name for idx in DimProduct.__table__.indexes}
        for field in ("title", "brand", "category", "asin"):
            assert f"idx_dim_product_{field}_trgm" in names, f"缺少 {field} 的子串搜索索引"

    def test_site_ref_has_no_foreign_key_from_facts(self):
        """站点参考表刻意不与事实表建外键，合法性由应用层保证。"""
        assert SiteRef.__table__.name == "sites"
        for model in (FactDaily, FactWeekly):
            assert not list(model.__table__.foreign_keys)


class TestStatsAndBatch:
    def test_stats_keyed_by_site_period_date(self):
        """总数与可选日期都按这个粒度取，粒度错了两个功能一起错。"""
        pk = [c.name for c in SiteDataStats.__table__.primary_key]
        assert pk == ["site", "period_type", "report_date"]

    def test_batch_records_full_provenance(self):
        columns = ImportBatch.__table__.columns.keys()
        for required in ("site", "period_type", "report_date"):
            assert required in columns

    def test_batch_uses_single_period_field_not_two_booleans(self):
        """V1 的 is_day_data/is_week_data 能表达出非法状态组合，单字段不能。"""
        columns = ImportBatch.__table__.columns.keys()
        assert "is_day_data" not in columns
        assert "is_week_data" not in columns

    def test_period_and_status_vocabularies(self):
        assert VALID_PERIOD_TYPES == {PERIOD_DAY, PERIOD_WEEK}
        assert "QUEUED" in VALID_STATUSES, "串行导入需要排队状态"


class TestSchemaQualification:
    @pytest.mark.parametrize("model", [
        DimKeyword, DimProduct, SiteRef, FactDaily, FactWeekly, SiteDataStats, ImportBatch,
    ])
    def test_all_tables_are_schema_qualified(self, model):
        """连接串不保证 search_path 指向 analysis，schema 必须显式。"""
        assert model.__table__.schema, f"{model.__tablename__} 缺少 schema 限定"
