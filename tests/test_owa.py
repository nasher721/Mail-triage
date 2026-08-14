import unittest
from unittest.mock import patch
from urllib.error import URLError

from email_triage.config import ConfigurationError, Settings
from email_triage.graph import GraphError
from email_triage.owa import (
    CapturedAuth,
    OwaMailbox,
    OUTLOOK_REST_ROOT,
    _auth_from_page,
    capture_edge_auth,
    edge_debug_available,
)


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
    def test_token_capture_routes_outlook_token_to_matching_api(self) -> None:
        class FakeRequest:
            def __init__(self, url: str, token: str):
                self.url = url
                self.headers = {"Authorization": f"Bearer {token}"}

        class FakeLocator:
            def __init__(self, page: "FakePage"):
                self.page = page

            @property
            def first(self) -> "FakeLocator":
                return self

            def count(self) -> int:
                return 1

            def click(self, timeout: int) -> None:
                del timeout
                for request in self.page.requests:
                    self.page.handler(request)

        class FakePage:
            def __init__(self):
                self.requests = [
                    FakeRequest(
                        "https://outlook.office.com/owa/service.svc",
                        "outlook-token-with-realistic-length",
                    ),
                    FakeRequest(
                        "https://graph.microsoft.com/v1.0/me/messages",
                        "graph-token-with-realistic-length",
                    ),
                ]
                self.handler = lambda request: None

            def on(self, event: str, handler) -> None:
                self.assert_event = event
                self.handler = handler

            def locator(self, selector: str) -> FakeLocator:
                del selector
                return FakeLocator(self)

            def wait_for_timeout(self, timeout: int) -> None:
                del timeout

        auth = _auth_from_page(FakePage())
        self.assertIsNotNone(auth)
        self.assertEqual(auth.token, "outlook-token-with-realistic-length")
        self.assertEqual(auth.api_root, OUTLOOK_REST_ROOT)

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

    def test_outlook_rest_messages_are_normalized(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=False)
        mailbox._auth = CapturedAuth("synthetic-token", OUTLOOK_REST_ROOT)
        payload = {
            "value": [
                {
                    "Id": "example-id",
                    "InternetMessageId": "example-internet-id",
                    "Subject": "Synthetic subject",
                    "From": {
                        "EmailAddress": {"Name": "Alex", "Address": "alex@example.org"}
                    },
                    "ReceivedDateTime": "2026-08-13T12:00:00Z",
                    "Body": {"ContentType": "Text", "Content": "Synthetic body"},
                    "Sensitivity": "Normal",
                    "HasAttachments": False,
                    "IsRead": False,
                }
            ]
        }
        with patch.object(mailbox, "_json_request", return_value=payload) as request:
            messages = mailbox.unread_messages(5)
        self.assertEqual(messages[0].sender_address, "alex@example.org")
        self.assertEqual(messages[0].body, "Synthetic body")
        self.assertIn("IsRead+eq+false", request.call_args.args[1])
        self.assertNotIn("Sensitivity", request.call_args.args[1])

    def test_outlook_rest_writes_use_pascal_case(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=True)
        mailbox._auth = CapturedAuth("synthetic-token", OUTLOOK_REST_ROOT)
        with patch.object(mailbox, "_json_request", return_value={}) as request:
            mailbox.update_message("example-id", categories=("Needs Reply",), is_read=True)
        self.assertEqual(
            request.call_args.args[2],
            {"Categories": ["Needs Reply"], "IsRead": True},
        )

    def test_writes_require_apply(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=False)
        mailbox._auth = CapturedAuth("synthetic-token")
        with self.assertRaisesRegex(GraphError, "--apply"):
            mailbox.update_message("example-id", is_read=True)


if __name__ == "__main__":
    unittest.main()
