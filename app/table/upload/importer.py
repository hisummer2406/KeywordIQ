"""V2 三段式导入核心。

    第一段 COPY   原始 CSV → UNLOGGED 无索引暂存表
    第二段 维度   从暂存表补齐 dim_keyword / dim_product
    第三段 事实   暂存表 JOIN 维度 + 关联上一期算派生值 → 追加写目标分区

日报与周报共用这一份代码，仅由 ``period`` 参数切换目标事实表与"上一期"的定义
（前一天 vs 前一周）。这是分表 + 时序化换来的最大简化，取代了 V1 两条各约 90 行、
彼此漂移的 UPSERT SQL。

本模块的函数**不提交事务**，事务边界由调用方（service 层）控制。COPY 需要 psycopg2
的原始游标，因此只支持同步 Session。

CSV 列布局（固定 21 列，按位置映射，沿用 V1——报表无稳定的列名，只能靠位置）：

    0 排名  1 搜索词
    2 品牌#1 3 品牌#2 4 品牌#3   5 类目#1 6 类目#2 7 类目#3
    8 ASIN#1 9 标题#1 10 点击份额#1 11 转化份额#1
    12 ASIN#2 13 标题#2 14 点击份额#2 15 转化份额#2
    16 ASIN#3 17 标题#3 18 点击份额#3 19 转化份额#3
    20 报告日期
"""

from __future__ import annotations

import csv
import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.site_registry import normalize_site_code
from app.table.analysis.partitioning import ensure_fact_partitions
from app.table.upload.import_source import ImportSource
from config import settings

logger = logging.getLogger(__name__)

CSV_COLUMN_COUNT = 21
# 元数据行 + 表头行，COPY 前跳过。
_HEADER_LINES = 2
_STAGING_TABLE = "analysis._import_staging"

# period → (事实表, 日期列, 上一期偏移天数, 行窗口回溯天数)
#
# 行窗口决定掉榜集合的范围：天视图是锚定日往前 7 天 [d-6, d]，周视图是锚定周往前 4 周
# [w-3, w]。这里存的是**回溯天数**（不含锚定期本身）：天 6 天、周 21 天。
# 它与"上一期偏移"是两回事——后者只回看一期用来算排名变化，前者圈定整个行集合。
_PERIOD_CONFIG: dict[str, tuple[str, str, int, int]] = {
    "DAY": ("fact_daily", "report_date", 1, 6),
    "WEEK": ("fact_weekly", "report_date_week", 7, 21),
}


class ImportError_(Exception):
    """导入过程中的可预期错误（结构不符、周期非法等）。"""


def validate_structure(source: ImportSource) -> None:
    """校验 CSV 三段式结构（元数据行 + 表头行 + 数据行），不符则抛 ImportError_。

    沿用 V1 靠中文关键字判断的逻辑——报表无稳定 schema，只能这样认。这是 COPY 前
    唯一的合法性关卡：结构不符若不拦，会被当成 0 行"成功"导入，掩盖真正的问题。

    来源经 :class:`ImportSource` 抽象——本地文件或 OSS 对象皆可，只读前三行即关闭
    （对 OSS 是一次早停的流式读取，代价极低）。
    """
    with source.open_text() as f:
        first = f.readline()
        second = f.readline()
        third = f.readline()
    if not (first and second and third):
        raise ImportError_("文件行数不足，至少需要 3 行（元数据、表头、数据）")
    if "报告范围" not in first:
        raise ImportError_("第一行应包含报告范围元数据")
    if "搜索频率排名" not in second or "搜索词" not in second:
        raise ImportError_("第二行应为表头，需包含'搜索频率排名'和'搜索词'")
    if len(third.strip().split(",")) < 3:
        raise ImportError_("第三行应包含实际数据")


@dataclass
class ImportResult:
    site: str
    period: str
    report_date: date
    total_data_rows: int   # 数据区总行数（不含元数据/表头）
    staged_rows: int       # 通过清洗、进入暂存表的行数
    skipped_rows: int      # 因列数不符或搜索词为空被跳过的行数
    written_rows: int      # 实际写入事实表的行数
    dropped_rows: int = 0  # 本期掉榜词数（窗口内出现过但本期无数据）
    growth_rows: int = 0   # 本期持续增长词数（仅日报；周报恒为 0，它没有「连续 7 天」）
    date_warning: str | None = None  # 文件名日期与 CSV 内日期不一致时的提示


