"""筛选下拉的统计表（``category_stats`` / ``brand_stats``）的重算。

这两张表回答"这个切片里有哪些类目/品牌，各命中多少关键词"，供过滤区的下拉使用。

**按 (站点, 周期, 日期) 分片，与筛选的作用域三元组严格同粒度。** 早前它们是按
(站点, 周期) 聚合的物化视图，每次导入 ``REFRESH ... CONCURRENTLY`` 一遍，两个毛病同源：

1. **数字对不上。** 视图定义里没有 ``report_date``，计数是"整个保留窗口内、前三类目里
   包含该类目的去重关键词数"，而筛选只作用于单个日期切片。实测（US 日报两天）：下拉显示
   ``Electronics`` 21944，实际筛 01-17 得 17497、筛 01-16 得 16609。
2. **刷新与数据总量同阶。** 视图无日期过滤，``EXPLAIN`` 是对每个分区 ``Seq Scan``，
   79.8 万行时刷一次 5.1s，而它在每次导入后都跑。满窗约 2 亿行是 250 倍。

分片之后，重算就是"删掉这个切片、按这个切片重算"，``EXPLAIN`` 确认只扫目标分区
（走 ``idx_fact_*_top*_brand``），代价与切片大小同阶、不随窗口增长。

放在 analysis 域而不是 upload 域：导入是生产环境唯一的写入者，但演示/合成数据脚本
同样要写这两张表。让它们去 import_service 里取一个私有函数，等于把"统计怎么算"复制成
几份，而漏改其中一份的表现是"下拉里的数字对不上"——最难被发现的那类。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

# (统计表, 事实表上的列前缀, 统计表上的 id 列)
_FILTER_STATS: tuple[tuple[str, str, str], ...] = (
    ("category_stats", "category", "category_id"),
    ("brand_stats", "brand", "brand_id"),
)

# period → (事实表, 日期列)
_PERIOD_FACT: dict[str, tuple[str, str]] = {
    "DAY": ("fact_daily", "report_date"),
    "WEEK": ("fact_weekly", "report_date_week"),
}


def rebuild_filter_stats(
    session: Session, *, site: str, period: str, report_date: date, schema: str = "analysis"
) -> None:
    """重算某一个切片的类目与品牌统计。不提交事务——边界由调用方控制。

    三个名次槽位用 ``UNION``（不是 ``UNION ALL``）去重：同一个关键词的前三名里若出现
    重复的品牌/类目，对该项只应计一次——与 ``_apply_filters`` 的"前三任一命中"完全一致。
    口径不一致的表现就是下拉说一个数、筛出来另一个数。

    未知周期码直接 KeyError：静默按日报重算会把周报切片的统计写成空的，而下拉为空在
    界面上看不出是"没数据"还是"算错了"。
    """
    fact, date_col = _PERIOD_FACT[period]
    params = {"site": site, "period": period, "rdate": report_date}

    for table, col_prefix, id_col in _FILTER_STATS:
        session.execute(
            text(
                f"DELETE FROM {schema}.{table} "
                f"WHERE site = :site AND period_type = :period AND report_date = :rdate"
            ),
            params,
        )
        slots = " UNION ".join(
            f"SELECT keyword_id, top{n}_{col_prefix}_id AS did FROM {schema}.{fact} "
            f"WHERE site = :site AND {date_col} = :rdate AND top{n}_{col_prefix}_id IS NOT NULL"
            for n in (1, 2, 3)
        )
        session.execute(
            text(
                f"""
                INSERT INTO {schema}.{table} (site, period_type, report_date, {id_col}, cnt)
                SELECT :site, :period, :rdate, did, count(*)
                  FROM ({slots}) x
                 GROUP BY did
                """
            ),
            params,
        )
