"""有界日志读取的单元测试：用合成日志内容验证正则与窗口过滤，不依赖真实 logs/app.log。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from ops_bot.checks.logs import count_recent_errors, find_by_request_id


def _line(*, minutes_ago: int, level: str, message: str, request_id: str = "req-1") -> str:
    ts = (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    return f"{ts} - app - [{request_id}] - pid=123 - {level} - {message}"


class TestCountRecentErrors(unittest.TestCase):
    def _write(self, tmp_path, lines: list[str]) -> str:
        path = tmp_path / "app.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def test_counts_error_level_within_window(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                Path(tmp),
                [
                    _line(minutes_ago=1, level="ERROR", message="boom"),
                    _line(minutes_ago=2, level="INFO", message="请求摘要 GET /x status=200 outcome=completed"),
                ],
            )
            self.assertEqual(count_recent_errors(path, since_minutes=5), 1)

    def test_counts_5xx_request_summary_even_at_info_level(self):
        """业务失败以 INFO 级记录（outcome=completed），只看 level 会漏掉这类。"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                Path(tmp),
                [_line(minutes_ago=1, level="INFO", message="请求摘要 POST /x status=503 outcome=completed")],
            )
            self.assertEqual(count_recent_errors(path, since_minutes=5), 1)

    def test_excludes_lines_outside_window(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), [_line(minutes_ago=10, level="ERROR", message="old boom")])
            self.assertEqual(count_recent_errors(path, since_minutes=5), 0)

    def test_missing_file_returns_zero(self):
        self.assertEqual(count_recent_errors("/nonexistent/app.log", since_minutes=5), 0)

    def test_does_not_double_count_error_line_with_5xx(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                Path(tmp),
                [_line(minutes_ago=1, level="ERROR", message="请求摘要 GET /x status=500 outcome=error")],
            )
            self.assertEqual(count_recent_errors(path, since_minutes=5), 1)


class TestFindByRequestId(unittest.TestCase):
    def test_returns_only_matching_request_id(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.log"
            lines = [
                _line(minutes_ago=1, level="INFO", message="a", request_id="req-A"),
                _line(minutes_ago=1, level="ERROR", message="b", request_id="req-B"),
                _line(minutes_ago=1, level="INFO", message="c", request_id="req-A"),
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            result = find_by_request_id(str(path), "req-A")

        self.assertEqual(len(result), 2)
        self.assertTrue(all("req-A" in line for line in result))

    def test_respects_max_lines(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.log"
            lines = [_line(minutes_ago=1, level="INFO", message=f"m{i}", request_id="req-A") for i in range(10)]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            result = find_by_request_id(str(path), "req-A", max_lines=3)

        self.assertEqual(len(result), 3)


if __name__ == "__main__":
    unittest.main()
