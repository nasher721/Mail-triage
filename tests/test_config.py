import unittest
from unittest.mock import patch

from email_triage.config import ConfigurationError, Settings
from pathlib import Path


GRAPH_ENV = {
    "MS_TENANT_ID": "synthetic-tenant",
    "MS_CLIENT_ID": "synthetic-client",
}


class ConfigurationTests(unittest.TestCase):
    def test_external_ai_requires_explicit_approval(self) -> None:
        environment = {
            **GRAPH_ENV,
            "TRIAGE_BACKEND": "openai",
            "OPENAI_API_KEY": "synthetic-key",
            "EXTERNAL_AI_APPROVED": "false",
        }
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "privacy/security approval"):
                Settings.from_env()

    def test_local_ollama_does_not_require_external_approval(self) -> None:
        environment = {
            **GRAPH_ENV,
            "TRIAGE_BACKEND": "ollama",
            "OLLAMA_HOST": "http://127.0.0.1:11434",
            "OLLAMA_MODEL": "qwen3:8b",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.ai_backend, "ollama")
        self.assertTrue(settings.uses_local_inference)
        self.assertFalse(settings.intercept_clinical)

    def test_local_input_skips_microsoft_graph(self) -> None:
        environment = {
            "TRIAGE_BACKEND": "ollama",
            "TRIAGE_INPUT": "samples/inbox.jsonl",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.mailbox_source, "local")
        self.assertEqual(settings.input_path, Path("samples/inbox.jsonl"))

    def test_missing_graph_defaults_to_credential_free_owa(self) -> None:
        with patch.dict("os.environ", {"TRIAGE_BACKEND": "ollama"}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.mailbox_source, "owa")

    def test_remote_ollama_requires_approval(self) -> None:
        environment = {
            **GRAPH_ENV,
            "TRIAGE_BACKEND": "ollama",
            "OLLAMA_HOST": "http://ollama.example.org:11434",
            "EXTERNAL_AI_APPROVED": "false",
        }
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "remote inference host"):
                Settings.from_env()

    def test_desktop_source_needs_no_graph_registration_and_is_read_only(self) -> None:
        environment = {
            "TRIAGE_BACKEND": "ollama",
            "OLLAMA_HOST": "http://127.0.0.1:11434",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env(source="desktop")
            self.assertEqual(settings.mailbox_source, "desktop")
            with self.assertRaisesRegex(ConfigurationError, "read-only"):
                Settings.from_env(source="desktop", apply_changes=True)
            with self.assertRaisesRegex(ConfigurationError, "cannot mark"):
                Settings.from_env(source="desktop", mark_read=True)

    def test_accessibility_source_needs_no_graph_and_is_read_only(self) -> None:
        environment = {
            "TRIAGE_BACKEND": "ollama",
            "OLLAMA_HOST": "http://127.0.0.1:11434",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env(source="accessibility")
            self.assertEqual(settings.mailbox_source, "accessibility")
            with self.assertRaisesRegex(ConfigurationError, "read-only"):
                Settings.from_env(source="accessibility", apply_changes=True)


if __name__ == "__main__":
    unittest.main()
