"""品牌词排除的真库验证（issue #119）。

默认跳过，只在设置 TEST_DATABASE_URL 时运行。正则的转义与整词边界取决于 PostgreSQL
自己的实现，Python 里模拟不出来。不依赖业务表：用 VALUES 构造 (关键词, 品牌) 对。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_new_word_rules_integration.py -v
"""
import os

import pytest
from sqlalchemy import String, column, create_engine, select, values
from sqlalchemy.pool import NullPool

from app.table.analysis.analysis_crud import brand_in_keyword

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)


def _hits(pairs: list[tuple[str, str | None]]) -> set[tuple[str, str | None]]:
    engine = create_engine(TEST_URL, poolclass=NullPool)
    try:
        t = values(column("kw", String), column("brand", String), name="t").data(pairs)
        # coalesce：品牌为 NULL 时判定结果是 NULL，EXISTS 里它等价于假
        stmt = select(t.c.kw, t.c.brand).where(brand_in_keyword(t.c.kw, t.c.brand))
        with engine.connect() as conn:
            return {(r[0], r[1]) for r in conn.execute(stmt)}
    finally:
        engine.dispose()


def test_keyword_containing_brand_as_whole_word_is_matched_case_insensitively():
    assert _hits([("anker power bank", "Anker"), ("ANKER charger", "anker")]) == {
        ("anker power bank", "Anker"),
        ("ANKER charger", "anker"),
    }


def test_brand_must_be_a_whole_word():
    assert _hits([("ankerwork cable", "Anker"), ("canker sore", "Anker")]) == set()
    assert _hits([("anker-power bank", "Anker")]) == {("anker-power bank", "Anker")}


def test_multi_word_brand_is_matched_as_a_phrase():
    assert _hits([("the north face jacket", "The North Face")]) == {
        ("the north face jacket", "The North Face")
    }
    assert _hits([("north jacket face", "The North Face")]) == set()


def test_regex_metacharacters_in_brand_names_are_literal():
    assert _hits([("c++ book", "C++"), ("cc book", "C++")]) == {("c++ book", "C++")}
    assert _hits([("a.b lamp", "a.b"), ("axb lamp", "a.b")]) == {("a.b lamp", "a.b")}
    assert _hits([("(new) case", "(new)")]) == {("(new) case", "(new)")}


def test_empty_short_or_null_brand_never_matches():
    """空串会产出匹配一切的正则；单字母品牌会命中几乎所有词；NULL 不应排除任何词。"""
    assert _hits([("phone case", ""), ("a case", "A"), ("phone case", None)]) == set()
