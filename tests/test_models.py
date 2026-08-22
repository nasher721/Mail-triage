import unittest
from datetime import datetime, timezone

from email_triage.models import Confidence, ReviewRecord, Route, ScreeningResult, Topic, Urgency


class ModelTests(unittest.TestCase):
    def valid(self, **overrides):
        values = {
            "summary": "A routine informational update.",
            "priority_score": 1,
            "action_items": [],
            "route": "no_reply",
            "response_required": False,
            "confidence": "high",
            "urgency": "routine",
            "deadline": None,
            "topic": "administrative",
            "manual_review_reason": None,
            "rationale": "Informational only.",
            "suggested_reply": None,
        }
        values.update(overrides)
        return values

    def test_extra_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            ScreeningResult.from_dict(self.valid(unexpected="value"))

    def test_reply_signature_is_required(self):
        with self.assertRaises(ValueError):
            ScreeningResult.from_dict(
                self.valid(route="needs_reply", response_required=True, suggested_reply="Thanks.")
            )

    def test_non_reply_route_cannot_contain_reply(self):
        with self.assertRaises(ValueError):
            ScreeningResult.from_dict(self.valid(suggested_reply="Best,\nNick"))

    def test_valid_contract_is_typed(self):
        result = ScreeningResult.from_dict(self.valid())
        self.assertEqual(result.route, Route.NO_REPLY)
        self.assertEqual(result.confidence, Confidence.HIGH)
        self.assertEqual(result.urgency, Urgency.ROUTINE)
        self.assertEqual(result.topic, Topic.ADMINISTRATIVE)


class ReviewRecordRoundTripTests(unittest.TestCase):
    def test_from_dict_reloads_to_dict_payload(self) -> None:
        record = ReviewRecord(
            message_id="message-1",
            internet_message_id="<1@example.org>",
            subject="Meeting",
            sender_name="Alex",
            sender_address="alex@example.org",
            received_at=datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc),
            sensitivity="normal",
            has_attachments=False,
            target_folder="AI Triage/Needs Reply",
            categories=("AI - Soon", "AI - Scheduling", "AI - Processed"),
            analysis=ScreeningResult(
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
                suggested_reply="Thanks.\n\nBest,\nNick",
            ),
            unsubscribe_suggestion=False,
            processing_error=None,
        )
        loaded = ReviewRecord.from_dict(record.to_dict())
        self.assertEqual(loaded.message_id, "message-1")
        self.assertEqual(loaded.target_folder, "AI Triage/Needs Reply")
        self.assertEqual(loaded.analysis.route, Route.NEEDS_REPLY)
        self.assertEqual(loaded.analysis.suggested_reply, "Thanks.\n\nBest,\nNick")
        self.assertEqual(loaded.categories, ("AI - Soon", "AI - Scheduling", "AI - Processed"))
        self.assertEqual(loaded.received_at, record.received_at)

    def test_from_dict_rejects_non_object(self) -> None:
        with self.assertRaises(ValueError):
            ReviewRecord.from_dict([])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
