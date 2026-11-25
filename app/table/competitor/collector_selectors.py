"""采集端页面选择器覆盖：从部署方配置的 JSON 文件读取并校验（extension-remote-selectors）。

插件里的 DOM 选择器写死在代码里，Amazon 改版后要重装插件才能修。MV3 不允许加载远程代码，
但允许读远程数据：选择器只是数据，放在服务端文件里，改完下一次请求就生效，不用重启进程。

整份文件要么全部合法、要么整体无效（返回 None 并告警）：部分生效会让运营以为改动已经上线。
只接受 CSS 选择器与纯文本——插件不会把这里的任何内容当成正则或代码执行。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

MAX_SELECTORS_PER_KEY = 10
MAX_SELECTOR_LEN = 200
MAX_VERSION_LEN = 64

_SelectorList = list[str]


class SelectorOverrides(BaseModel):
    """插件认得的六类覆盖；每类可选。未知键整体拒绝（extra=forbid）。"""

    model_config = ConfigDict(extra="forbid")

    result_card: _SelectorList | None = Field(default=None, min_length=1, max_length=MAX_SELECTORS_PER_KEY)
    next_page: _SelectorList | None = Field(default=None, min_length=1, max_length=MAX_SELECTORS_PER_KEY)
    seller_name: _SelectorList | None = Field(default=None, min_length=1, max_length=MAX_SELECTORS_PER_KEY)
    delivery: _SelectorList | None = Field(default=None, min_length=1, max_length=MAX_SELECTORS_PER_KEY)
    blocked_selector: _SelectorList | None = Field(default=None, min_length=1, max_length=MAX_SELECTORS_PER_KEY)
    blocked_text: _SelectorList | None = Field(default=None, min_length=1, max_length=MAX_SELECTORS_PER_KEY)

    @field_validator("*")
    @classmethod
    def _check_items(cls, values: list[str] | None) -> list[str] | None:
        for value in values or ():
            if not value.strip() or len(value) > MAX_SELECTOR_LEN:
                raise ValueError(f"选择器不能为空串，且不超过 {MAX_SELECTOR_LEN} 字符")
        return values


class SelectorFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=MAX_VERSION_LEN)
    selectors: SelectorOverrides


@dataclass(frozen=True)
class _CacheEntry:
    path: str
    mtime_ns: int
    size: int
    value: SelectorFile | None


_cache: _CacheEntry | None = None
_lock = threading.Lock()


def _parse(path: str) -> SelectorFile | None:
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return SelectorFile.model_validate(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError):
        logger.warning("采集端选择器文件无效，不下发覆盖：%s", path, exc_info=True)
        return None


def load_selector_overrides(path: str) -> SelectorFile | None:
    """读取选择器文件；按 (路径, mtime, 大小) 缓存，文件改了下一次调用就重新读取。

    未配置路径返回 None；文件不存在或不合法返回 None 并告警（不抛异常——
    单批上限必须照常返回，插件退回内置选择器）。
    """
    global _cache
    if not path:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        logger.warning("采集端选择器文件不存在或无法读取，不下发覆盖：%s", path, exc_info=True)
        return None
    with _lock:
        cached = _cache
        if cached and cached.path == path and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
            return cached.value
        value = _parse(path)
        _cache = _CacheEntry(path=path, mtime_ns=stat.st_mtime_ns, size=stat.st_size, value=value)
        return value
