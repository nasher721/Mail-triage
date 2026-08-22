import io
import json
import unittest
from unittest.mock import patch

from email_triage.classifier import (
    ClassificationError,
    ProviderClassifier,
    build_classifier,
)
from email_triage.config import Settings
from email_triage.models import Route
from email_triage.providers import build_client, provider_profile, ProviderSelection


RESULT = {
    "summary": "A colleague asks to schedule a meeting.",
    "priority_score": 3,
    "action_items": ["Confirm availability."],
    "route": "needs_reply",
    "response_required": True,
    "confidence": "high",
    "urgency": "soon",
    "deadline": "next Tuesday",
    "topic": "scheduling",
    "manual_review_reason": None,
    "rationale": "Direct scheduling request.",
    "suggested_reply": "Tuesday afternoon works for me.\n\nBest,\nNick",
}


def classifier_for(provider: str, **overrides) -> ProviderClassifier:
    profile = provider_profile(provider)
    selection = ProviderSelection(
        profile=profile,
        base_url=overrides.get("base_url", profile.default_base_url),
        model=overrides.get("model", profile.default_model),
        api_key="synthetic-key" if profile.requires_api_key else "",
    )
    return ProviderClassifier(build_client(selection))


class ClassifierTests(unittest.TestCase):
    def test_openai_request_uses_strict_schema_and_body_only_payload(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            response = {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(RESULT)}],
                    }
                ]
            }
            return io.BytesIO(json.dumps(response).encode())

        with patch("email_triage.providers.urlopen", fake_urlopen):
            result = classifier_for("openai").classify("Can we meet next Tuesday?", True)

        request_payload = json.loads(captured["request"].data)
        model_input = json.loads(request_payload["input"])
        self.assertEqual(set(model_input), {"email_body", "has_attachments"})
        self.assertFalse(request_payload["store"])
        self.assertTrue(request_payload["text"]["format"]["strict"])
        self.assertFalse(request_payload["text"]["format"]["schema"]["additionalProperties"])
        self.assertEqual(result.route, Route.NEEDS_REPLY)

    def test_ollama_request_stays_on_local_host_and_disables_thinking(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data)
            return io.BytesIO(json.dumps({"message": {"content": json.dumps(RESULT)}}).encode())

        with patch("email_triage.providers.urlopen", fake_urlopen):
            result = classifier_for("ollama").classify("Can we meet next Tuesday?", True)

        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        self.assertFalse(captured["payload"]["think"])
        self.assertEqual(captured["payload"]["format"]["type"], "object")
        self.assertEqual(result.route, Route.NEEDS_REPLY)

    def test_every_provider_produces_the_same_screening_contract(self):
        responses = {
            "ollama": {"message": {"content": json.dumps(RESULT)}},
            "anthropic": {"content": [{"type": "tool_use", "input": RESULT}]},
            "openrouter": {"choices": [{"message": {"content": json.dumps(RESULT)}}]},
            "gemini": {"choices": [{"message": {"content": json.dumps(RESULT)}}]},
        }
        for provider, response in responses.items():
            with self.subTest(provider=provider):
                with patch(
                    "email_triage.providers.urlopen",
                    lambda request, timeout=None, response=response: io.BytesIO(
                        json.dumps(response).encode()
                    ),
                ):
                    result = classifier_for(provider).classify("Can we meet?", False)
                self.assertEqual(result.route, Route.NEEDS_REPLY)
                self.assertEqual(result.priority_score, 3)

    def test_transport_failures_surface_as_classification_errors(self):
        with patch("email_triage.providers.urlopen", side_effect=TimeoutError):
            with self.assertRaises(ClassificationError):
                classifier_for("ollama").classify("Can we meet?", False)

    def test_build_classifier_pins_an_installed_local_model(self):
        environment = {
            "TRIAGE_PROVIDER": "ollama",
            "OLLAMA_MODEL": "missing:model",
            "TRIAGE_INPUT": "samples/inbox.jsonl",
        }
        listing = {"models": [{"name": "qwen3:8b"}]}
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env()
        with patch(
            "email_triage.providers.urlopen",
            lambda request, timeout=None: io.BytesIO(json.dumps(listing).encode()),
        ):
            classifier = build_classifier(settings)
        self.assertEqual(classifier.model, "qwen3:8b")
        self.assertEqual(classifier.provider, "ollama")

    def test_custom_closing_is_injected_into_screening_instructions(self) -> None:
        captured = {}
        custom = dict(RESULT)
        custom["suggested_reply"] = "Tuesday afternoon works for me.\n\nRegards,\nAlex"

        def fake_urlopen(request, timeout=None):
            captured["payload"] = json.loads(request.data)
            return io.BytesIO(json.dumps({"message": {"content": json.dumps(custom)}}).encode())

        classifier = classifier_for("ollama")
        classifier.reply_closing = "Regards,\nAlex"
        with patch("email_triage.providers.urlopen", fake_urlopen):
            result = classifier.classify("Can we meet next Tuesday?", False)
        self.assertIn("Regards,\\nAlex", captured["payload"]["messages"][0]["content"])
        self.assertTrue(result.suggested_reply.endswith("Regards,\nAlex"))


if __name__ == "__main__":
    unittest.main()
