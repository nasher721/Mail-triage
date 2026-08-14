import io
import json
import unittest
from unittest.mock import patch

from email_triage.classifier import OllamaClassifier, OpenAIClassifier, resolve_ollama_model
from email_triage.models import Route


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


class ClassifierTests(unittest.TestCase):
    def test_rest_request_uses_strict_schema_and_body_only_payload(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            response = {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(RESULT)}],
                    }
                ]
            }
            return io.BytesIO(json.dumps(response).encode())

        with patch("email_triage.classifier.urlopen", fake_urlopen):
            result = OpenAIClassifier("synthetic-key", "gpt-4o").classify(
                "Can we meet next Tuesday?", True
            )

        request_payload = json.loads(captured["request"].data)
        model_input = json.loads(request_payload["input"])
        self.assertEqual(set(model_input), {"email_body", "has_attachments"})
        self.assertFalse(request_payload["store"])
        self.assertTrue(request_payload["text"]["format"]["strict"])
        self.assertFalse(request_payload["text"]["format"]["schema"]["additionalProperties"])
        self.assertEqual(result.route, Route.NEEDS_REPLY)

    def test_ollama_request_stays_on_local_host_and_disables_thinking(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data)
            captured["timeout"] = timeout
            response = {"message": {"content": json.dumps(RESULT)}}
            return io.BytesIO(json.dumps(response).encode())

        with patch("email_triage.classifier.urlopen", fake_urlopen):
            result = OllamaClassifier("http://127.0.0.1:11434", "qwen3:8b").classify(
                "Can we meet next Tuesday?", True
            )

        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        self.assertFalse(captured["payload"]["think"])
        self.assertEqual(captured["payload"]["format"]["type"], "object")
        self.assertEqual(result.route, Route.NEEDS_REPLY)

    def test_resolve_ollama_model_prefers_strongest_installed_tag(self):
        def fake_urlopen(request, timeout=None):
            response = {"models": [{"name": "qwen3:0.6b"}, {"name": "qwen3:8b"}]}
            return io.BytesIO(json.dumps(response).encode())

        with patch("email_triage.classifier.urlopen", fake_urlopen):
            self.assertEqual(
                resolve_ollama_model("http://127.0.0.1:11434", "qwen3:8b"),
                "qwen3:8b",
            )
            self.assertEqual(
                resolve_ollama_model("http://127.0.0.1:11434", "missing:model"),
                "qwen3:8b",
            )


if __name__ == "__main__":
    unittest.main()
