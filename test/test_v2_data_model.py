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
from app.table.analysis.dimension_model import DimKeyword, DimProduct
from app.table.analysis.fact_model import (
    FactDaily,
    FactDropped,
    FactGrowth,
    FactWeekly,
    SiteDataStats,
)
from app.table.upload.import_batch_model import (
    PERIOD_DAY,
    PERIOD_WEEK,
    VALID_PERIOD_TYPES,
    VALID_STATUSES,
    ImportBatch,
)
from database import Base

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

    def test_registered_tables_cover_facts_and_derived(self):
        assert set(partitioning.PARTITIONED_TABLES) == {
            "fact_daily", "fact_weekly", "fact_dropped", "fact_growth",
        }
        assert partitioning.PARTITIONED_TABLES["fact_daily"].date_column == "report_date"
        assert partitioning.PARTITIONED_TABLES["fact_weekly"].date_column == "report_date_week"
        assert partitioning.PARTITIONED_TABLES["fact_dropped"].date_column == "anchor_date"
        assert partitioning.PARTITIONED_TABLES["fact_growth"].date_column == "anchor_date"

    def test_only_fact_tables_have_site_subpartition(self):
        """掉榜表只做一级月度 RANGE，不做站点二级分区。

        它是差集而非全量，比事实表小一到两个数量级；二级分区的裁剪收益这个量级上索引
        已经够用，代价却是分区数翻倍（站点数 × 月数）。分区方案写在注册表里而不是散在
        调用点，是为了让"这张表分不分站点"只有一处答案。
        """
        assert partitioning.PARTITIONED_TABLES["fact_daily"].site_subpartition is True
        assert partitioning.PARTITIONED_TABLES["fact_weekly"].site_subpartition is True
        assert partitioning.PARTITIONED_TABLES["fact_dropped"].site_subpartition is False
        # 增长榜同理，且比掉榜还小两个数量级（每期数千行）
        assert partitioning.PARTITIONED_TABLES["fact_growth"].site_subpartition is False

    def test_dropped_partition_name_has_no_site_suffix(self):
        """单级分区的名字里不该出现站点后缀——出现了说明走错了建分区的分支。"""
        assert partitioning.month_partition_name("fact_dropped", date(2026, 7, 23)) == \
            "fact_dropped_2026_07"


# --------------------------------------------------------------------------
# 模型结构
# --------------------------------------------------------------------------


class TestFactDroppedStructure:
    """掉榜表：行窗口内出现过、但锚定期没有数据的关键词。"""

    def test_primary_key_contains_partition_key(self):
        """PostgreSQL 要求分区表的主键包含分区键。这里只有一级 RANGE(anchor_date)。"""
        pk_columns = [c.name for c in FactDropped.__table__.primary_key]
        assert "anchor_date" in pk_columns, "缺少 RANGE 分区键"
        assert pk_columns == ["site", "period_type", "anchor_date", "keyword_id"]

    def test_partitioned_by_anchor_date(self):
        assert FactDropped.__table__.dialect_options["postgresql"]["partition_by"] == \
            "RANGE (anchor_date)"

    def test_carries_last_seen_period_and_ranking(self):
        """掉榜行唯一能带的跨期信息就是这两个字段，且都必须非空。

        它们是"最后一次在榜"的期次与名次——界面上掉榜行除了历史排名与趋势之外，
        全靠这两个数才不是一行空白。
        """
        columns = FactDropped.__table__.columns
        assert columns["last_seen_date"].nullable is False
        assert columns["last_ranking"].nullable is False

    def test_has_index_for_dropped_segment_paging(self):
        """掉榜段按最后在榜排名分页，索引顺序必须就是分页顺序，否则每页都要排序。

        列序 (site, period_type, anchor_date, last_ranking)：前三列是作用域边界、
        每次查询都带；最后一列承担排序。
        """
        index_columns = {
            tuple(c.name for c in idx.columns) for idx in FactDropped.__table__.indexes
        }
        assert ("site", "period_type", "anchor_date", "last_ranking") in index_columns