# 文件名形如 US_热门搜索词_简单_Day_2026_07_18.csv
_FILENAME_SITE_RE = re.compile(r"^([A-Za-z]{2})[_-]")
_FILENAME_DATE_RE = re.compile(r"(\d{4})[_-](\d{2})[_-](\d{2})")


def parse_report_filename(filename: str) -> tuple[str | None, date | None]:
    """从文件名解析出站点前缀与报告日期。任一解析不出返回 None（由调用方决定如何处理）。

    站点前缀返回大写；日期用 V1 同款正则。**report_date 只来自文件名**，是分区归属的
    唯一依据，因此调用方应做好日期缺失/冲突的处理。
    """
    site = None
    m = _FILENAME_SITE_RE.match(filename)
    if m:
        site = m.group(1).upper()
    report_date = None
    m = _FILENAME_DATE_RE.search(filename)
    if m:
        y, mo, d = m.groups()
        try:
            report_date = date(int(y), int(mo), int(d))
        except ValueError:
            report_date = None
    return site, report_date


_FILENAME_DAY_RE = re.compile(r"(?<![a-z])(day|daily)(?![a-z])|日报", re.IGNORECASE)
_FILENAME_WEEK_RE = re.compile(r"(?<![a-z])(week|weekly)(?![a-z])|周报", re.IGNORECASE)


def infer_period(filename: str) -> str | None:
    """从文件名推断周期，判定不出返回 ``None``（由调用方要求用户显式指定）。

    判定是**双向显式**的：日报关键词命中才是 DAY，周报关键词命中才是 WEEK。此前的写法是
    ``"day" in filename else "WEEK"``——关键词缺失的日报会被静默当成周报，写进 fact_weekly，
    落错事实表且界面上无从察觉。宁可要求用户选一次，也不猜。

    两侧同时命中同样返回 ``None``：文件名自相矛盾时任选一边都是在赌。
    ``(?<![a-z])``/``(?![a-z])`` 只挡字母，故 ``Day_2026`` 能命中而 ``Weekday`` 两侧皆不命中。
    """
    is_day = bool(_FILENAME_DAY_RE.search(filename))
    is_week = bool(_FILENAME_WEEK_RE.search(filename))
    if is_day == is_week:  # 都不命中，或自相矛盾
        return None
    return "DAY" if is_day else "WEEK"


def _num(col: str) -> str:
    """把一个 text 暂存列清洗成 numeric 的 SQL 片段。

    脏值 → 0（沿用 V1 的 coerce 容错，否则报表里任一脏数字会让整批失败）；
    "10.0" 这类也能正确解析。只保留数字、小数点、负号。
    """
    return f"COALESCE(NULLIF(regexp_replace({col}, '[^0-9.\\-]', '', 'g'), '')::numeric, 0)"


def _validate_period(period: str) -> tuple[str, str, int, int]:
    if period not in _PERIOD_CONFIG:
        raise ImportError_(f"period 只支持 DAY 或 WEEK，收到 {period!r}")
    return _PERIOD_CONFIG[period]


def _raw_cursor(session: Session):
    """取底层 psycopg2 游标用于 COPY。"""
    return session.connection().connection.cursor()


