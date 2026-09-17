"""备份编排的单元测试：`_parse_result` 用脚本当前真实文案样例覆盖；
`run_backup`/`cleanup_old_backups` 打桩 executor/OSS/DB。
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from ops_bot.backup import _parse_result, run_backup
from ops_bot.executor import ExecutionResult

_SUCCESS_OUTPUT = """
在 db 容器内执行 pg_dump（全量）...
$ docker compose exec -T db pg_dump ...

把 dump 从容器复制到本地临时文件 /tmp/xxx.custom ...
$ docker compose cp ...

清理容器内的临时文件...
$ docker compose exec -T db rm -f ...

上传到 OSS: my-bucket/backups/postgres/20260916T030000Z.custom（512.3 MB）...

备份完成：backups/postgres/20260916T030000Z.custom
"""

_DUMP_FAILED_OUTPUT = """
在 db 容器内执行 pg_dump（全量）...
$ docker compose exec -T db pg_dump ...
Traceback ...
CalledProcessError
"""

_COPY_OUT_FAILED_OUTPUT = """
在 db 容器内执行 pg_dump（全量）...
把 dump 从容器复制到本地临时文件 /tmp/xxx.custom ...
备份失败，仍尝试清理容器内可能留下的临时文件...
"""

_UPLOAD_FAILED_OUTPUT = """
在 db 容器内执行 pg_dump（全量）...
把 dump 从容器复制到本地临时文件 /tmp/xxx.custom ...
清理容器内的临时文件...
上传到 OSS: my-bucket/backups/postgres/20260916T030000Z.custom（512.3 MB）...
oss2.exceptions.RequestError: ...
"""


class TestParseResult(unittest.TestCase):
    def test_success_extracts_object_key_and_size(self):
        object_key, size_bytes, failed_stage = _parse_result(_SUCCESS_OUTPUT)

        self.assertEqual(object_key, "backups/postgres/20260916T030000Z.custom")
        self.assertAlmostEqual(size_bytes, int(512.3 * 1024 * 1024))
        self.assertIsNone(failed_stage)

    def test_dump_failure_detected(self):
        _, _, failed_stage = _parse_result(_DUMP_FAILED_OUTPUT)
        self.assertEqual(failed_stage, "DUMP")

    def test_copy_out_failure_detected(self):
        _, _, failed_stage = _parse_result(_COPY_OUT_FAILED_OUTPUT)
        self.assertEqual(failed_stage, "COPY_OUT")

    def test_upload_failure_detected(self):
        _, _, failed_stage = _parse_result(_UPLOAD_FAILED_OUTPUT)
        self.assertEqual(failed_stage, "UPLOAD")


class TestRunBackup(unittest.IsolatedAsyncioTestCase):
    async def test_failure_records_before_raising(self):
        """失败时必须先落一行 ops_backup_record 再抛异常——不能因为抛了异常就漏记。"""
        record = AsyncMock()
        with patch(
            "ops_bot.backup.executor.backup_now",
            return_value=ExecutionResult(False, _DUMP_FAILED_OUTPUT, None),
        ), patch("ops_bot.backup.crud.record_backup", record), patch(
            "ops_bot.backup.OpsAsyncSessionFactory"
        ) as factory:
            factory.return_value.__aenter__.return_value = AsyncMock()
            with self.assertRaises(RuntimeError):
                await run_backup(retention_days=30)

        record.assert_awaited_once()
        self.assertEqual(record.call_args.kwargs["status"], "FAILED")
        self.assertEqual(record.call_args.kwargs["failed_stage"], "DUMP")

    async def test_success_records_and_cleans_up(self):
        record = AsyncMock()
        cleanup = AsyncMock(return_value="无需清理")
        with patch(
            "ops_bot.backup.executor.backup_now",
            return_value=ExecutionResult(True, _SUCCESS_OUTPUT, None),
        ), patch("ops_bot.backup.crud.record_backup", record), patch(
            "ops_bot.backup.cleanup_old_backups", cleanup
        ), patch("ops_bot.backup.OpsAsyncSessionFactory") as factory:
            factory.return_value.__aenter__.return_value = AsyncMock()
            output = await run_backup(retention_days=30)

        record.assert_awaited_once()
        self.assertEqual(record.call_args.kwargs["status"], "SUCCEEDED")
        cleanup.assert_awaited_once_with(retention_days=30)
        self.assertIn("备份成功", output)


if __name__ == "__main__":
    unittest.main()
