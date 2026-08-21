import io
import base64
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from email_triage.config import ConfigurationError, Settings
from email_triage.graph import GraphError
from email_triage.owa import (
    CapturedAuth,
    CapturedCookie,
    OwaMailbox,
    OUTLOOK_REST_ROOT,
    _auth_from_page,
    _anchor_from_token,
    _capture_cookies,
    capture_edge_auth,
    edge_debug_available,
    _root_for_url,
    _session_headers,
)


class OwaConfigTests(unittest.TestCase):
    def test_edge_helper_uses_dedicated_profile_without_quitting_browser(self) -> None:
        root = Path(__file__).resolve().parent
        if root.name == "tests":
            root = root.parent
        script_path = root / "scripts" / "open_outlook_in_edge.sh"
        if not script_path.exists():
            return
        script = script_path.read_text(encoding="utf-8")
        self.assertIn("--user-data-dir", script)
        self.assertIn("--remote-debugging-address=127.0.0.1", script)
        self.assertIn('"$EDGE_BIN_PATH" \\', script)
        self.assertIn("for _ in {1..60}; do", script)
        self.assertIn("OWA_URL must be HTTPS on an approved Outlook host", script)
        self.assertIn("EDGE_PROFILE_DIR must not be a symbolic link", script)
        self.assertIn("Library/Application Support/MailTriage/EdgeProfile", script)
        self.assertIn(".mail-triage-cdp-owner", script)
        self.assertIn("Refusing an unrecognized process", script)
        self.assertNotIn("TRIAGE_OUTPUT_DIR", script)
        self.assertNotIn('tell application "Microsoft Edge" quit', script)

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

    def test_owa_source_rejects_remote_browser_debugging_endpoint(self) -> None:
        environment = {
            "TRIAGE_BACKEND": "ollama",
            "TRIAGE_SOURCE": "owa",
            "OLLAMA_HOST": "http://127.0.0.1:11434",
            "EDGE_CDP_URL": "http://remote.example:9222",
        }
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "loopback endpoint"):
                Settings.from_env()


