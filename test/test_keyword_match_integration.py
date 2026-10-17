"""关键词「全部词」匹配的真库验证（issue #126）。

默认跳过，只在设置 TEST_DATABASE_URL 时运行。正则的词边界、大小写、`[[:alnum:]]` 的
行为取决于 PostgreSQL 自己的实现，Python 里模拟不出来，必须真库。不依赖任何业务表：
用 VALUES 构造候选词，直接套 ``keyword_conditions`` 产出的条件。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_keyword_match_integration.py -v
"""
import os

import pytest
from sqlalchemy import String, column, create_engine, select, values
from sqlalchemy.pool import NullPool

from app.table.analysis.analysis_crud import keyword_conditions

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

CORPUS = [
    # --- faucet sink organizer（客户反馈，2026-10-11 日报）
    "faucet sink organizer",
    "over faucet sink organizer",
    "faucet sink organizer rack",
    "kitchen sink faucet organizer",
    "sink faucet organizer",
    "kitchen sink organizer around faucet",
    "sink organizer faucet",
    "faucet mount sink caddy organizer",
    "faucet organizer",
    "sink organizer",
    "faucet sink",
    # --- halloween rug（第 41 周周报）
    "halloween rug",
    "halloween rugs",
    "halloween bathroom rug",
    "halloween outdoor rug",
    "halloween 3d rug",
    "halloween rugby",
    "halloween-rug set",
    "Halloween Area RUG",
    "rug halloween",
    "halloween",
    # --- 词形与边界
    "berry basket",
    "berries basket",
    "gift boxes",
    "gift box",
    "movie night",
    "movies night",
    "mice trap",
    "mouse trap",
    "50% off sale",
    "50x off sale",
]


def _match(keyword: str, mode: str = "ALL_TERMS") -> set[str]:
    engine = create_engine(TEST_URL, poolclass=NullPool)
    try:
        t = values(column("keyword", String), name="t").data([(k,) for k in CORPUS])
        stmt = select(t.c.keyword).where(*_rebind(keyword_conditions(keyword, mode), t))
        with engine.connect() as conn:
            return {r[0] for r in conn.execute(stmt)}
    finally:
        engine.dispose()


def _rebind(conds, t):
    """把针对 DimKeyword.keyword 的条件改绑到 VALUES 的列上。"""
    from sqlalchemy.sql import visitors

    from app.table.analysis.dimension_model import DimKeyword

    def swap(el):
        if el is DimKeyword.keyword.__clause_element__() or getattr(el, "key", None) == "keyword" and getattr(el, "table", None) is DimKeyword.__table__:
            return t.c.keyword

    return [visitors.replacement_traverse(c, {}, swap) for c in conds]


def test_faucet_sink_organizer_hits_original_and_reordered_or_inserted_terms():
    hits = _match("faucet sink organizer")
    assert hits == {
        "faucet sink organizer",
        "over faucet sink organizer",
        "faucet sink organizer rack",
        "kitchen sink faucet organizer",
        "sink faucet organizer",
        "kitchen sink organizer around faucet",
        "sink organizer faucet",
        "faucet mount sink caddy organizer",
    }


def test_missing_a_core_term_never_matches():
    hits = _match("faucet sink organizer")
    for missing in ("faucet organizer", "sink organizer", "faucet sink"):
        assert missing not in hits


def test_halloween_rug_matches_inserted_words_plural_and_hyphen_but_not_rugby():
    hits = _match("Halloween Rug")
    assert {
        "halloween rug",
        "halloween rugs",
        "halloween bathroom rug",
        "halloween outdoor rug",
        "halloween 3d rug",
        "halloween-rug set",
        "Halloween Area RUG",
        "rug halloween",
    } <= hits
    assert "halloween rugby" not in hits
    assert "halloween" not in hits


def test_plural_is_covered_in_both_directions():
    assert _match("halloween rugs") == _match("halloween rug")
    assert _match("berry") == {"berry basket", "berries basket"}
    assert _match("berries") == {"berry basket", "berries basket"}
    assert _match("box") == {"gift boxes", "gift box"}
    assert _match("boxes") == {"gift boxes", "gift box"}
    assert _match("movie") == {"movie night", "movies night"}


def test_irregular_plurals_are_not_unified():
    assert _match("mouse") == {"mouse trap"}
    assert _match("mice") == {"mice trap"}


def test_case_and_whitespace_are_normalised_by_tokenizing():
    assert _match("  FAUCET   Sink\tOrganizer ") == _match("faucet sink organizer")


def test_single_word_is_whole_word_not_substring():
    assert "halloween rugby" not in _match("rug")
    assert "halloween rug" in _match("rug")


def test_phrase_mode_keeps_old_contiguous_substring_behaviour():
    assert _match("faucet sink organizer", "PHRASE") == {
        "faucet sink organizer",
        "over faucet sink organizer",
        "faucet sink organizer rack",
    }
    # 旧行为：子串，所以 rug 会命中 rugby
    assert "halloween rugby" in _match("rug", "PHRASE")


def test_percent_and_underscore_are_literal_in_phrase_mode():
    assert _match("50%", "PHRASE") == {"50% off sale"}
    assert _match("50_", "PHRASE") == set()
