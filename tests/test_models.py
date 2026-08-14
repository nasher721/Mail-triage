import unittest

from email_triage.models import Confidence, Route, ScreeningResult, Topic, Urgency


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


if __name__ == "__main__":
    unittest.main()
