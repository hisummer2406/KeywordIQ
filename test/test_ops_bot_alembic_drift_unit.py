"""迁移漂移巡检的单元测试：打桩 subprocess，不真起容器。"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from ops_bot.checks.alembic_drift import is_in_sync


def _proc(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TestIsInSync(unittest.TestCase):
    def test_matching_revisions_in_sync(self):
        with patch(
            "ops_bot.checks.alembic_drift.subprocess.run",
            side_effect=[_proc("71e646b2d200 (head)\n"), _proc("71e646b2d200 (head)\n")],
        ):
            self.assertTrue(is_in_sync())

    def test_mismatched_revisions_out_of_sync(self):
        with patch(
            "ops_bot.checks.alembic_drift.subprocess.run",
            side_effect=[_proc("9d4eb6834e6b\n"), _proc("71e646b2d200 (head)\n")],
        ):
            self.assertFalse(is_in_sync())

    def test_command_failure_returns_none_not_false(self):
        """探测失败不能被当成"漂移"或"同步"——两种误判都会污染告警。"""
        with patch(
            "ops_bot.checks.alembic_drift.subprocess.run",
            side_effect=[_proc("", returncode=1), _proc("71e646b2d200 (head)\n")],
        ):
            self.assertIsNone(is_in_sync())

    def test_timeout_returns_none(self):
        with patch(
            "ops_bot.checks.alembic_drift.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="alembic", timeout=60),
        ):
            self.assertIsNone(is_in_sync())


if __name__ == "__main__":
    unittest.main()
