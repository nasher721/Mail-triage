import json
import tempfile
import unittest
from pathlib import Path
from stat import S_IMODE
from email_triage.actions import default_plan
from email_triage.apply import GraphActuator
from email_triage.config import ConfigurationError
from email_triage.pipeline import LocalQueue
from dataclasses import replace

from email_triage.selected import MAX_APPLY_IDS, apply_selected, load_apply_ids
from test_actions import needs_reply_record
from test_apply import FakeGraphMailbox


class LoadApplyIdsTests(unittest.TestCase):
    def test_loads_owner_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ids.json"
            path.write_text('{"message_ids": ["a", "b"]}\n', encoding="utf-8")
            self.assertEqual(load_apply_ids(path), ("a", "b"))

    def test_empty_or_too_many_or_malformed_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ids.json"
            path.write_text('{"message_ids": []}\n', encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_apply_ids(path)
            path.write_text('{"message_ids": %s}\n' % json.dumps(["x"] * (MAX_APPLY_IDS + 1)), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_apply_ids(path)
            path.write_text("[]\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_apply_ids(path)
            with self.assertRaises(ConfigurationError):
                load_apply_ids(Path(directory) / "missing.json")


class ApplySelectedTests(unittest.TestCase):
    def test_actuates_only_listed_ids_without_classifier(self) -> None:
        first = needs_reply_record()
        second = replace(needs_reply_record(), message_id="message-2")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            queue = LocalQueue(output)
            for record in (first, second):
                extra = {
                    "plan_source": "deterministic",
                    "planned_actions": [action.to_dict() for action in default_plan(record, False)],
                    "actions": [],
                }
                queue.append(record, extra=extra)
            mailbox = FakeGraphMailbox()
            emitted, failures = apply_selected(
                output_dir=output,
                ids=("message-1",),
                actuator=GraphActuator(mailbox),
                mark_read=False,
            )
        self.assertEqual((emitted, failures), (1, 0))
        self.assertTrue(any(call[0] == "move_message" for call in mailbox.calls))
        moved_ids = [call[1] for call in mailbox.calls if call[0] == "move_message"]
        self.assertEqual(moved_ids, ["message-1"])

    def test_missing_id_fails_and_continues(self) -> None:
        record = needs_reply_record()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            LocalQueue(output).append(
                record,
                extra={
                    "plan_source": "deterministic",
                    "planned_actions": [action.to_dict() for action in default_plan(record, False)],
                },
            )
            mailbox = FakeGraphMailbox()
            emitted, failures = apply_selected(
                output_dir=output,
                ids=("message-1", "missing"),
                actuator=GraphActuator(mailbox),
                mark_read=False,
            )
        self.assertEqual(emitted, 2)
        self.assertEqual(failures, 1)

    def test_mailbox_404_fails_that_row(self) -> None:
        record = needs_reply_record()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            LocalQueue(output).append(
                record,
                extra={
                    "plan_source": "deterministic",
                    "planned_actions": [action.to_dict() for action in default_plan(record, False)],
                },
            )
            mailbox = FakeGraphMailbox(fail_on="ensure_folder_path")
            emitted, failures = apply_selected(
                output_dir=output,
                ids=("message-1",),
                actuator=GraphActuator(mailbox),
                mark_read=False,
            )
        self.assertEqual((emitted, failures), (1, 1))

    def test_does_not_rewrite_review_queue(self) -> None:
        record = needs_reply_record()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            queue = LocalQueue(output)
            queue.append(record, extra={"plan_source": "deterministic"})
            before = queue.queue_path.read_text(encoding="utf-8")
            apply_selected(
                output_dir=output,
                ids=("message-1",),
                actuator=GraphActuator(FakeGraphMailbox()),
                mark_read=False,
            )
            self.assertEqual(queue.queue_path.read_text(encoding="utf-8"), before)
            self.assertTrue((output / "applied_actions.jsonl").is_file())
            self.assertEqual(S_IMODE((output / "applied_actions.jsonl").stat().st_mode), 0o600)