def _stage_csv(session: Session, source: ImportSource) -> tuple[int, int, int]:
    """第一段：清洗并 COPY 原始 CSV 到 UNLOGGED 暂存表。

    用 Python csv 解析（引号安全，标题里的逗号不会误分列），逐行流式处理：
    - 跳过列数 < 21 的坏行，并计数（V1 是 on_bad_lines='skip' 静默吞掉，这里改为可观测）
    - 跳过搜索词为空的行
    - 好行写入临时文件，再整体 COPY（比逐行 INSERT 快一个量级，且内存有界）

    来源经 :class:`ImportSource` 抽象：对 OSS 对象是从头流式读取，不把整个文件下载落地
    （清洗后的临时文件只含通过校验的行，仍受"逐行流式"约束，内存有界）。

    返回 (数据区总行数, 入库行数, 跳过行数)。
    """
    cols = ", ".join(f"c{i} text" for i in range(CSV_COLUMN_COUNT))
    session.execute(text(f"DROP TABLE IF EXISTS {_STAGING_TABLE}"))
    session.execute(text(f"CREATE UNLOGGED TABLE {_STAGING_TABLE} ({cols})"))

    total = staged = skipped = 0
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=True, newline="", encoding="utf-8"
    ) as clean, source.open_text() as src:
        for _ in range(_HEADER_LINES):
            src.readline()
        reader = csv.reader(src)
        writer = csv.writer(clean)
        for row in reader:
            total += 1
            if len(row) < CSV_COLUMN_COUNT:
                skipped += 1
                continue
            if not (row[1] or "").strip():  # 搜索词为空
                skipped += 1
                continue
            writer.writerow(row[:CSV_COLUMN_COUNT])
            staged += 1
        clean.flush()

        if staged:
            cur = _raw_cursor(session)
            with open(clean.name, encoding="utf-8-sig", newline="") as f:
                cur.copy_expert(
                    f"COPY {_STAGING_TABLE} FROM STDIN WITH (FORMAT csv)", f
                )

    logger.info("暂存完成: 总行 %d, 入库 %d, 跳过 %d", total, staged, skipped)
    return total, staged, skipped


def _upsert_dimensions(session: Session, site: str) -> None:
    """第二段：从暂存表补齐维度，维度先于事实。"""
    # dim_keyword：本站点的去重关键词。
    session.execute(
        text(
            """
            INSERT INTO analysis.dim_keyword (site, keyword)
            SELECT DISTINCT :site, c1
              FROM analysis._import_staging
             WHERE NULLIF(c1, '') IS NOT NULL
            ON CONFLICT (site, keyword) DO NOTHING
            """
        ),
        {"site": site},
    )
    # dim_product：三个商品槽位合并去重。SCD Type 1 —— 冲突时更新为最新值。
    # DISTINCT ON (asin) 避免同一 asin 在一条 INSERT 里出现两次触发 ON CONFLICT 报错。
    #
    # **只写 asin 与 title。** 品牌与类目曾经也写在这里（品牌#N 配商品#N），那是本变更
    # 修掉的缺陷：商品槽位在源报表里根本没有这两个字段，配对出来的值没有数据来源。
    # 实测同一 ASIN 固定在槽位 1 时，title 冲突率 0%（真属性），brand 11.7%、
    # category 3.4%——后两者的冲突正是"把别的东西当成商品属性"的直接证据。
    #
    # **`WHERE ... IS DISTINCT FROM` 不是冗余，删掉它导入会随维度表增长持续变慢。**
    # PostgreSQL 的 UPDATE 无论新旧值是否相同都写新元组、标死旧元组、并在该行涉及的每个
    # 索引上插新条目——而 title 上正挂着这张表最大的索引（GIN trgm，生产上 1.3 GB）。
    # 无条件更新意味着每次导入重写**所有已存在的商品行**：一份日报约 40 万行 × 3 个槽位
    # ≈ 120 万个 ASIN 提及，其中绝大多数早已在库里。
    #
    # 它还会自我加速恶化：维度表越大，新导入里"已存在"的比例越高，白做的更新越多，
    # 承受这些更新的索引也越大。实测同一份 10 万行日报从 54 秒退化到 416 秒（7.7 倍），
    # 现场证据是 n_tup_upd 达到 n_tup_ins 的 6.4 倍（issue #33）。
    #
    # **用 `IS DISTINCT FROM` 而非 `<>`**：title 现为 NOT NULL，两者等价；但 `<>` 在任一侧
    # 为 NULL 时结果是 NULL（不成立），一旦将来放宽可空性就会静默退化成"NULL 的行永远不
    # 更新"。`IS DISTINCT FROM` 对 NULL 有定义，不留这个坑。
    #
    # 标题**确实变了**的商品照常更新（SCD Type 1 语义不变），回归见
    # test_importer_integration.py 的 test_changed_title_still_updates_the_product。
    session.execute(
        text(
            """
            INSERT INTO analysis.dim_product (asin, title)
            SELECT DISTINCT ON (asin) asin, title
              FROM (
                    -- COPY CSV 把空字段当 NULL，但 title NOT NULL，故 COALESCE 回空串
                    -- （沿用 V1 的 fillna('')）。asin 空则整槽位无商品，由 WHERE 过滤掉。
                    SELECT NULLIF(c8,'')  AS asin, COALESCE(c9,'')  AS title FROM analysis._import_staging
                    UNION ALL
                    SELECT NULLIF(c12,'') AS asin, COALESCE(c13,'') AS title FROM analysis._import_staging
                    UNION ALL
                    SELECT NULLIF(c16,'') AS asin, COALESCE(c17,'') AS title FROM analysis._import_staging
                   ) slots
             WHERE asin IS NOT NULL
             ORDER BY asin
            ON CONFLICT (asin) DO UPDATE
               SET title = EXCLUDED.title
             WHERE analysis.dim_product.title IS DISTINCT FROM EXCLUDED.title
            """
        )
    )
    # dim_brand / dim_category：关键词级排行项，三个名次列合并去重。
    #
    # 与 dim_product 不同，这里**没有** ON CONFLICT DO UPDATE——维度只存名称，没有可更新
    # 的属性。空串在此被过滤掉（NULLIF）：让空串进维度会产生一个"名字是空的品牌"，
    # 然后堂而皇之出现在下拉选项里。
    #
    # **NOT EXISTS 预筛不是 ON CONFLICT 的冗余，它是本段的要点。**
    # PostgreSQL 对 `INSERT ... ON CONFLICT DO NOTHING` 是先取默认值（即 nextval）再判冲突，
    # 所以哪怕一行都没插进去，序列照样按候选行数前进。实测：连插两遍同样的 100 个名字，
    # 表里始终 100 行，序列却从 100 走到 200。
    #
    # 这对 dim_category 是致命的——它的主键是 smallserial，上限 32767（选 smallint 是为了
    # 省事实表上 3 列 × 2 亿行的空间，见 dimension_model.DimCategory）。按每次导入约 120 个
    # 类目、3 个站点每天各一次日报算，约 78 天就会耗尽，此后每次导入直接
    # `ERROR: nextval: reached maximum value of sequence`；rebuild_brand_category 全量重导
    # 一轮（约 300 个批次）更是一次就能烧掉三万多个值。
    #
    # 加了预筛之后，nextval 只对**真正的新名称**发生，与导入次数脱钩。
    # ON CONFLICT 仍保留作兜底：导入虽由咨询锁串行，但兜底的代价只是极罕见的一次跳号。
    for table, cols in (("dim_brand", ("c2", "c3", "c4")), ("dim_category", ("c5", "c6", "c7"))):
        union = " UNION ".join(
            f"SELECT NULLIF({c}, '') AS name FROM analysis._import_staging" for c in cols
        )
        session.execute(
            text(
                f"""
                INSERT INTO analysis.{table} (name)
                SELECT name FROM ({union}) vals
                 WHERE name IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM analysis.{table} d WHERE d.name = vals.name
                   )
                ON CONFLICT (name) DO NOTHING
                """
            )
        )


