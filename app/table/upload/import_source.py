"""导入数据源抽象。

导入内核只关心"能按 utf-8-sig 逐行读到 CSV 文本"，不关心字节来自本地磁盘还是 OSS。
把来源抽象成 :class:`ImportSource`，让 :mod:`app.table.upload.importer` 与具体来源解耦：

- :class:`LocalFileSource`：读本地文件（旧路径与失败退路）。
- :class:`OSSObjectSource`：凭一个"打开器"从 OSS 流式读对象，真流式、有界内存，不落地临时文件。

两者都提供 ``open_text()`` 上下文管理器，产出一个文本流。OSS 侧每次 ``open_text`` 是一次
新的对象读取（从头流式），因此 ``validate_structure`` 与 ``_stage_csv`` 各开一次是安全的。
"""

from __future__ import annotations

import io
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import IO

# CSV 统一按带 BOM 的 UTF-8 解析——真实报表首行含 BOM（见 docs/table 样本）。
_ENCODING = "utf-8-sig"


class ImportSource:
    """导入来源的抽象基类。子类提供 ``location`` 与 ``open_text()``。"""

    location: str

    def open_text(self):  # pragma: no cover - 抽象方法
        raise NotImplementedError


class LocalFileSource(ImportSource):
    """本地文件来源。"""

    def __init__(self, path: str):
        self.path = path
        self.location = path

    @contextmanager
    def open_text(self) -> Iterator[IO[str]]:
        with open(self.path, encoding=_ENCODING, newline="") as fh:
            yield fh


class _RawReadable(io.RawIOBase):
    """把任意"支持 ``read(size)`` 的二进制流"适配成 ``RawIOBase``。

    OSS SDK 的对象流与 ``io.BytesIO`` 都支持 ``read(size)``，但未必都实现完整的
    ``RawIOBase`` 协议。统一包一层，``BufferedReader`` + ``TextIOWrapper`` 即可稳定地
    做流式文本读取。
    """

    def __init__(self, src):
        self._src = src

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        chunk = self._src.read(len(b))
        if not chunk:
            return 0
        n = len(chunk)
        b[: n] = chunk
        return n


class OSSObjectSource(ImportSource):
    """OSS 对象来源。

    ``opener(key)`` 返回一个支持 ``read(size)`` 的二进制流（生产环境即
    ``oss_client.object_stream``；测试可传返回 ``io.BytesIO`` 的桩）。
    """

    def __init__(self, opener: Callable[[str], object], key: str, location: str | None = None):
        self._opener = opener
        self.key = key
        self.location = location or f"oss://{key}"

    @contextmanager
    def open_text(self) -> Iterator[IO[str]]:
        raw = self._opener(self.key)
        buffered = io.BufferedReader(_RawReadable(raw))
        text = io.TextIOWrapper(buffered, encoding=_ENCODING, newline="")
        try:
            yield text
        finally:
            # 先解绑，避免 TextIOWrapper 关闭时连带关掉底层导致重复关闭异常；
            # 再显式关掉 OSS 流（释放连接）。
            try:
                text.detach()
            except Exception:
                pass
            close = getattr(raw, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def source_from_upload_path(path: str) -> LocalFileSource:
    """便捷构造：本地上传文件来源。"""
    return LocalFileSource(path)


def basename(name: str) -> str:
    """取纯文件名，去掉任何路径成分（防目录穿越进入 object key）。"""
    return os.path.basename(name.replace("\\", "/"))
