"""Tests for the Settings switch that installs the Windows background task.

Nothing is created here: these lock down the command that would run, the
quoting of install paths with spaces, and the non-Windows / already-off paths.

Run from the slatewire folder:

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import notifier  # noqa: E402


class TaskCommandTests(unittest.TestCase):
    def test_powershell_literal_doubles_single_quotes(self):
        self.assertEqual(notifier._ps_quote("D:\\Testing Ground"), "'D:\\Testing Ground'")
        self.assertEqual(notifier._ps_quote("it's"), "'it''s'")

    def test_command_registers_the_same_task_as_the_bat_file(self):
        argv = notifier._task_command()
        self.assertEqual(argv[0], "powershell")
        script = argv[-1]
        self.assertIn(notifier.TASK_NAME, script)
        self.assertIn("notifier.py", script)
        self.assertIn("New-ScheduledTaskAction", script)
        self.assertIn("New-TimeSpan -Minutes 30", script)
        self.assertIn("Register-ScheduledTask", script)

    def test_non_windows_says_so(self):
        with mock.patch.object(notifier.os, "name", "posix"):
            ok, message = notifier.task_install()
        self.assertFalse(ok)
        self.assertIn("Windows only", message)

    def test_install_reports_a_refusal_from_windows(self):
        refused = mock.Mock(returncode=1, stdout="", stderr="Access is denied.")
        with mock.patch.object(notifier.os, "name", "nt"), \
                mock.patch.object(notifier, "task_status", return_value=False), \
                mock.patch.object(notifier.subprocess, "run", return_value=refused):
            ok, message = notifier.task_install()
        self.assertFalse(ok)
        self.assertIn("Access is denied", message)

    def test_remove_is_a_no_op_when_the_task_is_absent(self):
        with mock.patch.object(notifier, "task_status", return_value=False), \
                mock.patch.object(notifier.subprocess, "run") as run:
            ok, message = notifier.task_remove()
        self.assertTrue(ok)
        run.assert_not_called()
        self.assertIn("off", message)

    def test_remove_deletes_the_task(self):
        done = mock.Mock(returncode=0, stdout="SUCCESS", stderr="")
        with mock.patch.object(notifier, "task_status", side_effect=[True, False]), \
                mock.patch.object(notifier.subprocess, "run", return_value=done) as run:
            ok, _message = notifier.task_remove()
        self.assertTrue(ok)
        self.assertEqual(run.call_args[0][0],
                         ["schtasks", "/Delete", "/TN", notifier.TASK_NAME, "/F"])


if __name__ == "__main__":
    unittest.main()