def _load_facts(session: Session, site: str, period: str, report_date: date) -> int:
    """第三段：关联上一期算派生值，覆盖式追加写事实分区。返回写入行数。"""
    table, date_col, prev_offset, _ = _validate_period(period)
    prev_date = report_date - timedelta(days=prev_offset)

    # 覆盖语义：同一期重复导入时先删该切片（事务内，外部观察不到中间态）。
    session.execute(
        text(f"DELETE FROM analysis.{table} WHERE site = :site AND {date_col} = :rdate"),
        {"site": site, "rdate": report_date},
    )

    # 派生值一次算出：
    # - previous_ranking = 上一期同 (site,keyword) 的 current_ranking，无则 0
    # - ranking_change   = 本期 - 上期，无上期则 0
    # - is_new           = 上一期不存在该关键词
    # DISTINCT ON (keyword_id) 防止暂存表里偶发的重复关键词撞唯一约束。
    cur = _num("s.c0") + "::int"
    result = session.execute(
        text(
            f"""
            INSERT INTO analysis.{table} (
                site, {date_col}, keyword_id,
                current_ranking, previous_ranking, ranking_change, is_new,
                top1_product_id, top1_click_share, top1_conversion_share,
                top2_product_id, top2_click_share, top2_conversion_share,
                top3_product_id, top3_click_share, top3_conversion_share,
                top1_brand_id, top2_brand_id, top3_brand_id,
                top1_category_id, top2_category_id, top3_category_id
            )
            SELECT DISTINCT ON (k.keyword_id)
                :site, :rdate, k.keyword_id,
                {cur},
                COALESCE(prev.current_ranking, 0),
                CASE WHEN prev.keyword_id IS NULL THEN 0 ELSE {cur} - prev.current_ranking END,
                prev.keyword_id IS NULL,
                p1.product_id, {_num('s.c10')}, {_num('s.c11')},
                p2.product_id, {_num('s.c14')}, {_num('s.c15')},
                p3.product_id, {_num('s.c18')}, {_num('s.c19')},
                b1.brand_id, b2.brand_id, b3.brand_id,
                g1.category_id, g2.category_id, g3.category_id
            FROM analysis._import_staging s
            JOIN analysis.dim_keyword k
              ON k.site = :site AND k.keyword = s.c1
            LEFT JOIN analysis.dim_product p1 ON p1.asin = NULLIF(s.c8, '')
            LEFT JOIN analysis.dim_product p2 ON p2.asin = NULLIF(s.c12, '')
            LEFT JOIN analysis.dim_product p3 ON p3.asin = NULLIF(s.c16, '')
            -- 品牌/类目按名次各自关联，与商品槽位无关：某个名次缺失时该列为 NULL，
            -- 不顺延、也不指向一个空串维度行。
            LEFT JOIN analysis.dim_brand b1 ON b1.name = NULLIF(s.c2, '')
            LEFT JOIN analysis.dim_brand b2 ON b2.name = NULLIF(s.c3, '')
            LEFT JOIN analysis.dim_brand b3 ON b3.name = NULLIF(s.c4, '')
            LEFT JOIN analysis.dim_category g1 ON g1.name = NULLIF(s.c5, '')
            LEFT JOIN analysis.dim_category g2 ON g2.name = NULLIF(s.c6, '')
            LEFT JOIN analysis.dim_category g3 ON g3.name = NULLIF(s.c7, '')
            LEFT JOIN analysis.{table} prev
              ON prev.site = :site AND prev.{date_col} = :prev_date AND prev.keyword_id = k.keyword_id
            WHERE NULLIF(s.c1, '') IS NOT NULL
            ORDER BY k.keyword_id, s.ctid
            """
        ),
        {"site": site, "rdate": report_date, "prev_date": prev_date},
    )
    return result.rowcount


