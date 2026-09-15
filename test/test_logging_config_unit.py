"""WatchedFileHandler 的多进程安全轮转假设：验证外部 rename+新建文件后，handler
在下一次 emit() 时会重新打开新文件，不丢失、不写入已改名的旧文件。

不依赖真实 logrotate 或多进程——这里用单进程模拟"文件被外部轮转机制替换"这个
事件本身，这正是 WatchedFileHandler 与 logrotate `create` 模式配合时依赖的唯一
契约（design.md 决策 A）。
"""
from __future__ import annotations

import logging
import logging.handlers
import os


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_watched_file_handler_reopens_after_external_rotation(tmp_path):
    log_path = tmp_path / "app.log"
    handler = logging.handlers.WatchedFileHandler(str(log_path), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test.watched_rotation")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False

    try:
        logger.info("before-rotation-1")
        logger.info("before-rotation-2")

        # 模拟 logrotate 的 `create` 模式：rename 走当前文件，新建同名空文件。
        rotated_path = tmp_path / "app.log.1"
        os.rename(log_path, rotated_path)
        with open(log_path, "w", encoding="utf-8"):
            pass

        # 下一次 emit() 应侦测到 (dev, ino) 变化并重新打开新文件。
        logger.info("after-rotation-1")
        logger.info("after-rotation-2")
    finally:
        logger.removeHandler(handler)
        handler.close()

    rotated_content = _read(str(rotated_path))
    assert "before-rotation-1" in rotated_content
    assert "before-rotation-2" in rotated_content
    assert "after-rotation" not in rotated_content

    new_content = _read(str(log_path))
    assert "after-rotation-1" in new_content
    assert "after-rotation-2" in new_content
    assert "before-rotation" not in new_content


def test_watched_file_handler_survives_missing_directory_recreated(tmp_path):
    """轮转发生在没有新日志写入的低流量窗口时，handler 仍持有旧 fd 直到下一次
    写入才切换——这不是数据丢失（design.md「风险」一节），这里验证"迟到的切换"
    本身不会报错或写坏文件。"""
    log_path = tmp_path / "app.log"
    handler = logging.handlers.WatchedFileHandler(str(log_path), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test.watched_rotation_idle")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False

    try:
        logger.info("first")
        os.rename(log_path, tmp_path / "app.log.1")
        with open(log_path, "w", encoding="utf-8"):
            pass
        # 轮转后长时间没有写入——handler 此刻仍持有旧 fd，属于预期的滞后窗口。
        logger.info("second")
    finally:
        logger.removeHandler(handler)
        handler.close()

    assert "second" in _read(str(log_path))
