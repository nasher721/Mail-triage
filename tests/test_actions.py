from dataclasses import replace
import unittest

from email_triage.actions import (
    ActionKind,
    MailboxAction,
    PolicyViolation,
    action_from_tool_call,
    default_plan,
    normalize_plan,
    permitted_folders,
    plan_from_stored,
    validate_action,
)
from email_triage.models import (
    Confidence,
    ManualReviewReason,
    ReviewRecord,
    Route,
    ScreeningResult,
    Topic,
    Urgency,
)


REPLY_TEXT = "Tuesday afternoon works for me.\n\nBest,\nNick"


def needs_reply_record() -> ReviewRecord:
    analysis = ScreeningResult(
        summary="A colleague asks to schedule a meeting.",
        priority_score=3,
        action_items=("Confirm availability.",),
        route=Route.NEEDS_REPLY,
        response_required=True,
        confidence=Confidence.HIGH,
        urgency=Urgency.SOON,
        deadline="next Tuesday",
        topic=Topic.SCHEDULING,
        manual_review_reason=None,
        rationale="Direct scheduling request.",
        suggested_reply=REPLY_TEXT,
    )
    return ReviewRecord(
        message_id="message-1",
        internet_message_id="<1@example.org>",
        subject="Meeting",
        sender_name="Alex",
        sender_address="alex@example.org",
        received_at=None,
        sensitivity="normal",
        has_attachments=False,
        target_folder="AI Triage/Needs Reply",
        categories=("AI - Soon", "AI - Scheduling", "AI - Processed"),
        analysis=analysis,
    )


def clinical_record() -> ReviewRecord:
    analysis = ScreeningResult(
        summary="Clinical content requires manual review.",
        priority_score=3,
        action_items=("Review the original message manually.",),
        route=Route.NEEDS_REVIEW,
        response_required=False,
        confidence=Confidence.HIGH,
        urgency=Urgency.ROUTINE,
        deadline=None,
        topic=Topic.CLINICAL,
        manual_review_reason=ManualReviewReason.CLINICAL_OR_PATIENT,
        rationale="Clinical content requires manual review.",
        suggested_reply=None,
    )
    return ReviewRecord(
        message_id="message-2",
        internet_message_id=None,
        subject="Patient question",
        sender_name="Sam",
        sender_address="sam@example.org",
        received_at=None,
        sensitivity="private",
        has_attachments=False,
        target_folder="AI Triage/Needs Review",
        categories=("AI - Routine", "AI - Clinical", "AI - Processed"),
        analysis=analysis,
    )