def _load_dropped(session: Session, site: str, period: str, report_date: date) -> int:
    """第四段：算定本期的掉榜集合。返回写入行数。

    掉榜 = **行窗口内出现过、但锚定期没有事实数据**的关键词。窗口按周期取（天 7 天、
    周 4 周），长度存在 :data:`_PERIOD_CONFIG` 里。

    **为什么在导入侧算。** 这个集合放到查询侧实时算的话，是一次在 180 万行（天）/
    900 万行（周）上的反连接，而且**每翻一页重跑一次**。掉榜是锚定期的一项事实，
    锚定期一旦确定就不再变化，因此与排名变化、上期排名、是否新词一样在写入时算定。

    ``DISTINCT ON (keyword_id) ... ORDER BY keyword_id, <日期> DESC`` 取每个词在窗口内
    **最近**一次在榜的期次与名次——取最早那次会让「最后在榜」这个字段名变成谎话。

    整条是库内 ``INSERT ... SELECT``，不把任何行读进应用进程；库内排序超出 ``work_mem``
    时外部排序落盘，内存有界。
    """
    table, date_col, _, window_back = _validate_period(period)
    window_start = report_date - timedelta(days=window_back)

    # 覆盖语义：与事实表一致，同一期重复导入时先删该期再重算。
    session.execute(
        text(
            "DELETE FROM analysis.fact_dropped "
            " WHERE site = :site AND period_type = :period AND anchor_date = :anchor"
        ),
        {"site": site, "period": period, "anchor": report_date},
    )

    result = session.execute(
        text(
            f"""
            INSERT INTO analysis.fact_dropped
                (site, period_type, anchor_date, keyword_id, last_seen_date, last_ranking)
            SELECT DISTINCT ON (f.keyword_id)
                   :site, :period, :anchor, f.keyword_id, f.{date_col}, f.current_ranking
              FROM analysis.{table} f
             WHERE f.site = :site
               AND f.{date_col} >= :win_start
               AND f.{date_col} < :anchor
               AND NOT EXISTS (
                     SELECT 1 FROM analysis.{table} c
                      WHERE c.site = :site
                        AND c.{date_col} = :anchor
                        AND c.keyword_id = f.keyword_id
                   )
             ORDER BY f.keyword_id, f.{date_col} DESC
            """
        ),
        {"site": site, "period": period, "anchor": report_date, "win_start": window_start},
    )
    logger.info(
        "掉榜集合算定: %s %s %s，窗口 [%s, %s)，%d 行",
        site, period, report_date, window_start, report_date, result.rowcount,
    )
    return result.rowcount


