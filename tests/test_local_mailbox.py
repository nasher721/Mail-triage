import tempfile
import unittest
from pathlib import Path

from email_triage.local_mailbox import LocalMailbox


class LocalMailboxTests(unittest.TestCase):
    def test_jsonl_messages_are_loaded_without_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inbox.jsonl"
            path.write_text(
                '{"id": "m1", "subject": "Meet?", "sender_address": "alex@example.org", '
                '"body": "Can we meet next Tuesday afternoon?"}\n',
                encoding="utf-8",
            )
            messages = LocalMailbox(path).unread_messages(20)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].id, "m1")
        self.assertEqual(messages[0].body, "Can we meet next Tuesday afternoon?")

    def test_eml_file_is_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.eml"
            path.write_text(
                "From: Alex <alex@example.org>\n"
                "Subject: Status\n"
                "Message-ID: <note@example.org>\n\n"
                "This is informational only.\n",
                encoding="utf-8",
            )
            messages = LocalMailbox(path).unread_messages(20)
        self.assertEqual(messages[0].subject, "Status")
        self.assertEqual(messages[0].sender_address, "alex@example.org")
        self.assertIn("informational", messages[0].body)


if __name__ == "__main__":
    unittest.main()