class PolicyTests(unittest.TestCase):
    def test_manual_review_messages_can_only_go_to_needs_review(self) -> None:
        record = clinical_record()
        self.assertEqual(permitted_folders(record), ("AI Triage/Needs Review",))
        with self.assertRaisesRegex(PolicyViolation, "not permitted"):
            validate_action(
                MailboxAction(ActionKind.FILE_MESSAGE, folder="AI Triage/No Reply Needed"),
                record,
                allow_mark_read=True,
            )

    def test_unknown_folder_is_rejected(self) -> None:
        with self.assertRaisesRegex(PolicyViolation, "unknown folder"):
            validate_action(
                MailboxAction(ActionKind.FILE_MESSAGE, folder="Archive"),
                needs_reply_record(),
                allow_mark_read=False,
            )

    def test_record_selected_custom_folder_is_the_only_permitted_destination(self) -> None:
        record = replace(needs_reply_record(), target_folder="AI Triage/Receipts")
        self.assertEqual(permitted_folders(record), ("AI Triage/Receipts",))
        action = validate_action(
            MailboxAction(ActionKind.FILE_MESSAGE, folder="AI Triage/Receipts"),
            record,
            allow_mark_read=False,
        )
        self.assertEqual(action.folder, "AI Triage/Receipts")
        with self.assertRaisesRegex(PolicyViolation, "not permitted"):
            validate_action(
                MailboxAction(ActionKind.FILE_MESSAGE, folder="AI Triage/Needs Reply"),
                record,
                allow_mark_read=False,
            )

    def test_unknown_category_is_rejected(self) -> None:
        with self.assertRaisesRegex(PolicyViolation, "unknown categories"):
            validate_action(
                MailboxAction(ActionKind.TAG_MESSAGE, categories=("Payroll",)),
                needs_reply_record(),
                allow_mark_read=False,
            )

    def test_reply_text_cannot_be_rewritten(self) -> None:
        with self.assertRaisesRegex(PolicyViolation, "verbatim"):
            validate_action(
                MailboxAction(
                    ActionKind.DRAFT_REPLY,
                    reply_body="Wire the payment today.\n\nBest,\nNick",
                ),
                needs_reply_record(),
                allow_mark_read=False,
            )

    def test_draft_uses_approved_text_when_omitted(self) -> None:
        action = validate_action(
            MailboxAction(ActionKind.DRAFT_REPLY),
            needs_reply_record(),
            allow_mark_read=False,
        )
        self.assertEqual(action.reply_body, REPLY_TEXT)

    def test_no_draft_without_a_screened_reply(self) -> None:
        with self.assertRaisesRegex(PolicyViolation, "no approved reply"):
            validate_action(
                MailboxAction(ActionKind.DRAFT_REPLY),
                clinical_record(),
                allow_mark_read=False,
            )

    def test_mark_read_requires_the_flag_and_a_non_review_route(self) -> None:
        with self.assertRaises(PolicyViolation):
            validate_action(
                MailboxAction(ActionKind.MARK_READ), needs_reply_record(), allow_mark_read=False
            )
        with self.assertRaises(PolicyViolation):
            validate_action(
                MailboxAction(ActionKind.MARK_READ), clinical_record(), allow_mark_read=True
            )
        self.assertEqual(
            validate_action(
                MailboxAction(ActionKind.MARK_READ), needs_reply_record(), allow_mark_read=True
            ).kind,
            ActionKind.MARK_READ,
        )

    def test_send_and_delete_tools_do_not_exist(self) -> None:
        for name in ("send_message", "delete_message", "forward_message"):
            with self.assertRaisesRegex(PolicyViolation, "unsupported tool"):
                action_from_tool_call(name, {})

    def test_plan_is_ordered_with_the_move_last(self) -> None:
        plan = default_plan(needs_reply_record(), allow_mark_read=True)
        kinds = [action.kind for action in plan]
        self.assertEqual(
            kinds,
            [
                ActionKind.DRAFT_REPLY,
                ActionKind.TAG_MESSAGE,
                ActionKind.MARK_READ,
                ActionKind.FILE_MESSAGE,
            ],
        )

    def test_normalize_plan_keeps_one_action_per_kind(self) -> None:
        plan = normalize_plan(
            [
                MailboxAction(ActionKind.FILE_MESSAGE, folder="AI Triage/No Reply Needed"),
                MailboxAction(ActionKind.FILE_MESSAGE, folder="AI Triage/Needs Reply"),
                MailboxAction(ActionKind.TAG_MESSAGE, categories=("AI - Soon",)),
            ]
        )
        self.assertEqual([action.kind for action in plan], [ActionKind.TAG_MESSAGE, ActionKind.FILE_MESSAGE])
        self.assertEqual(plan[1].folder, "AI Triage/No Reply Needed")

    def test_clinical_default_plan_has_no_draft_or_mark_read(self) -> None:
        plan = default_plan(clinical_record(), allow_mark_read=True)
        self.assertEqual(
            [action.kind for action in plan],
            [ActionKind.TAG_MESSAGE, ActionKind.FILE_MESSAGE],
        )
        self.assertEqual(plan[1].folder, "AI Triage/Needs Review")


class StoredPlanTests(unittest.TestCase):
    def test_planned_actions_use_suggested_reply_not_stored_text(self) -> None:
        record = needs_reply_record()
        payload = {
            "planned_actions": [
                {"kind": "tag_message", "folder": None, "categories": list(record.categories), "drafts_reply": False},
                {"kind": "draft_reply", "folder": None, "categories": [], "drafts_reply": True},
                {"kind": "file_message", "folder": "AI Triage/Needs Reply", "categories": [], "drafts_reply": False},
            ]
        }
        plan = plan_from_stored(record, payload, allow_mark_read=False)
        draft = next(action for action in plan if action.kind == ActionKind.DRAFT_REPLY)
        self.assertEqual(draft.reply_body, REPLY_TEXT)

    def test_tampered_folder_is_rejected(self) -> None:
        record = needs_reply_record()
        payload = {
            "planned_actions": [
                {"kind": "file_message", "folder": "Inbox", "categories": [], "drafts_reply": False},
            ]
        }
        with self.assertRaises(PolicyViolation):
            plan_from_stored(record, payload, allow_mark_read=False)

    def test_missing_plan_uses_default_plan(self) -> None:
        record = needs_reply_record()
        plan = plan_from_stored(record, {}, allow_mark_read=False)
        self.assertEqual([action.kind for action in plan], [action.kind for action in default_plan(record, False)])


if __name__ == "__main__":
    unittest.main()