def load_growth(session: Session, site: str, anchor_date: date) -> int:
    """第五段：算定本期的持续增长榜。返回写入行数。**只适用于日报。**

    命中口径（三条同时满足，窗口是 ``[d-(N-1), d]`` 共 N 期，N = ``GROWTH_WINDOW_DAYS``）：

    1. 窗口内每一期都在榜；
    2. 累计净增 ``rank(首期) − rank(锚定期)`` > ``GROWTH_MIN_RANK_GAIN``；
    3. 窗口内每一个日环比 ``(前期 − 本期) / 前期`` ≥ ``GROWTH_MIN_DAILY_RATE``。

    N 期数据产生 **N−1** 个日环比。要凑满 N 个环比就得取 N+1 期，那与掉榜的 7 天窗口
    对不齐，且多依赖一天的数据——需求方确认按 N−1 个环比判定。

    **``days_on = N`` 一条同时兑现两件事**，因此不必去查 ``fact_dropped``：

    - *中途掉榜自动出局*——掉的那期没有行，凑不满 N；
    - *窗口内缺期时整期榜单为空*——所有词都凑不满 N。这是据实反映而非缺陷，缺期由查询侧
      回传、界面明示（否则用户会把它读成「今天没有增长词」）。

    ``current_ranking > 0`` 不是可有可无的防御：排名来自 CSV 且经 ``_num`` 清洗，脏值
    落库为 0，而 0 会让日环比的分母为零直接炸掉整个导入。排除它同时也让「这一期不算在榜」
    成立——排名为 0 的行本就不是一条有效榜单记录。

    与 :func:`_load_dropped` 一样是整条库内 ``INSERT ... SELECT``，不把任何行读进应用
    进程；窗口排序键 ``(keyword_id, report_date)`` 与
    ``idx_fact_daily_keyword_history (site, keyword_id, report_date)`` 列序一致，可走索引序，
    回落到外部排序时 PostgreSQL 落盘处理，内存有界。

    **公开而非下划线私有**：回填脚本 ``scripts/backfill_growth.py`` 要调同一份实现——
    口径只能有一处定义，抄一份到脚本里迟早与导入侧漂移，而漂移的表现是「回填出来的榜单
    和每日导入出来的不一样」，没有任何地方会报错。
    """
    window_days = settings.GROWTH_WINDOW_DAYS
    win_start = anchor_date - timedelta(days=window_days - 1)

    # 覆盖语义：与事实表一致，同一期重复导入时先删该期再重算。
    session.execute(
        text("DELETE FROM analysis.fact_growth WHERE site = :site AND anchor_date = :anchor"),
        {"site": site, "anchor": anchor_date},
    )

    # 锚定期排名与最低日环比在 SELECT / HAVING 里各出现两次，抽成常量免得两处写法漂移。
    end_rank = "max(current_ranking) FILTER (WHERE report_date = :anchor)"
    min_rate = (
        "min((prev_ranking - current_ranking)::numeric / prev_ranking) "
        "FILTER (WHERE prev_ranking IS NOT NULL)"
    )
    result = session.execute(
        text(
            f"""
            INSERT INTO analysis.fact_growth
                (site, anchor_date, keyword_id,
                 start_ranking, end_ranking, rank_gain, min_growth_rate)
            WITH win AS (
                SELECT keyword_id, report_date, current_ranking,
                       lag(current_ranking) OVER w             AS prev_ranking,
                       first_value(current_ranking) OVER w     AS start_ranking,
                       count(*) OVER (PARTITION BY keyword_id) AS days_on
                  FROM analysis.fact_daily
                 WHERE site = :site
                   AND report_date BETWEEN :win_start AND :anchor
                   AND current_ranking > 0
                WINDOW w AS (PARTITION BY keyword_id ORDER BY report_date)
            )
            SELECT :site, :anchor, keyword_id,
                   max(start_ranking),
                   {end_rank},
                   max(start_ranking) - {end_rank},
                   {min_rate}
              FROM win
             WHERE days_on = :window_days
             GROUP BY keyword_id
            HAVING max(start_ranking) - {end_rank} > :min_gain
               AND {min_rate} >= :min_rate
            """
        ),
        {
            "site": site, "anchor": anchor_date, "win_start": win_start,
            "window_days": window_days,
            "min_gain": settings.GROWTH_MIN_RANK_GAIN,
            "min_rate": settings.GROWTH_MIN_DAILY_RATE,
        },
    )
    logger.info(
        "持续增长榜算定: %s %s，窗口 [%s, %s]，%d 行",
        site, anchor_date, win_start, anchor_date, result.rowcount,
    )
    return result.rowcount


