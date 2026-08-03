"""演示/开发数据：一个站点连续 7 天日报 + 对齐周报，关键词跨天全重叠、排名线性漂移。

**仅用于本地演示**——让分析屏的 7 天走势 sparkline 与周排名变化真正出现（合成压测数据在小
规模下关键词跨天不重叠，看不到走势；真实数据每站通常只导了一天）。幂等：重跑先清掉自己造的
demo 数据（keyword 前缀 demo- / asin 前缀 DEMO）。

    DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_v2_dev \\
        PYTHONPATH=. uv run python scripts/seed_demo_data.py --site US
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from config import settings
from database import SessionFactory

SCHEMA = settings.DATABASE_SCHEMA
N_KEYWORDS = 40
DAYS = 7  # 07-06..07-12（同一 ISO 周，Mon..Sun）
DEMO_PRODUCTS = [
    ("DEMO000001", "Wireless Noise Cancelling Headphones", "Sony", "Electronics"),
    ("DEMO000002", "Stainless Steel Air Fryer 5.8QT", "Ninja", "Home & Kitchen"),
    ("DEMO000003", "Ergonomic Office Chair", "Herman Miller", "Furniture"),
    ("DEMO000004", "Yoga Mat Non Slip", "Gaiam", "Sports & Outdoors"),
    ("DEMO000005", "LED Desk Lamp with USB", "BenQ", "Home & Kitchen"),
]


def _clear_demo(db):
    db.execute(text(f"DELETE FROM {SCHEMA}.fact_daily WHERE keyword_id IN "
                    f"(SELECT keyword_id FROM {SCHEMA}.dim_keyword WHERE keyword LIKE 'demo-%')"))
    db.execute(text(f"DELETE FROM {SCHEMA}.fact_weekly WHERE keyword_id IN "
                    f"(SELECT keyword_id FROM {SCHEMA}.dim_keyword WHERE keyword LIKE 'demo-%')"))
    db.execute(text(f"DELETE FROM {SCHEMA}.dim_keyword WHERE keyword LIKE 'demo-%'"))
    db.execute(text(f"DELETE FROM {SCHEMA}.dim_product WHERE asin LIKE 'DEMO%'"))


def main() -> int:
    parser = argparse.ArgumentParser(description="演示数据：连续 7 天日报 + 对齐周报")
    parser.add_argument("--site", default="US")
    parser.add_argument("--end-date", default="2026-07-12", help="最后一天（应为周日，同 ISO 周）")
    args = parser.parse_args()

    site = args.site.upper()
    end = date.fromisoformat(args.end_date)
    days = [end - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]  # 升序 7 天
    week_date = end  # 周报期次落在同 ISO 周

    from app.table.analysis.partitioning import ensure_fact_partitions

    with SessionFactory() as db:
        _clear_demo(db)

        # 演示商品
        product_ids = []
        for asin, title, brand, cat in DEMO_PRODUCTS:
            pid = db.execute(
                text(f"INSERT INTO {SCHEMA}.dim_product(asin,title,brand,category) "
                     f"VALUES (:a,:t,:b,:c) RETURNING product_id"),
                {"a": asin, "t": title, "b": brand, "c": cat},
            ).scalar()
            product_ids.append(pid)

        # 演示关键词
        kw_ids = []
        for i in range(N_KEYWORDS):
            kid = db.execute(
                text(f"INSERT INTO {SCHEMA}.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
                {"s": site, "k": f"demo-keyword-{i:02d}"},
            ).scalar()
            kw_ids.append(kid)

        ensure_fact_partitions(db.connection(), "fact_daily", site, days[0])
        ensure_fact_partitions(db.connection(), "fact_weekly", site, week_date)

        def rank_of(i, d):
            # 线性漂移：trend_i ∈ [-2,2]，随天数移动，夹在 [1,60]
            trend = (i % 5) - 2
            return max(1, min(60, (i + 1) + trend * (d - 3)))

        for i, kid in enumerate(kw_ids):
            pid = product_ids[i % len(product_ids)]
            for d, day in enumerate(days):
                cur = rank_of(i, d)
                prev = rank_of(i, d - 1) if d > 0 else cur
                db.execute(
                    text(f"INSERT INTO {SCHEMA}.fact_daily"
                         f"(site,report_date,keyword_id,current_ranking,previous_ranking,ranking_change,is_new,"
                         f" top1_product_id,top1_click_share,top1_conversion_share) "
                         f"VALUES (:s,:d,:k,:cur,:prev,:ch,:new,:p,:cs,:vs)"),
                    {"s": site, "d": day, "k": kid, "cur": cur, "prev": prev,
                     "ch": cur - prev, "new": (d == 0), "p": pid,
                     "cs": round(50 - i * 0.5, 2), "vs": round(10 - i * 0.1, 2)},
                )
            # 周报：同关键词，week_change 用一个可见的值
            wk_cur = rank_of(i, DAYS - 1)
            db.execute(
                text(f"INSERT INTO {SCHEMA}.fact_weekly"
                     f"(site,report_date_week,keyword_id,current_ranking,previous_ranking,ranking_change,is_new,"
                     f" top1_product_id,top1_click_share,top1_conversion_share) "
                     f"VALUES (:s,:w,:k,:cur,:prev,:ch,false,:p,:cs,:vs)"),
                {"s": site, "w": week_date, "k": kid, "cur": wk_cur,
                 "prev": wk_cur + ((i % 5) - 2) * 3, "ch": -((i % 5) - 2) * 3,
                 "p": product_ids[i % len(product_ids)],
                 "cs": round(50 - i * 0.5, 2), "vs": round(10 - i * 0.1, 2)},
            )

        # site_data_stats + category_stats
        for day in days:
            db.execute(
                text(f"INSERT INTO {SCHEMA}.site_data_stats(site,period_type,report_date,row_count) "
                     f"VALUES (:s,'DAY',:d,:n) "
                     f"ON CONFLICT (site,period_type,report_date) DO UPDATE SET row_count=EXCLUDED.row_count"),
                {"s": site, "d": day, "n": N_KEYWORDS},
            )
        db.execute(
            text(f"INSERT INTO {SCHEMA}.site_data_stats(site,period_type,report_date,row_count) "
                 f"VALUES (:s,'WEEK',:w,:n) "
                 f"ON CONFLICT (site,period_type,report_date) DO UPDATE SET row_count=EXCLUDED.row_count"),
            {"s": site, "w": week_date, "n": N_KEYWORDS},
        )
        db.execute(text(f"REFRESH MATERIALIZED VIEW {SCHEMA}.category_stats"))
        db.commit()

    print(f"演示数据已写入：{site} 日报 {days[0]}..{days[-1]}（{DAYS}天）+ 周报 {week_date}，"
          f"{N_KEYWORDS} 个跨天重叠关键词")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
