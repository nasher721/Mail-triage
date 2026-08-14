import unittest
from unittest.mock import patch
from urllib.error import URLError

from email_triage.config import ConfigurationError, Settings
from email_triage.graph import GraphError
from email_triage.owa import CapturedAuth, OwaMailbox, capture_edge_auth, edge_debug_available


class OwaConfigTests(unittest.TestCase):
    def test_owa_source_does_not_need_entra_app(self) -> None:
        environment = {
            "TRIAGE_BACKEND": "ollama",
            "TRIAGE_SOURCE": "owa",
            "OLLAMA_HOST": "http://127.0.0.1:11434",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.mailbox_source, "owa")
        self.assertEqual(settings.owa_cdp_url, "http://127.0.0.1:9222")
        self.assertEqual(settings.tenant_id, "")

    def test_apply_is_allowed_with_owa(self) -> None:
        environment = {
            "TRIAGE_BACKEND": "ollama",
            "TRIAGE_SOURCE": "owa",
            "OLLAMA_HOST": "http://127.0.0.1:11434",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env(apply_changes=True)
        self.assertTrue(settings.apply_changes)
        self.assertEqual(settings.mailbox_source, "owa")

    def test_apply_still_rejected_for_local_files(self) -> None:
        environment = {
            "TRIAGE_BACKEND": "ollama",
            "TRIAGE_INPUT": "samples/inbox.jsonl",
        }
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "--owa --apply"):
                Settings.from_env(apply_changes=True)

    def test_cli_source_flag_selects_owa(self) -> None:
        environment = {
            "TRIAGE_BACKEND": "ollama",
            "OLLAMA_HOST": "http://127.0.0.1:11434",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env(source="owa")
        self.assertEqual(settings.mailbox_source, "owa")


class OwaMailboxTests(unittest.TestCase):
    def test_missing_debug_port_explains_edge_relaunch(self) -> None:
        with patch("email_triage.owa.urlopen", side_effect=URLError("down")):
            self.assertFalse(edge_debug_available("http://127.0.0.1:9222"))
            with self.assertRaisesRegex(ConfigurationError, "open_outlook_in_edge"):
                capture_edge_auth("http://127.0.0.1:9222")

    def test_unread_messages_use_the_captured_session(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=False)
        mailbox._auth = CapturedAuth("synthetic-token")
        payload = {
            "value": [
                {
                    "id": "example-id",
                    "internetMessageId": "example-internet-id",
                    "subject": "Synthetic subject",
                    "from": {"emailAddress": {"name": "Alex", "address": "alex@example.org"}},
                    "receivedDateTime": "2026-08-13T12:00:00Z",
                    "body": {"contentType": "text", "content": "Synthetic body"},
                    "sensitivity": "normal",
                    "hasAttachments": False,
                }
            ]
        }
        with patch.object(mailbox, "_json_request", return_value=payload) as request:
            messages = mailbox.unread_messages(5)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].id, "example-id")
        self.assertIn("mailFolders/inbox/messages", request.call_args.args[1])

    def test_writes_require_apply(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=False)
        mailbox._auth = CapturedAuth("synthetic-token")
        with self.assertRaisesRegex(GraphError, "--apply"):
            mailbox.update_message("example-id", is_read=True)


if __name__ == "__main__":
    unittest.main()
