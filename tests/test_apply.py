import json
import tempfile
import unittest
from pathlib import Path
from stat import S_IMODE

from email_triage.actions import ActionKind, default_plan
from email_triage.apply import ActionLog, DryRunActuator, GraphActuator, apply_plan
from email_triage.graph import GraphError, _text_to_html
from test_actions import needs_reply_record


class FakeGraphMailbox:
    def __init__(self, fail_on: str | None = None, error: GraphError | None = None):
        self.fail_on = fail_on
        self.error = error
        self.calls: list[tuple] = []
        self.folders = {"AI Triage/Needs Reply": "folder-1"}

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            if self.error is not None:
                raise self.error
            raise GraphError(f"synthetic {name} failure")

    def create_reply_draft(self, message_id: str, reply_text: str) -> str:
        self._maybe_fail("create_reply_draft")
        self.calls.append(("create_reply_draft", message_id, reply_text))
        return "draft-1"

    def update_message(self, message_id, categories=None, is_read=None) -> None:
        self._maybe_fail("update_message")
        self.calls.append(("update_message", message_id, categories, is_read))

    def ensure_folder_path(self, folder_path: str) -> str:
        self._maybe_fail("ensure_folder_path")
        self.calls.append(("ensure_folder_path", folder_path))
        return self.folders.get(folder_path, "folder-new")

    def move_message(self, message_id: str, folder_id: str) -> str:
        self._maybe_fail("move_message")
        self.calls.append(("move_message", message_id, folder_id))
        return "moved-id"


class ApplyTests(unittest.TestCase):
    def test_dry_run_touches_nothing(self) -> None:
        record = needs_reply_record()
        actuator = DryRunActuator()
        applied = apply_plan(record, default_plan(record, allow_mark_read=True), actuator)
        self.assertEqual(len(applied), 4)
        self.assertTrue(all(item.status == "planned" for item in applied))
        self.assertEqual(len(actuator.calls), 4)

    def test_graph_actuator_drafts_before_moving(self) -> None:
        record = needs_reply_record()
        mailbox = FakeGraphMailbox()
        applied = apply_plan(
            record, default_plan(record, allow_mark_read=True), GraphActuator(mailbox)
        )
        self.assertTrue(all(item.status == "applied" for item in applied))
        order = [call[0] for call in mailbox.calls]
        self.assertEqual(
            order,
            ["create_reply_draft", "update_message", "update_message", "ensure_folder_path", "move_message"],
        )
        self.assertLess(order.index("create_reply_draft"), order.index("move_message"))

    def test_failure_stops_the_remaining_plan(self) -> None:
        record = needs_reply_record()
        mailbox = FakeGraphMailbox(
            fail_on="create_reply_draft",
            error=GraphError("message gone", status=404, code="ErrorItemNotFound"),
        )
        applied = apply_plan(
            record, default_plan(record, allow_mark_read=False), GraphActuator(mailbox)
        )
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].status, "failed")
        self.assertEqual(mailbox.calls, [])

    def test_backend_graph_error_is_not_swallowed(self) -> None:
        record = needs_reply_record()
        mailbox = FakeGraphMailbox(
            fail_on="create_reply_draft",
            error=GraphError("session expired", status=401, operation="POST"),
        )
        with self.assertRaises(GraphError) as caught:
            apply_plan(
                record, default_plan(record, allow_mark_read=False), GraphActuator(mailbox)
            )
        self.assertEqual(caught.exception.status, 401)

    def test_moved_identifier_is_reused_for_later_actions(self) -> None:
        record = needs_reply_record()
        actuator = GraphActuator(FakeGraphMailbox())
        actuator.execute(record.message_id, default_plan(record, allow_mark_read=False)[-1])
        self.assertEqual(actuator._resolve(record.message_id), "moved-id")

    def test_reply_html_escapes_untrusted_text(self) -> None:
        self.assertEqual(
            _text_to_html("a <b>x</b>\nBest,\nNick"),
            "<div>a &lt;b&gt;x&lt;/b&gt;<br>Best,<br>Nick</div>",
        )

    def test_action_log_is_owner_only_and_stores_no_body(self) -> None:
        record = needs_reply_record()
        with tempfile.TemporaryDirectory() as directory:
            log = ActionLog(Path(directory) / "var")
            applied = apply_plan(record, default_plan(record, allow_mark_read=False), DryRunActuator())
            log.append(record, "agent", "dry-run", applied)
            entry = json.loads(log.path.read_text(encoding="utf-8").strip())
            self.assertEqual(S_IMODE(log.path.stat().st_mode), 0o600)
        self.assertEqual(entry["plan_source"], "agent")
        self.assertNotIn("body", entry)
        self.assertEqual(entry["actions"][0]["kind"], str(ActionKind.DRAFT_REPLY))
        self.assertNotIn("Best,", json.dumps(entry))


if __name__ == "__main__":
    unittest.main()