class TestFactGrowthStructure:
    """持续增长榜：近 N 期名次持续前进且幅度达标的关键词。仅日报。"""

    def test_primary_key_contains_partition_key(self):
        pk_columns = [c.name for c in FactGrowth.__table__.primary_key]
        assert "anchor_date" in pk_columns, "缺少 RANGE 分区键"
        assert pk_columns == ["site", "anchor_date", "keyword_id"]

    def test_partitioned_by_anchor_date(self):
        assert FactGrowth.__table__.dialect_options["postgresql"]["partition_by"] == \
            "RANGE (anchor_date)"

    def test_has_no_period_type_column(self):
        """只有日报有「连续 7 天」这个概念。

        留一列 period_type 等于宣称 WEEK 也会出现在这张表里，而那会让读表的人以为
        「周报的增长榜在哪儿」是个待补的功能，而不是一个刻意不存在的东西。
        """
        assert "period_type" not in FactGrowth.__table__.columns

    def test_carries_both_endpoints_not_just_the_gain(self):
        """只存净增的话，「从 10 万涨到 6 万」与「从 5 万涨到 1 万」在表里一模一样。"""
        columns = FactGrowth.__table__.columns
        for name in ("start_ranking", "end_ranking", "rank_gain", "min_growth_rate"):
            assert columns[name].nullable is False

    def test_has_index_for_board_paging(self):
        """榜单按净增名次分页：作用域二元组做前缀、净增承担排序。

        不写 DESC 是刻意的——btree 可反向扫描，默认的净增降序照样免排序节点，而带方向
        的索引会在 autogenerate 里产生比不掉的差异（集成测试要求 autogenerate 为空）。
        """
        index_columns = {
            tuple(c.name for c in idx.columns) for idx in FactGrowth.__table__.indexes
        }
        assert ("site", "anchor_date", "rank_gain") in index_columns


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

    @pytest.mark.parametrize("model,table_name,date_column", [
        (FactDaily, "fact_daily", "report_date"),
        (FactWeekly, "fact_weekly", "report_date_week"),
    ])
    def test_brand_indexes_are_declared_on_the_model(self, model, table_name, date_column):
        """三个品牌索引必须**声明在模型上**，不能只存在于迁移里。

        它们是在真实数据上实测选定的（US 周报 278.5 万行：无索引 499ms → 187ms）。
        但性能改动一度只写进了迁移，模型里没有——于是库里有、模型里没有，下一次
        ``alembic revision --autogenerate`` 会把它们当成"多余的索引"生成六条 drop_index。
        那个 diff 看起来完全正常，合进去之后品牌筛选静默退回全切片扫描，没有测试会红。

        列序 (site, 日期, 品牌) 一并钉死：品牌放在最后做等值，前两列是每个查询都带的
        作用域边界。
        """
        indexes = {idx.name: [c.name for c in idx.columns] for idx in model.__table__.indexes}
        for n in (1, 2, 3):
            name = f"idx_{table_name}_top{n}_brand"
            assert name in indexes, f"缺少品牌索引 {name}（迁移里有、模型里没有？）"
            assert indexes[name] == ["site", date_column, f"top{n}_brand_id"]

    @pytest.mark.parametrize("model", [FactDaily, FactWeekly])
    def test_no_category_index(self, model):
        """类目**刻意不建索引**：基数只有 122，选择性太低，计划器多半不会选它。

        V1 有过投机建索引、实测 0 命中的先例。品牌建了是因为基数 13.5 万且实测有效，
        两者不对称是结论而非疏漏——所以顺手"补全对称性"的改动要先被这条挡下来。
        """
        for idx in model.__table__.indexes:
            assert "category" not in idx.name, (
                f"{idx.name}：类目索引需要先有实测依据（见 design 决策 4）"
            )

    @pytest.mark.parametrize("model", [FactDaily, FactWeekly])
    def test_fact_rows_have_no_is_new(self, model):
        """首次入榜是 dim_keyword 上的永久记录；事实行不再带会随补历史变陈旧的 is_new，
        也就不该再有 `_new` 部分索引。"""
        assert "is_new" not in model.__table__.c
        assert not [i for i in model.__table__.indexes if i.name.endswith("_new")]

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

    def test_only_title_carries_a_trigram_index(self):
        """商品维度上**只有 title** 做子串搜索，因此只有它需要 trigram。

        曾经是 title/brand/category/asin 四个。品牌与类目已移出商品维度（它们是关键词级
        的排行项，在商品上没有数据来源），改为精确多选走各自的 stats 物化视图。

        asin 是最后移出的一个：它是精确码，筛选改为等值匹配后走 ``uq_dim_product_asin``
        唯一索引即可（issue #49）。这条断言是**反向**的——写成"asin 也要有 trgm"时，
        谁顺手把索引加回来都不会有任何地方报错，而它由每次导入的每一行写入维护，
        在生产上是 409 MB。
        """
        names = {idx.name for idx in DimProduct.__table__.indexes}
        assert "idx_dim_product_title_trgm" in names, "标题是子串搜索字段，必须有 trgm"
        assert "idx_dim_product_asin_trgm" not in names, "asin 是精确码，不该有 trgm 索引"

    def test_facts_declare_no_foreign_keys(self):
        """事实表刻意不建外键：批量灌入时每行一次外键检查是纯粹的导入税，
        而导入是唯一写入路径且已在应用层校验过站点码与维度 id。"""
        for model in (FactDaily, FactWeekly):
            assert not list(model.__table__.foreign_keys)

    def test_no_site_metadata_table(self):
        """站点的中文名/时区/域名只有 ``site_registry`` 一份，不进库。

        曾有一张 ``sites`` 参考表存同样三个字段，从建表到删除没有任何代码写过它，
        库里始终是空的。空表本身不算大事，麻烦的是它是站点元数据第二份事实来源的
        入口——两份副本迟早分叉，``GB``/``UK`` 两种写法正是这么来的。
        """
        assert "sites" not in {t.name for t in Base.metadata.tables.values()}


