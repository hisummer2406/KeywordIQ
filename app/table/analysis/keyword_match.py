"""关键词检索的「全部词」匹配规则（issue #126）。

两种模式：

- ``PHRASE``：连续子串（``ILIKE '%输入%'``），即旧行为。
- ``ALL_TERMS``：输入拆成词，**每个词都要作为完整单词出现在关键词里，顺序不限、允许
  额外词**（AND，不是 OR）。

纯函数，无数据库依赖，``/search`` 与 ``/export`` 经 ``analysis_crud._apply_filters``
共用同一份——两处各写一份的表现是「导出的行数与表里看到的不一样」。

分词与词形规则（都是有意的小范围取舍）：

- 小写、按「非字母数字」切分：空白、连字符、标点都是分隔符，所以 ``Halloween  Rug``、
  ``faucet-sink`` 都能拆开。重复词只算一次。
- **单复数仅限 ASCII 英文的常规变化**：``s`` / ``es`` / ``ies``↔``y``。不规则变化
  （mice、knives、men）、非英语、含数字的词、不足 3 个字母的词一律只做精确匹配。
  不简单去末尾 ``s``——那会让 ``glass`` 变成 ``glas``、``rugby`` 之类误命中。
- 词边界匹配，``rug`` 不命中 ``rugby``，但命中 ``rugs``、``area-rug``。
"""
from __future__ import annotations

import os
import re
from typing import Literal

KeywordMode = Literal["ALL_TERMS", "PHRASE"]

# 单次检索最多接受的词数。每个词是一个独立的正则条件，无上限会让计划器放弃索引。
MAX_TERMS = 8

# trgm 索引需要至少 3 个字符才能生效
_MIN_STEM_FOR_INDEX = 3

_SPLIT_RE = re.compile(r"[\W_]+", re.UNICODE)
_PLAIN_ENGLISH_RE = re.compile(r"^[a-z]{3,}$")
_SIBILANT_ENDINGS = ("s", "x", "z", "ch", "sh")
_VOWELS = "aeiou"


def tokenize(text: str) -> list[str]:
    """小写、按非字母数字切分、去重（保持首次出现顺序）。"""
    seen: dict[str, None] = {}
    for part in _SPLIT_RE.split(text.lower()):
        if part:
            seen.setdefault(part, None)
    return list(seen)


def word_forms(token: str) -> list[str]:
    """一个词的可接受词形（含自身）。仅对 ASCII 纯字母、≥3 字母的词做单复数变换。"""
    if not _PLAIN_ENGLISH_RE.match(token):
        return [token]

    forms = [token]
    if token.endswith("ies") and len(token) > 4:
        # berries → berry；也可能是 movies → movie，两种还原都收（多出的假词形不会命中真词）
        forms += [token[:-3] + "y", token[:-1]]
    elif token.endswith("y") and token[-2] not in _VOWELS:
        forms.append(token[:-1] + "ies")  # berry → berries
    elif token.endswith("oes"):
        # tomatoes → tomato；shoes → shoe。两种还原都收（多出的假词形不会命中真词）
        forms += [token[:-2], token[:-1]]
    elif token.endswith("es") and token[:-2].endswith(_SIBILANT_ENDINGS):
        forms.append(token[:-2])  # boxes → box, dresses → dress
    elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        forms.append(token[:-1])  # rugs → rug
    elif token.endswith(_SIBILANT_ENDINGS):
        forms.append(token + "es")  # box → boxes, dress → dresses
    elif token.endswith("o"):
        forms += [token + "es", token + "s"]  # tomato → tomatoes，photo → photos
    else:
        forms.append(token + "s")  # rug → rugs
    return list(dict.fromkeys(forms))


def term_regex(token: str) -> str:
    """PostgreSQL 正则：该词（任一词形）作为完整单词出现。

    边界用「非字母数字或串首/串尾」而不是 ``\\m``/``\\M``：后者把下划线算作词字符，
    与上面的分词口径不一致。词形只含 ``[a-z0-9]`` 与非 ASCII 字母，无需转义。
    """
    alternatives = "|".join(word_forms(token))
    return rf"(^|[^[:alnum:]])({alternatives})([^[:alnum:]]|$)"


def term_stem(token: str) -> str | None:
    """各词形的公共前缀，供 ``ILIKE '%stem%'`` 预筛走 trgm 索引；不足 3 字符则无。"""
    stem = os.path.commonprefix(word_forms(token))
    return stem if len(stem) >= _MIN_STEM_FOR_INDEX else None


# 转义符不用反斜杠：ESCAPE 子句里的反斜杠是否要双写取决于 standard_conforming_strings，
# 换成普通字符就没有这个依赖。
LIKE_ESCAPE = "!"


def escape_like(text: str) -> str:
    """转义 LIKE 通配符（``%`` ``_`` 与转义符本身），配合 ``escape=LIKE_ESCAPE`` 使用。"""
    return (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
