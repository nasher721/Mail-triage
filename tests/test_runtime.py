import os
import tempfile
import unittest
from pathlib import Path
from stat import S_IMODE
from unittest.mock import patch

from email_triage.config import ConfigurationError
from email_triage.graph import GraphError, GraphMailbox
from email_triage.runtime import LockBusy, load_env_file, parse_env_file, single_instance_lock


class EnvFileTests(unittest.TestCase):
    def write(self, directory: str, text: str, mode: int = 0o600) -> Path:
        path = Path(directory) / "env"
        path.write_text(text, encoding="utf-8")
        os.chmod(path, mode)
        return path

    def test_parses_comments_quotes_and_export(self) -> None:
        values = parse_env_file(
            "# comment\n\nexport MS_TENANT_ID=abc\nOLLAMA_MODEL=\"qwen3:8b\"\nTRIAGE_APPLY=true\n"
        )
        self.assertEqual(
            values,
            {"MS_TENANT_ID": "abc", "OLLAMA_MODEL": "qwen3:8b", "TRIAGE_APPLY": "true"},
        )

    def test_malformed_line_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "line 1"):
            parse_env_file("not-a-setting\n")

    def test_group_readable_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "MS_TENANT_ID=abc\n", mode=0o644)
            with self.assertRaisesRegex(ConfigurationError, "readable by other users"):
                load_env_file(path)

    def test_existing_environment_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "OLLAMA_MODEL=from-file\nMAX_UNREAD_MESSAGES=7\n")
            with patch.dict("os.environ", {"OLLAMA_MODEL": "from-shell"}, clear=True):
                loaded = load_env_file(path)
                self.assertEqual(os.environ["OLLAMA_MODEL"], "from-shell")
                self.assertEqual(os.environ["MAX_UNREAD_MESSAGES"], "7")
            self.assertEqual(loaded, ["MAX_UNREAD_MESSAGES"])

    def test_missing_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigurationError, "does not exist"):
                load_env_file(Path(directory) / "absent")


class LockTests(unittest.TestCase):
    def test_second_run_is_refused_while_the_first_holds_the_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "var" / "triage.lock"
            with single_instance_lock(path):
                self.assertEqual(S_IMODE(path.stat().st_mode), 0o600)
                with self.assertRaises(LockBusy):
                    with single_instance_lock(path):
                        self.fail("second lock should not be granted")
            with single_instance_lock(path):
                pass


class NonInteractiveAuthTests(unittest.TestCase):
    def mailbox(self, directory: str, interactive: bool) -> GraphMailbox:
        return GraphMailbox(
            "tenant",
            "client",
            Path(directory) / "oauth_token_cache.json",
            interactive=interactive,
        )

    def test_scheduled_run_fails_fast_instead_of_prompting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mailbox = self.mailbox(directory, interactive=False)
            with patch.object(GraphMailbox, "_post_form") as post_form:
                with self.assertRaisesRegex(GraphError, "--login"):
                    mailbox.access_token()
            post_form.assert_not_called()

    def test_cached_refresh_token_is_used_without_a_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mailbox = self.mailbox(directory, interactive=False)
            mailbox._save_cache({"access_token": "old", "refresh_token": "r1", "expires_in": 0})
            with patch.object(
                GraphMailbox,
                "_post_form",
                return_value={"access_token": "fresh", "expires_in": 3600},
            ) as post_form:
                self.assertEqual(mailbox.access_token(), "fresh")
            self.assertEqual(post_form.call_count, 1)

    def test_scope_change_invalidates_the_cached_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reader = self.mailbox(directory, interactive=False)
            reader._save_cache({"access_token": "read-token", "expires_in": 3600})
            writer = GraphMailbox(
                "tenant",
                "client",
                Path(directory) / "oauth_token_cache.json",
                read_write=True,
                interactive=False,
            )
            with self.assertRaisesRegex(GraphError, "--login"):
                writer.access_token()


if __name__ == "__main__":
    unittest.main()