class OwaMailboxTests(unittest.TestCase):
    def test_normal_owa_path_rejects_remote_browser_debugging_endpoint(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "loopback endpoint"):
            OwaMailbox("http://remote.example:9222")
        with patch("email_triage.owa.urlopen") as opener:
            self.assertFalse(edge_debug_available("http://remote.example:9222"))
            opener.assert_not_called()
        with self.assertRaisesRegex(ConfigurationError, "loopback endpoint"):
            capture_edge_auth("http://remote.example:9222")

    def test_browser_cookie_capture_keeps_only_outlook_rest_domains(self) -> None:
        class Context:
            @staticmethod
            def cookies():
                return [
                    {"name": "allowed", "value": "one", "domain": ".outlook.office.com"},
                    {"name": "login", "value": "two", "domain": "login.microsoftonline.com"},
                ]

        class Page:
            context = Context()

        self.assertEqual(
            _capture_cookies(Page()),
            (CapturedCookie("allowed", "one", ".outlook.office.com"),),
        )

    def test_outlook_rest_replay_includes_only_allowlisted_session_headers(self) -> None:
        auth = CapturedAuth(
            "synthetic-token",
            OUTLOOK_REST_ROOT,
            anchor_mailbox="PUID:object@tenant",
            cookies=(
                CapturedCookie("good", "allowed-value", ".outlook.office.com"),
                CapturedCookie("evil", "blocked-value", "evil.example"),
            ),
        )
        headers = _session_headers(auth)
        self.assertEqual(headers["X-AnchorMailbox"], "PUID:object@tenant")
        self.assertEqual(headers["Cookie"], "good=allowed-value")
        self.assertNotIn("blocked-value", json.dumps(headers))
        self.assertNotIn("synthetic-token", repr(auth))
        self.assertNotIn("allowed-value", repr(auth))

    def test_outlook_rest_replay_drops_header_injection_cookie_material(self) -> None:
        auth = CapturedAuth(
            "synthetic-token",
            OUTLOOK_REST_ROOT,
            cookies=(
                CapturedCookie("safe", "allowed", ".outlook.office.com"),
                CapturedCookie("bad\r\nInjected", "blocked", ".outlook.office.com"),
                CapturedCookie(
                    "also-bad", "blocked\r\nInjected: yes", ".outlook.office.com"
                ),
            ),
        )

        headers = _session_headers(auth)

        self.assertEqual(headers["Cookie"], "safe=allowed")

    def test_anchor_mailbox_is_derived_from_browser_token_in_memory(self) -> None:
        payload = base64.urlsafe_b64encode(
            json.dumps({"oid": "object", "tid": "tenant"}).encode("utf-8")
        ).decode("ascii").rstrip("=")
        self.assertEqual(
            _anchor_from_token(f"header.{payload}.signature"),
            "PUID:object@tenant",
        )

    def test_only_exact_approved_https_origins_are_accepted(self) -> None:
        self.assertEqual(
            _root_for_url("https://outlook.office365.com/owa/service.svc"),
            OUTLOOK_REST_ROOT,
        )
        self.assertIsNone(_root_for_url("http://outlook.office.com/owa"))
        self.assertIsNone(_root_for_url("https://outlook.office.com.evil.example/owa"))
        self.assertIsNone(_root_for_url("https://user@outlook.office.com/owa"))

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
        self.assertNotIn("%40odata.type", request.call_args.args[1])

    def test_outlook_rest_writes_use_pascal_case(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=True)
        mailbox._auth = CapturedAuth("synthetic-token", OUTLOOK_REST_ROOT)
        with patch.object(mailbox, "_json_request", return_value={}) as request:
            mailbox.update_message("example-id", categories=("Needs Reply",), is_read=True)
        self.assertEqual(
            request.call_args.args[2],
            {"Categories": ["Needs Reply"], "IsRead": True},
        )

    def test_unread_messages_401_refreshes_token_once(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=False)
        mailbox._auth = CapturedAuth("old-token")

        first_err = HTTPError(
            url="https://graph.microsoft.com/v1.0/me/messages?$top=1",
            code=401,
            msg="unauthorized",
            hdrs=None,
            fp=io.BytesIO(b"expired"),
        )

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

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        with patch("email_triage.owa.capture_edge_auth") as refresh, patch(
            "email_triage.owa.urlopen", side_effect=[first_err, FakeResponse()]
        ) as request:
            refresh.return_value = CapturedAuth("new-token")
            messages = mailbox.unread_messages(5)

        self.assertEqual(len(messages), 1)
        self.assertEqual(refresh.call_count, 1)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(messages[0].id, "example-id")


    def test_metadata_stage_skips_processed_and_calendar_before_body_fetch(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=False)
        mailbox._auth = CapturedAuth("synthetic-token")
        metadata = {
            "value": [
                {"id": "processed"},
                {"id": "calendar", "@odata.type": "#microsoft.graph.eventMessage"},
                {"id": "eligible"},
            ]
        }
        detail = {
            "id": "eligible",
            "subject": "Eligible",
            "from": {"emailAddress": {"address": "alex@example.org"}},
            "body": {"content": "Body"},
        }
        with patch.object(
            mailbox, "_json_request", side_effect=[metadata, detail]
        ) as request:
            messages = mailbox.unread_messages(5, {"processed"})
        self.assertEqual([message.id for message in messages], ["eligible"])
        self.assertEqual(request.call_count, 2)

    def test_untrusted_pagination_origin_is_rejected(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=False)
        mailbox._auth = CapturedAuth("synthetic-token")
        payload = {
            "value": [],
            "@odata.nextLink": "https://evil.example/messages",
        }
        with patch.object(mailbox, "_json_request", return_value=payload):
            with self.assertRaisesRegex(GraphError, "approved API URL"):
                mailbox.unread_messages(1)

    def test_page_scan_limit_is_reported(self) -> None:
        mailbox = OwaMailbox(
            "http://127.0.0.1:9222", read_write=False, max_scan_pages=1
        )
        mailbox._auth = CapturedAuth("synthetic-token")
        payload = {
            "value": [],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=next",
        }
        with patch.object(mailbox, "_json_request", return_value=payload):
            with self.assertRaises(GraphError) as caught:
                mailbox.unread_messages(1)
        self.assertEqual(caught.exception.code, "scan_limit")

    def test_duplicate_exact_folder_names_fail_closed(self) -> None:
        mailbox = OwaMailbox("http://127.0.0.1:9222", read_write=True)
        mailbox._auth = CapturedAuth("synthetic-token")
        duplicate = {
            "value": [
                {"id": "one", "displayName": "AI Triage"},
                {"id": "two", "displayName": "AI Triage"},
            ]
        }
        with patch.object(mailbox, "_json_request", return_value=duplicate):
            with self.assertRaises(GraphError) as caught:
                mailbox.ensure_folder_path("AI Triage")
        self.assertIn("ambiguous", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
