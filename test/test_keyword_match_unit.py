"""关键词「全部词」匹配规则的单元测试（issue #126，无需 PostgreSQL）。

正则的真实判定（词边界、大小写、`rugby` 不误命中）由
test_keyword_match_integration.py 在真库上验证。
"""
import pytest
from pydantic import ValidationError

from app.table.analysis.analysis_crud import keyword_conditions, request_has_filters
from app.table.analysis.keyword_match import (
    MAX_TERMS,
    escape_like,
    term_stem,
    tokenize,
    word_forms,
)
from app.table.search.search_schemas import AnalysisSearchRequest


def _req(**kw) -> AnalysisSearchRequest:
    return AnalysisSearchRequest(site="US", view="DAY", **kw)


def test_tokenize_lowercases_splits_on_punctuation_and_dedupes():
    assert tokenize("  Halloween   Rug ") == ["halloween", "rug"]
    assert tokenize("faucet-sink, organizer!") == ["faucet", "sink", "organizer"]
    assert tokenize("rug rug RUG") == ["rug"]
    assert tokenize("3d rug") == ["3d", "rug"]


def test_tokenize_of_punctuation_only_is_empty():
    assert tokenize("&") == []
    assert tokenize("  ") == []


@pytest.mark.parametrize(
    "word, expected",
    [
        ("rug", {"rug", "rugs"}),
        ("rugs", {"rugs", "rug"}),
        ("box", {"box", "boxes"}),
        ("boxes", {"boxes", "box"}),
        ("berry", {"berry", "berries"}),
        ("berries", {"berries", "berry"}),
        ("movies", {"movies", "movie"}),
        ("dress", {"dress", "dresses"}),
        ("glasses", {"glasses", "glass"}),
        ("toy", {"toy", "toys"}),
    ],
)
def test_regular_english_plurals_are_covered_both_ways(word, expected):
    assert expected <= set(word_forms(word))


@pytest.mark.parametrize("word", ["3d", "ab", "café", "咖啡机", "x1"])
def test_short_digit_and_non_english_words_are_exact_only(word):
    """不足 3 字母、含数字、非 ASCII：只做精确匹配，不乱猜词形。"""
    assert word_forms(word) == [word]


def test_irregular_plurals_are_not_converted():
    """不规则变化不在范围内：mice 不会还原成 mouse，knives 不会还原成 knife。"""
    assert "mouse" not in word_forms("mice")
    assert "knife" not in word_forms("knives")
    assert "mice" not in word_forms("mouse")


def test_rugby_is_not_a_form_of_rug():
    assert "rugby" not in word_forms("rug")
    assert "rug" not in word_forms("rugby")


def test_stem_is_common_prefix_and_needs_three_chars():
    assert term_stem("berries") == "berr"
    assert term_stem("rug") == "rug"
    assert term_stem("3d") is None


def test_escape_like_neutralises_wildcards_and_escape_char():
    assert escape_like("50%_off!") == "50!%!_off!!"


def _sql(conds) -> str:
    from sqlalchemy.dialects import postgresql

    return " AND ".join(str(c.compile(dialect=postgresql.dialect())) for c in conds)


def test_all_terms_builds_one_condition_per_term_and_not_or():
    """每个词各自成条件、AND 叠加——不能实现成任一词命中（OR）。"""
    conds = keyword_conditions("faucet sink organizer", "ALL_TERMS")
    regexes = [c for c in conds if "~*" in str(c.compile())]
    assert len(regexes) == 3
    assert " OR " not in _sql(conds)


def test_phrase_mode_is_a_single_substring_condition():
    conds = keyword_conditions("faucet sink", "PHRASE")
    assert len(conds) == 1
    assert "ILIKE" in _sql(conds).upper()


def test_punctuation_only_input_falls_back_to_substring_not_to_no_filter():
    conds = keyword_conditions("&", "ALL_TERMS")
    assert len(conds) == 1
    assert "ILIKE" in _sql(conds).upper()


def test_keyword_is_trimmed_and_blank_means_unset():
    assert _req(keyword="  rug ").keyword == "rug"
    assert _req(keyword="   ").keyword is None
    assert request_has_filters(_req(keyword="   ")) is False
    assert request_has_filters(_req(keyword="rug")) is True


def test_default_mode_is_all_terms():
    assert _req(keyword="rug").keyword_mode == "ALL_TERMS"


def test_term_count_is_capped_in_all_terms_mode_only():
    many = " ".join(f"w{i}" for i in range(MAX_TERMS + 1))
    with pytest.raises(ValidationError):
        _req(keyword=many)
    assert _req(keyword=many, keyword_mode="PHRASE").keyword == many
    assert _req(keyword=" ".join(f"w{i}" for i in range(MAX_TERMS))).keyword
