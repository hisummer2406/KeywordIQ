"""排除品牌词的预算命中对（#154 阶段二 D4）：``keyword_brand_hit`` 的写入与切片就绪标记。

「关键词把自己前三品牌之一作为完整单词包含」原本在查询时对切片逐行判定（子串 + 正则），US 周报
288 万行切片上单这一步约 14 s。判定只取决于关键词文本与品牌名，两者都不会改，所以按
(关键词, 品牌) 对算一次、永久保存，查询改为按主键反连接。

**判定只有一处定义**：这里复用 ``analysis_crud.brand_in_keyword``，与未就绪切片的逐行判定是
同一个表达式——两边各写一份的话，分叉的表现是同一切片就绪前后排除的词不同，且不报错。

就绪标记 ``site_data_stats.brand_hits_ready`` 回答「这个切片出现的对是否都已判定过」：
- 重写切片的事务里先置假（``invalidate_slice``，由 ``importer`` 在删旧切片时调用）；
- 算完本切片再置真（``rebuild_slice``）。没算完、算失败都只是继续走逐行判定，结果不变。

放在 analysis 域：导入收尾与回填脚本 ``scripts/backfill_brand_hits.py`` 共用。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import exists, select, text, union
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.table.analysis.analysis_crud import brand_in_keyword, fact_and_datecol
from app.table.analysis.dimension_model import DimBrand, DimKeyword, KeywordBrandHit


def invalidate_slice(session: Session, *, site: str, period: str, report_date: date) -> None:
    """切片即将被重写：就绪置假。不提交，须与重写事实同一事务（外部看不到中间态）。"""
    session.execute(
        text("UPDATE analysis.site_data_stats SET brand_hits_ready = false "
             " WHERE site = :site AND period_type = :period AND report_date = :rdate "
             "   AND brand_hits_ready"),
        {"site": site, "period": period, "rdate": report_date},
    )


def rebuild_slice(session: Session, *, site: str, period: str, report_date: date) -> int:
    """判定本切片前三品牌槽位出现的全部 (关键词, 品牌) 对，补进命中表，再把切片置为就绪。

    返回新增的命中对数。不提交。已有的对跳过（``NOT EXISTS`` 预筛 + 主键冲突兜底），重复执行、
    重导、乱序导入都只会补进新的对。切片在 ``site_data_stats`` 不存在时只写命中、不建行。
    """
    fact, date_col = fact_and_datecol(period)
    pairs = union(*(
        select(fact.keyword_id.label("keyword_id"), getattr(fact, f"top{n}_brand_id").label("brand_id"))
        .where(fact.site == site, date_col == report_date,
               getattr(fact, f"top{n}_brand_id").is_not(None))
        for n in (1, 2, 3)
    )).subquery("pairs")
    hits = (
        select(pairs.c.keyword_id, pairs.c.brand_id)
        .join(DimKeyword, DimKeyword.keyword_id == pairs.c.keyword_id)
        .join(DimBrand, DimBrand.brand_id == pairs.c.brand_id)
        .where(
            brand_in_keyword(DimKeyword.keyword, DimBrand.name),
            ~exists().where(
                KeywordBrandHit.keyword_id == pairs.c.keyword_id,
                KeywordBrandHit.brand_id == pairs.c.brand_id,
            ),
        )
    )
    added = session.execute(
        insert(KeywordBrandHit)
        .from_select(["keyword_id", "brand_id"], hits)
        .on_conflict_do_nothing(index_elements=["keyword_id", "brand_id"])
    ).rowcount
    session.execute(
        text("UPDATE analysis.site_data_stats SET brand_hits_ready = true "
             " WHERE site = :site AND period_type = :period AND report_date = :rdate"),
        {"site": site, "period": period, "rdate": report_date},
    )
    return added