def import_report(
    session: Session, *, site: str, period: str, report_date: date, source: ImportSource
) -> ImportResult:
    """三段式导入一个报表文件。不提交事务——由调用方控制边界。

    ``source`` 是 :class:`ImportSource`（本地文件或 OSS 对象），导入内核不关心字节来源。
    调用前应已获得导入串行锁（见 service 层）。会在写事实表前确保分区存在。
    """
    site = normalize_site_code(site)
    table, _, _, _ = _validate_period(period)

    validate_structure(source)
    ensure_fact_partitions(session, table, site, report_date)
    # 掉榜表也是分区表，写入前同样必须确保分区存在——漏掉的表现是导入到最后一步
    # 才报 "no partition of relation found for row"，前面的事实数据已经写完了。
    ensure_fact_partitions(session, "fact_dropped", site, report_date)
    if period == "DAY":
        ensure_fact_partitions(session, "fact_growth", site, report_date)

    total, staged, skipped = _stage_csv(session, source)
    written = 0
    dropped = 0
    growth = 0
    date_warning = None
    if staged:
        date_warning = _check_date_consistency(session, report_date)
        _upsert_dimensions(session, site)
        written = _load_facts(session, site, period, report_date)
        # 必须在事实写入之后：掉榜集合是拿窗口内历史与**本期已落库的事实**做差集算出来的。
        dropped = _load_dropped(session, site, period, report_date)
        # 同样必须在事实写入之后（窗口含锚定期本身）。**只有日报**：周报没有「连续 7 天」
        # 这个概念，给它算一份等于把两种时间粒度混进同一张榜。
        if period == "DAY":
            growth = load_growth(session, site, report_date)

    session.execute(text(f"DROP TABLE IF EXISTS {_STAGING_TABLE}"))

    return ImportResult(
        site=site, period=period, report_date=report_date,
        total_data_rows=total, staged_rows=staged, skipped_rows=skipped, written_rows=written,
        dropped_rows=dropped, growth_rows=growth, date_warning=date_warning,
    )


def _check_date_consistency(session: Session, report_date: date) -> str | None:
    """交叉校验：CSV 第 21 列（c20）内的日期 vs 文件名解析出的 report_date。

    report_date 决定写入哪个分区，写错后果严重。但只**警告不拒绝**——文件名是 V1 沿用的
    权威来源，CSV 内日期格式未必稳定，贸然拒绝会挡掉本可正常导入的文件。不一致时返回提示
    字符串（由 service 记入批次记录），一致或解析不出则返回 None。
    """
    sample = session.execute(
        text(f"SELECT c20 FROM {_STAGING_TABLE} WHERE NULLIF(c20, '') IS NOT NULL LIMIT 1")
    ).scalar()
    if not sample:
        return None
    m = _FILENAME_DATE_RE.search(sample)  # 同款 YYYY-MM-DD 正则，容忍引号/前后缀
    if not m:
        return None
    try:
        csv_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if csv_date != report_date:
        msg = f"文件名日期 {report_date} 与 CSV 内日期 {csv_date} 不一致（按文件名写入）"
        logger.warning(msg)
        return msg
    return None
