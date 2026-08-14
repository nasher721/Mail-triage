import unittest

from email_triage.graph import _parse_message


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


if __name__ == "__main__":
    unittest.main()
