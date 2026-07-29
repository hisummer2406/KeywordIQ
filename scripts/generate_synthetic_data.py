"""可选的容量/压测工具——在接近真实的规模上生成合成数据。

**这不是上线步骤，也不是验证结构正确性的手段。** 分区裁剪、跨月建分区、唯一约束、
执行计划顺序这些结构问题，用几千行小样本就能验（见 tasks 第 7 组）。本脚本只用于一件
事：当有人想在**真实数据量**下测量查询性能与容量时，提供一份分布接近真实的假数据。

绝大多数时候你不需要运行它。默认 ``--scale`` 很小，真要灌到大规模需要显式放大并耗费
数小时磁盘与时间。

**为什么在服务端生成**：大规模数据从 Python 侧逐行推送不现实。数据由 ``generate_series``
在数据库内生成，Python 只负责拼 SQL 与推进进度。

**两处必须保真的分布**，否则测出来的收益是假的：

1. **ASIN 的流行度呈幂律**。热门商品会出现在成千上万个关键词下——这正是星型模型
   把标题提取到维度表后能省下约 550 GB 的原因。若 ASIN 均匀分布，维度表会退化成
   与事实表等长，省不下任何东西。
2. **关键词的日间重叠率高**。今天 Top 30 万里约九成昨天也在。7 天走势查询依赖
   ``(site, keyword_id, 日期)`` 索引做范围扫描，若每天的关键词集合完全不同，
   走势查询会退化成只命中一行，测不出真实代价。

用法::

    # 结构冒烟（几秒，就是 tasks 7.2-7.5 需要的小样本）
    uv run python scripts/generate_synthetic_data.py --days 3 --weeks 2 --scale 0.001

    # 若真要做真实规模的容量/性能压测（数小时、约 200 GB 磁盘，很少需要）
    uv run python scripts/generate_synthetic_data.py --days 365 --weeks 52 --scale 1.0 --yes

.. warning::
   本脚本**只应在测试实例上运行**，不做幂等保护——重复运行会因主键冲突而失败。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta

from sqlalchemy import text

logger = logging.getLogger("synthetic")

# 真实量级（每站点）。--scale 会按比例缩放，便于先跑小规模冒烟。
KEYWORDS_PER_SITE = 5_000_000
DAILY_ROWS_PER_SITE = 300_000
WEEKLY_ROWS_PER_SITE = 3_000_000
PRODUCTS = 3_000_000

# 关键词日间重叠率：今天的 Top N 里有多大比例昨天也在。
DAILY_OVERLAP = 0.9

CATEGORIES = [
    "Home & Kitchen", "Electronics", "Sports & Outdoors", "Beauty", "Toys & Games",
    "Tools & Home Improvement", "Pet Supplies", "Office Products", "Automotive",
    "Health & Household", "Clothing", "Garden & Outdoor", "Baby", "Musical Instruments",
    "Industrial & Scientific", "Appliances", "Arts & Crafts", "Cell Phones",
    "Computers", "Furniture",
]


def _scaled(value: int, scale: float) -> int:
    return max(1, int(value * scale))


def seed_products(conn, count: int) -> None:
    """填充商品维度。

    标题长度刻意做到约 110 字符——它是 V1 宽表里最大的重复来源（单行 650 字节中
    约 390 字节是三个商品标题），标题偏短会让星型模型的收益被严重低估。
    """
    logger.info("生成 dim_product: %s 行", f"{count:,}")
    categories_sql = ", ".join(f"'{c}'" for c in CATEGORIES)
    conn.execute(
        text(f"""
            INSERT INTO analysis.dim_product (asin, title, brand, category)
            SELECT 'B' || lpad(g::text, 9, '0'),
                   'Product ' || md5(g::text) || ' ' || md5((g * 7)::text)
                              || ' ' || md5((g * 13)::text),
                   'Brand ' || (g % 5000),
                   (ARRAY[{categories_sql}])[1 + (g % {len(CATEGORIES)})]
              FROM generate_series(1, :count) g
            ON CONFLICT (asin) DO NOTHING
        """),
        {"count": count},
    )


def seed_keywords(conn, site: str, count: int) -> tuple[int, int]:
    """填充某站点的关键词维度，返回该站点 keyword_id 的 ``[最小, 最大]`` 区间。

    关键词长度约 25 字符，接近真实搜索词的平均长度。
    """
    logger.info("生成 dim_keyword[%s]: %s 行", site, f"{count:,}")
    conn.execute(
        text("""
            INSERT INTO analysis.dim_keyword (site, keyword)
            SELECT :site, substr(md5((g || :site)::text), 1, 12) || ' ' || (g % 100000)
              FROM generate_series(1, :count) g
            ON CONFLICT (site, keyword) DO NOTHING
        """),
        {"site": site, "count": count},
    )
    row = conn.execute(
        text("SELECT min(keyword_id), max(keyword_id) FROM analysis.dim_keyword WHERE site = :site"),
        {"site": site},
    ).one()
    return int(row[0]), int(row[1])


def _fact_insert_sql(table: str, date_column: str) -> str:
    """一期事实数据的生成 SQL。

    ``current_ranking`` 直接取序号 g，即"第 g 名"，与真实报表一致。

    关键词的选取用 ``(g * step + offset) % pool`` 做偏移，让相邻两期的关键词集合
    有可控的重叠——``offset`` 每期只挪动 ``(1 - DAILY_OVERLAP)`` 的量。

    商品的选取用 ``power(random(), 3)`` 把分布压向低位序号，模拟幂律流行度：
    少数热门 ASIN 覆盖大量关键词。
    """
    return f"""
        INSERT INTO analysis.{table} (
            site, {date_column}, keyword_id,
            current_ranking, previous_ranking, ranking_change, is_new,
            top1_product_id, top1_click_share, top1_conversion_share,
            top2_product_id, top2_click_share, top2_conversion_share,
            top3_product_id, top3_click_share, top3_conversion_share
        )
        SELECT :site,
               :report_date,
               :kw_min + ((g * 7919 + :kw_offset) % :kw_pool),
               g,
               GREATEST(1, g + (g % 21) - 10),
               (g % 21) - 10,
               (g % 50) = 0,
               1 + (power(random(), 3) * :product_max)::bigint,
               round((random() * 30)::numeric, 2),
               round((random() * 20)::numeric, 2),
               1 + (power(random(), 3) * :product_max)::bigint,
               round((random() * 15)::numeric, 2),
               round((random() * 10)::numeric, 2),
               1 + (power(random(), 3) * :product_max)::bigint,
               round((random() * 10)::numeric, 2),
               round((random() * 8)::numeric, 2)
          FROM generate_series(1, :rows) g
        ON CONFLICT DO NOTHING
    """


def seed_period(
    conn, table: str, date_column: str, site: str, report_date: date,
    rows: int, kw_min: int, kw_pool: int, kw_offset: int, product_max: int,
) -> None:
    from app.table.analysis.partitioning import ensure_fact_partitions

    ensure_fact_partitions(conn, table, site, report_date)
    conn.execute(
        text(_fact_insert_sql(table, date_column)),
        {
            "site": site, "report_date": report_date, "rows": rows,
            "kw_min": kw_min, "kw_pool": kw_pool, "kw_offset": kw_offset,
            "product_max": product_max,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=365, help="生成多少天的日报数据")
    parser.add_argument("--weeks", type=int, default=52, help="生成多少周的周报数据")
    parser.add_argument("--scale", type=float, default=1.0, help="规模系数，0.01 表示百分之一")
    parser.add_argument("--end-date", type=str, default=None, help="最后一期的日期，默认今天")
    parser.add_argument("--sites", type=str, default=None, help="站点列表，默认取 ENABLED_SITES")
    parser.add_argument("--yes", action="store_true", help="跳过确认提示")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from config import settings
    from database import SessionFactory
    from app.site_registry import normalize_site_codes

    sites = normalize_site_codes(args.sites.split(",")) if args.sites else settings.enabled_site_codes
    end_date = date.fromisoformat(args.end_date) if args.end_date else date.today()

    keywords = _scaled(KEYWORDS_PER_SITE, args.scale)
    daily_rows = _scaled(DAILY_ROWS_PER_SITE, args.scale)
    weekly_rows = _scaled(WEEKLY_ROWS_PER_SITE, args.scale)
    products = _scaled(PRODUCTS, args.scale)

    total = len(sites) * (args.days * daily_rows + args.weeks * weekly_rows)
    logger.info("站点=%s 日报=%s天×%s行 周报=%s周×%s行 商品=%s",
                sites, args.days, f"{daily_rows:,}", args.weeks, f"{weekly_rows:,}", f"{products:,}")
    logger.info("预计写入事实行数: %s", f"{total:,}")

    if not args.yes:
        answer = input("这会向数据库写入大量数据，且不可幂等重跑。确认继续？[y/N] ")
        if answer.strip().lower() != "y":
            logger.info("已取消")
            return 1

    started = time.monotonic()
    daily_shift = max(1, int(daily_rows * (1 - DAILY_OVERLAP)))

    with SessionFactory() as session:
        seed_products(session, products)
        session.commit()

        for site in sites:
            kw_min, kw_max = seed_keywords(session, site, keywords)
            session.commit()
            kw_pool = kw_max - kw_min + 1

            for i in range(args.days):
                report_date = end_date - timedelta(days=args.days - 1 - i)
                seed_period(session, "fact_daily", "report_date", site, report_date,
                            daily_rows, kw_min, kw_pool, i * daily_shift, products)
                session.commit()
                logger.info("fact_daily[%s] %s 完成 (%s/%s)", site, report_date, i + 1, args.days)

            for i in range(args.weeks):
                report_date = end_date - timedelta(weeks=args.weeks - 1 - i)
                seed_period(session, "fact_weekly", "report_date_week", site, report_date,
                            weekly_rows, kw_min, kw_pool, i * daily_shift * 7, products)
                session.commit()
                logger.info("fact_weekly[%s] %s 完成 (%s/%s)", site, report_date, i + 1, args.weeks)

    logger.info("完成，耗时 %.1f 分钟", (time.monotonic() - started) / 60)
    logger.info("接下来：用 EXPLAIN 验证分区裁剪、维度关联是否发生在分页之后、7 天走势耗时")
    return 0


if __name__ == "__main__":
    sys.exit(main())
