"""演示/开发数据：一个站点连续 7 天日报 + 对齐周报，关键词跨天全重叠、排名线性漂移。

**仅用于本地演示**——让分析屏那几处「只有真实数据形状才看得见」的东西真正出现：

- 7 天走势 sparkline 与周排名变化（合成压测数据在小规模下关键词跨天不重叠，看不到走势；
  真实数据每站通常只导了一天）
- 点击量前三的商品卡牌：三个槽位都填、且**故意留出只有两个 / 只有一个商品的行**，
  好验证空槽位不塌陷
- 商品缩略图：ASIN 用真实存在的商品，否则图床只返回占位像素（见 DEMO_PRODUCTS 的说明）

幂等：重跑先清掉自己造的数据（关键词前缀 demo-，商品按 ASIN 清单且只删无人引用的）。

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

# ASIN 用**真实存在**的商品，不是 DEMO000001 这种占位串。
#
# 原因是缩略图：图床按 ASIN 全局解析，对不存在的 ASIN 返回 HTTP 200 + 1×1 透明像素
# （不是 404）。用合成 ASIN 的话，前三卡牌里永远是占位方块，本地根本验不了图片这条路
# 是否正常——而这正是最容易悄悄坏掉的一环。下列 ASIN 均已实测有真图。
#
# 标题/品牌/类目仍是编的，只要能区分即可；它们不影响本脚本要演示的东西。
DEMO_PRODUCTS = [
    ("B08N5WRWNW", "Echo Dot (4th Gen) Smart Speaker", "Amazon", "Electronics"),
    ("B07FZ8S74R", "Echo Show 5 Smart Display", "Amazon", "Electronics"),
    ("B01LP0U5X0", "Instant Pot Duo 7-in-1", "Instant Pot", "Home & Kitchen"),
    ("B0BSHF7WHW", "Ergonomic Mesh Office Chair", "Sihoo", "Furniture"),
    ("B09B8V1LZ3", "Non Slip Yoga Mat 6mm", "Gaiam", "Sports & Outdoors"),
    ("B07ZPKN6YR", "LED Desk Lamp with USB Port", "TaoTronics", "Home & Kitchen"),
    ("B08C1W5N87", "Stainless Steel Air Fryer 5.8QT", "Ninja", "Home & Kitchen"),
]
DEMO_ASINS = [asin for asin, *_ in DEMO_PRODUCTS]


def _clear_demo(db):
    db.execute(text(f"DELETE FROM {SCHEMA}.fact_daily WHERE keyword_id IN "
                    f"(SELECT keyword_id FROM {SCHEMA}.dim_keyword WHERE keyword LIKE 'demo-%')"))
    db.execute(text(f"DELETE FROM {SCHEMA}.fact_weekly WHERE keyword_id IN "
                    f"(SELECT keyword_id FROM {SCHEMA}.dim_keyword WHERE keyword LIKE 'demo-%')"))
    db.execute(text(f"DELETE FROM {SCHEMA}.dim_keyword WHERE keyword LIKE 'demo-%'"))
    # 商品用真实 ASIN 之后，清理不能再按前缀一刀切——同一个 ASIN 可能来自真实导入的报表，
    # 删掉它会让那些事实行的商品信息凭空消失（没有外键，不会报错，只会静默变空）。
    # 故只删「除本脚本外无人引用」的：demo 事实行在上面已删，此刻仍有引用的必属真实数据。
    db.execute(
        text(f"""
            DELETE FROM {SCHEMA}.dim_product p
             WHERE p.asin = ANY(:asins)
               AND NOT EXISTS (SELECT 1 FROM {SCHEMA}.fact_daily f
                                WHERE p.product_id IN (f.top1_product_id, f.top2_product_id, f.top3_product_id))
               AND NOT EXISTS (SELECT 1 FROM {SCHEMA}.fact_weekly f
                                WHERE p.product_id IN (f.top1_product_id, f.top2_product_id, f.top3_product_id))
        """),
        {"asins": DEMO_ASINS},
    )


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

        def slots_for(i):
            """第 i 个关键词的三个商品槽位与份额。

            **每 5 个关键词里留一个只有两个商品、一个只有一个商品**——空槽位不由后一名
            顶替、剩余卡牌不收拢，是这批字段的核心行为，本地看不到就验不了。
            份额随名次递减，与真实报表一致（第一名远高于后两名）。
            """
            filled = 3 if i % 5 < 3 else (2 if i % 5 == 3 else 1)
            out = []
            for rank in range(3):
                if rank >= filled:
                    out.append((None, 0, 0))
                    continue
                pid = product_ids[(i + rank) % len(product_ids)]
                out.append((
                    pid,
                    round(max(0.5, (50 - i * 0.5) / (rank * 3 + 1)), 2),
                    round(max(0.1, (10 - i * 0.1) / (rank * 3 + 1)), 2),
                ))
            return out

        for i, kid in enumerate(kw_ids):
            slots = slots_for(i)
            slot_params = {
                f"{key}{rank + 1}": value
                for rank, (pid, cs, vs) in enumerate(slots)
                for key, value in (("p", pid), ("cs", cs), ("vs", vs))
            }
            for d, day in enumerate(days):
                cur = rank_of(i, d)
                prev = rank_of(i, d - 1) if d > 0 else cur
                db.execute(
                    text(f"INSERT INTO {SCHEMA}.fact_daily"
                         f"(site,report_date,keyword_id,current_ranking,previous_ranking,ranking_change,is_new,"
                         f" top1_product_id,top1_click_share,top1_conversion_share,"
                         f" top2_product_id,top2_click_share,top2_conversion_share,"
                         f" top3_product_id,top3_click_share,top3_conversion_share) "
                         f"VALUES (:s,:d,:k,:cur,:prev,:ch,:new,"
                         f" :p1,:cs1,:vs1, :p2,:cs2,:vs2, :p3,:cs3,:vs3)"),
                    {"s": site, "d": day, "k": kid, "cur": cur, "prev": prev,
                     "ch": cur - prev, "new": (d == 0), **slot_params},
                )
            # 周报：同关键词，week_change 用一个可见的值
            wk_cur = rank_of(i, DAYS - 1)
            db.execute(
                text(f"INSERT INTO {SCHEMA}.fact_weekly"
                     f"(site,report_date_week,keyword_id,current_ranking,previous_ranking,ranking_change,is_new,"
                     f" top1_product_id,top1_click_share,top1_conversion_share,"
                     f" top2_product_id,top2_click_share,top2_conversion_share,"
                     f" top3_product_id,top3_click_share,top3_conversion_share) "
                     f"VALUES (:s,:w,:k,:cur,:prev,:ch,false,"
                     f" :p1,:cs1,:vs1, :p2,:cs2,:vs2, :p3,:cs3,:vs3)"),
                {"s": site, "w": week_date, "k": kid, "cur": wk_cur,
                 "prev": wk_cur + ((i % 5) - 2) * 3, "ch": -((i % 5) - 2) * 3,
                 **slot_params},
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
