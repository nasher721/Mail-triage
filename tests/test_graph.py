import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from email_triage.graph import GRAPH_ROOT, GraphError, GraphMailbox, _parse_message


class JsonResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return self.payload


class GraphParsingTests(unittest.TestCase):
    def test_graph_message_body_and_attachment_boolean_are_parsed(self):
        message = _parse_message(
            {
                "id": "example-id",
                "internetMessageId": "example-internet-id",
                "subject": "Synthetic subject",
                "from": {"emailAddress": {"name": "Alex", "address": "alex@example.org"}},
                "receivedDateTime": "2026-08-13T12:00:00Z",
                "body": {"contentType": "text", "content": "Synthetic body"},
                "sensitivity": "private",
                "hasAttachments": True,
            }
        )
        self.assertEqual(message.body, "Synthetic body")
        self.assertTrue(message.has_attachments)
        self.assertEqual(message.sensitivity, "private")


class GraphHardeningTests(unittest.TestCase):
    def mailbox(self, max_scan_pages: int = 10) -> GraphMailbox:
        return GraphMailbox(
            "tenant",
            "client",
            Path(tempfile.gettempdir()) / "unused-email-triage-token.json",
            max_scan_pages=max_scan_pages,
        )

    def test_metadata_stage_skips_processed_and_calendar_before_body_fetch(self) -> None:
        mailbox = self.mailbox()
        metadata = {
            "value": [
                {"id": "processed"},
                {"id": "calendar", "@odata.type": "#microsoft.graph.eventMessage"},
                {"id": "id/with+characters"},
            ]
        }
        detail = {
            "id": "id/with+characters",
            "subject": "Eligible",
            "from": {"emailAddress": {"address": "alex@example.org"}},
            "body": {"content": "Body"},
        }
        with patch.object(mailbox, "access_token", return_value="token"), patch.object(
            mailbox, "_request_json", side_effect=[(metadata, "token"), (detail, "token")]
        ) as request:
            messages = mailbox.unread_messages(5, {"processed"})
        self.assertEqual([message.id for message in messages], ["id/with+characters"])
        self.assertEqual(request.call_count, 2)
        body_url = request.call_args_list[1].args[0].full_url
        self.assertIn("id%2Fwith%2Bcharacters", body_url)

    def test_untrusted_pagination_origin_is_rejected(self) -> None:
        mailbox = self.mailbox()
        with patch.object(mailbox, "access_token", return_value="token"), patch.object(
            mailbox,
            "_request_json",
            return_value=({"value": [], "@odata.nextLink": "https://evil.example/messages"}, "token"),
        ):
            with self.assertRaises(GraphError) as caught:
                mailbox.unread_messages(1)
        self.assertEqual(caught.exception.code, "unsafe_next_link")

    def test_page_scan_limit_fails_boundedly(self) -> None:
        mailbox = self.mailbox(max_scan_pages=1)
        next_link = f"{GRAPH_ROOT}/me/messages?$skiptoken=next"
        with patch.object(mailbox, "access_token", return_value="token"), patch.object(
            mailbox,
            "_request_json",
            return_value=({"value": [], "@odata.nextLink": next_link}, "token"),
        ):
            with self.assertRaises(GraphError) as caught:
                mailbox.unread_messages(1)
        self.assertEqual(caught.exception.code, "scan_limit")

    def test_401_refreshes_once_and_returns_structured_error_context(self) -> None:
        mailbox = self.mailbox()
        expired = HTTPError(
            f"{GRAPH_ROOT}/me", 401, "expired", None, io.BytesIO(b"expired")
        )
        request = Request(
            f"{GRAPH_ROOT}/me", headers={"Authorization": "Bearer old-token"}
        )
        with patch("email_triage.graph.urlopen", side_effect=[expired, JsonResponse({"id": "me"})]) as open_url, patch.object(
            mailbox, "_refresh_access_token", return_value="fresh-token"
        ):
            payload, token = mailbox._request_json(
                request, token="old-token", operation="profile"
            )
        self.assertEqual(payload, {"id": "me"})
        self.assertEqual(token, "fresh-token")
        self.assertEqual(open_url.call_count, 2)
        error = GraphError("bounded", code="example", operation="test")
        self.assertEqual(error.to_dict()["code"], "example")

    def test_401_retry_transport_failure_stays_structured(self) -> None:
        mailbox = self.mailbox()
        expired = HTTPError(
            f"{GRAPH_ROOT}/me", 401, "expired", None, io.BytesIO(b"expired")
        )
        request = Request(
            f"{GRAPH_ROOT}/me", headers={"Authorization": "Bearer old-token"}
        )
        with patch(
            "email_triage.graph.urlopen",
            side_effect=[expired, URLError("offline")],
        ), patch.object(
            mailbox, "_refresh_access_token", return_value="fresh-token"
        ):
            with self.assertRaises(GraphError) as caught:
                mailbox._request_json(
                    request, token="old-token", operation="profile"
                )
        self.assertEqual(caught.exception.code, "retry_transport_error")
        self.assertTrue(caught.exception.retryable)

    def test_duplicate_exact_folder_names_fail_closed(self) -> None:
        mailbox = GraphMailbox(
            "tenant", "client", Path("unused"), read_write=True
        )
        duplicate = {
            "value": [
                {"id": "one", "displayName": "AI Triage"},
                {"id": "two", "displayName": "AI Triage"},
            ]
        }
        with patch.object(mailbox, "_json_request", return_value=duplicate):
            with self.assertRaises(GraphError) as caught:
                mailbox.ensure_folder_path("AI Triage")
        self.assertEqual(caught.exception.code, "ambiguous_folder")


if __name__ == "__main__":
    unittest.main()