class TestStatsAndBatch:
    def test_stats_keyed_by_site_period_date(self):
        """总数与可选日期都按这个粒度取，粒度错了两个功能一起错。"""
        pk = [c.name for c in SiteDataStats.__table__.primary_key]
        assert pk == ["site", "period_type", "report_date"]

    def test_stats_carry_dropped_count(self):
        """掉榜行数与在榜行数同粒度预存，分页总数才能保持 O(1)。

        实时数（在事实表上聚合）会让每次翻页都付一次扫描代价，正是这张表当初存在的理由。
        """
        column = SiteDataStats.__table__.columns["dropped_count"]
        assert column.nullable is False

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
        DimKeyword, DimProduct, FactDaily, FactWeekly, SiteDataStats, ImportBatch,
    ])
    def test_all_tables_are_schema_qualified(self, model):
        """连接串不保证 search_path 指向 analysis，schema 必须显式。"""
        assert model.__table__.schema, f"{model.__tablename__} 缺少 schema 限定"


# --------------------------------------------------------------------------
# 品牌与类目是关键词级维度，不是商品属性
#
# 源报表里「点击量第 1/2/3 的品牌」与「点击量最高/第二/第 3 的分类」是三份彼此独立的
# 排行，商品槽位本身只有 ASIN / 标题 / 点击份额 / 转化贡献占比四个字段。
# 把品牌#N 配给商品#N 会造出一个没有来源的属性：实测同一 ASIN 固定在槽位 1 时，
# 标题冲突率 0%（真属性），品牌 11.7%、类目 3.4%（伪造的）。
# --------------------------------------------------------------------------


def test_dim_product_has_no_brand_or_category():
    """商品维度收敛为 ASIN + 标题。

    留着这两列会让后来者以为「商品有品牌」，然后基于一个任选值胜出的字段做筛选。
    """
    columns = set(DimProduct.__table__.columns.keys())

    assert "brand" not in columns
    assert "category" not in columns
    assert {"asin", "title"} <= columns


def test_dim_product_keeps_no_brand_or_category_index():
    """两个 trgm 索引随列一起消失，否则迁移会在建索引时引用不存在的列。"""
    index_names = {i.name for i in DimProduct.__table__.indexes}

    assert "idx_dim_product_brand_trgm" not in index_names
    assert "idx_dim_product_category_trgm" not in index_names
    assert "idx_dim_product_title_trgm" in index_names  # 标题是真属性，保留


def test_brand_and_category_dimensions_exist():
    from app.table.analysis.dimension_model import DimBrand, DimCategory

    assert "name" in DimBrand.__table__.columns
    assert "name" in DimCategory.__table__.columns


def test_dimension_key_types_follow_cardinality():
    """按基数选类型：品牌 13.5 万/站/天用 integer，类目 104 用 smallint。

    两者都用 integer 不会错，但事实表上共 6 列 × 2 亿行，类目那 3 列白占一半空间。
    """
    from sqlalchemy import Integer, SmallInteger

    from app.table.analysis.dimension_model import DimBrand, DimCategory

    assert isinstance(DimBrand.__table__.c.brand_id.type, Integer)
    assert isinstance(DimCategory.__table__.c.category_id.type, SmallInteger)


@pytest.mark.parametrize("fact", [FactDaily, FactWeekly])
def test_fact_tables_carry_keyword_level_brand_and_category(fact):
    """两张事实表各带前三品牌与前三类目的引用列，名次由列位置承载。"""
    columns = set(fact.__table__.columns.keys())
    expected = {f"top{n}_{kind}_id" for n in (1, 2, 3) for kind in ("brand", "category")}

    assert expected <= columns, f"缺少 {sorted(expected - columns)}"


@pytest.mark.parametrize("fact", [FactDaily, FactWeekly])
def test_new_fact_columns_are_nullable_without_default(fact):
    """必须可空且无默认值。

    带 DEFAULT 的 ADD COLUMN 会让 PostgreSQL 重写整张表——2 亿行的分区表上这是灾难；
    无默认值时它是纯元数据操作。可空也是历史分区的必然要求：那些数据没有这六个字段，
    只能靠按批次重导补上。
    """
    for n in (1, 2, 3):
        for kind in ("brand", "category"):
            col = fact.__table__.c[f"top{n}_{kind}_id"]
            assert col.nullable, f"{col.name} 必须可空"
            assert col.default is None and col.server_default is None, (
                f"{col.name} 不能有默认值，否则 ADD COLUMN 会重写整张分区表"
            )
