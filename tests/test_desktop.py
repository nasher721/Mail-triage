import subprocess
import unittest
from unittest.mock import patch

from email_triage.desktop import OutlookDesktopMailbox, desktop_diagnostic


class SequencedRunner:
    def __init__(self, results: list[subprocess.CompletedProcess[str]]):
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        return self.results.pop(0)


class DesktopMailboxTests(unittest.TestCase):
    @patch("email_triage.desktop.shutil")
    @patch("email_triage.desktop.platform")
    def test_reads_only_frontmost_visible_message(self, platform_module, shutil_module) -> None:
        platform_module.system.return_value = "Darwin"
        shutil_module.which.return_value = "/usr/bin/osascript"
        runner = SequencedRunner(
            [
                subprocess.CompletedProcess([], 0, stdout="true\n", stderr=""),
                subprocess.CompletedProcess(
                    [], 0, stdout="Synthetic subject\nVisible body text\n", stderr=""
                ),
            ]
        )
        mailbox = OutlookDesktopMailbox(runner)
        messages = mailbox.unread_messages(20)
        self.assertEqual(messages[0].subject, "Synthetic subject")
        self.assertEqual(messages[0].body, "")
        self.assertNotIn("Visible body text", messages[0].id)
        self.assertFalse(hasattr(mailbox, "move_message"))
        self.assertFalse(hasattr(mailbox, "create_reply_draft"))
        self.assertEqual(len(runner.calls), 2)

    @patch("email_triage.desktop.platform")
    def test_non_macos_fails_closed_without_running_a_command(self, platform_module) -> None:
        platform_module.system.return_value = "Linux"
        runner = SequencedRunner([])
        result = desktop_diagnostic(runner)
        self.assertFalse(result.available)
        self.assertEqual(result.code, "unsupported_platform")
        self.assertEqual(runner.calls, [])

    @patch("email_triage.desktop.shutil")
    @patch("email_triage.desktop.platform")
    def test_processed_visible_message_is_excluded(self, platform_module, shutil_module) -> None:
        platform_module.system.return_value = "Darwin"
        shutil_module.which.return_value = "/usr/bin/osascript"
        first = SequencedRunner(
            [
                subprocess.CompletedProcess([], 0, stdout="true\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="Subject\nBody\n", stderr=""),
            ]
        )
        message = OutlookDesktopMailbox(first).unread_messages(1)[0]
        second = SequencedRunner(
            [
                subprocess.CompletedProcess([], 0, stdout="true\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="Subject\nBody\n", stderr=""),
            ]
        )
        self.assertEqual(
            OutlookDesktopMailbox(second).unread_messages(1, {message.id}), []
        )


if __name__ == "__main__":
    unittest.main()
