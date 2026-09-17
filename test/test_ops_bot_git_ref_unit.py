"""SHA 校验与迁移/回滚边界判断的单元测试：打桩 subprocess，不碰真实仓库。"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from ops_bot.release.git_ref import (
    change_summary,
    current_sha,
    is_ancestor_of_branch,
    is_valid_sha_format,
    migrations_changed,
    rollback_needs_review,
)


def _proc(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TestIsValidShaFormat(unittest.TestCase):
    def test_full_40_char_sha_accepted(self):
        self.assertTrue(is_valid_sha_format("a" * 40))

    def test_short_sha_rejected(self):
        self.assertFalse(is_valid_sha_format("a" * 7))

    def test_branch_name_rejected(self):
        self.assertFalse(is_valid_sha_format("v2"))

    def test_uppercase_rejected(self):
        """大写十六进制不是 git 实际产出的形式，拒绝而非规整化。"""
        self.assertFalse(is_valid_sha_format("A" * 40))


class TestIsAncestorOfBranch(unittest.TestCase):
    def test_ancestor_returns_true(self):
        with patch("ops_bot.release.git_ref.subprocess.run", side_effect=[_proc(), _proc(returncode=0)]):
            self.assertTrue(is_ancestor_of_branch("a" * 40))

    def test_not_ancestor_returns_false(self):
        with patch("ops_bot.release.git_ref.subprocess.run", side_effect=[_proc(), _proc(returncode=1)]):
            self.assertFalse(is_ancestor_of_branch("a" * 40))

    def test_fetch_failure_returns_none(self):
        with patch(
            "ops_bot.release.git_ref.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            self.assertIsNone(is_ancestor_of_branch("a" * 40))

    def test_sha_not_found_returns_none_not_false(self):
        """既不是 0 也不是 1 的返回码（sha 在本地对象库里都找不到）不能被当成"否"。"""
        with patch("ops_bot.release.git_ref.subprocess.run", side_effect=[_proc(), _proc(returncode=128)]):
            self.assertIsNone(is_ancestor_of_branch("a" * 40))


class TestMigrationsChanged(unittest.TestCase):
    def test_same_tree_hash_no_migration(self):
        with patch(
            "ops_bot.release.git_ref.subprocess.run",
            side_effect=[_proc(stdout="tree-abc\n"), _proc(stdout="tree-abc\n")],
        ):
            self.assertFalse(migrations_changed("a" * 40))

    def test_different_tree_hash_migration_present(self):
        with patch(
            "ops_bot.release.git_ref.subprocess.run",
            side_effect=[_proc(stdout="tree-abc\n"), _proc(stdout="tree-xyz\n")],
        ):
            self.assertTrue(migrations_changed("a" * 40))

    def test_command_failure_returns_none(self):
        with patch(
            "ops_bot.release.git_ref.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            self.assertIsNone(migrations_changed("a" * 40))


class TestRollbackNeedsReview(unittest.TestCase):
    def test_not_an_ancestor_no_review_needed(self):
        """目标不是"更早"的提交（可能是同分支往前，或完全无关分支），不算回滚。"""
        with patch("ops_bot.release.git_ref._is_ancestor", return_value=False):
            self.assertFalse(rollback_needs_review("a" * 40))

    def test_ancestor_without_migration_diff_no_review_needed(self):
        with patch("ops_bot.release.git_ref._is_ancestor", return_value=True), patch(
            "ops_bot.release.git_ref.migrations_changed", return_value=False
        ):
            self.assertFalse(rollback_needs_review("a" * 40))

    def test_ancestor_with_migration_diff_needs_review(self):
        with patch("ops_bot.release.git_ref._is_ancestor", return_value=True), patch(
            "ops_bot.release.git_ref.migrations_changed", return_value=True
        ):
            self.assertTrue(rollback_needs_review("a" * 40))

    def test_ancestry_unknown_returns_none(self):
        with patch("ops_bot.release.git_ref._is_ancestor", return_value=None):
            self.assertIsNone(rollback_needs_review("a" * 40))


class TestCurrentSha(unittest.TestCase):
    def test_returns_stripped_output(self):
        with patch(
            "ops_bot.release.git_ref.subprocess.run", return_value=_proc(stdout="abc123\n")
        ):
            self.assertEqual(current_sha(), "abc123")

    def test_command_failure_returns_none(self):
        with patch(
            "ops_bot.release.git_ref.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            self.assertIsNone(current_sha())


class TestChangeSummary(unittest.TestCase):
    def test_returns_one_line_per_commit(self):
        with patch(
            "ops_bot.release.git_ref.subprocess.run",
            return_value=_proc(stdout="abc111 fix: a\nabc222 feat: b\n"),
        ):
            lines = change_summary("a" * 40, "b" * 40)

        self.assertEqual(lines, ["abc111 fix: a", "abc222 feat: b"])

    def test_truncates_beyond_max_lines(self):
        stdout = "\n".join(f"sha{i} commit {i}" for i in range(15))
        with patch("ops_bot.release.git_ref.subprocess.run", return_value=_proc(stdout=stdout)):
            lines = change_summary("a" * 40, "b" * 40, max_lines=10)

        self.assertEqual(len(lines), 11)
        self.assertIn("共 15 条提交", lines[-1])

    def test_command_failure_returns_none(self):
        with patch(
            "ops_bot.release.git_ref.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            self.assertIsNone(change_summary("a" * 40, "b" * 40))


if __name__ == "__main__":
    unittest.main()
