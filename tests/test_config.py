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


class ProviderSettingsTests(unittest.TestCase):
    BASE = {"TRIAGE_INPUT": "samples/inbox.jsonl"}

    def test_provider_defaults_come_from_the_registry(self) -> None:
        environment = {**self.BASE, "TRIAGE_PROVIDER": "anthropic",
                       "ANTHROPIC_API_KEY": "synthetic-key", "EXTERNAL_AI_APPROVED": "true"}
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.ai_provider, "anthropic")
        self.assertEqual(settings.screening.base_url, "https://api.anthropic.com")
        self.assertEqual(settings.screening.model, "claude-sonnet-4-5")
        self.assertEqual(settings.screening.api_key, "synthetic-key")
        self.assertFalse(settings.uses_local_inference)
        self.assertTrue(settings.intercept_clinical)

    def test_backend_alias_and_friendly_names_still_select_a_provider(self) -> None:
        environment = {**self.BASE, "TRIAGE_BACKEND": "claude",
                       "CLAUDE_API_KEY": "synthetic-key", "EXTERNAL_AI_APPROVED": "true"}
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.ai_provider, "anthropic")

    def test_explicit_overrides_beat_environment_defaults(self) -> None:
        environment = {**self.BASE, "TRIAGE_PROVIDER": "ollama", "OLLAMA_MODEL": "qwen3:8b"}
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env(model="llama3.1:8b", base_url="http://localhost:11500")
        self.assertEqual(settings.screening.model, "llama3.1:8b")
        self.assertEqual(settings.screening.base_url, "http://localhost:11500")
        self.assertTrue(settings.uses_local_inference)

    def test_unknown_provider_is_rejected_with_the_supported_list(self) -> None:
        with patch.dict("os.environ", {**self.BASE, "TRIAGE_PROVIDER": "hal9000"}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "Unknown AI provider"):
                Settings.from_env()

    def test_a_remote_sorting_agent_also_needs_approval(self) -> None:
        environment = {**self.BASE, "TRIAGE_PROVIDER": "ollama",
                       "TRIAGE_AGENT_PROVIDER": "openai", "OPENAI_API_KEY": "synthetic-key"}
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "privacy/security approval"):
                Settings.from_env()
            settings = Settings.from_env(use_agent=False)
        self.assertTrue(settings.uses_local_inference)

    def test_agent_overrides_apply_only_to_the_agent(self) -> None:
        environment = {
            **self.BASE,
            "TRIAGE_PROVIDER": "ollama",
            "TRIAGE_MODEL": "qwen3:14b",
            "TRIAGE_AGENT_MODEL": "qwen3:4b",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.screening.model, "qwen3:14b")
        self.assertEqual(settings.agent.model, "qwen3:4b")
        self.assertEqual(settings.agent.base_url, settings.screening.base_url)

    def test_missing_hosted_key_is_reported_before_anything_is_contacted(self) -> None:
        with patch.dict("os.environ", {**self.BASE, "TRIAGE_PROVIDER": "groq"}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "GROQ_API_KEY"):
                Settings.from_env()

    def test_request_tuning_is_read_from_the_environment(self) -> None:
        environment = {
            **self.BASE,
            "TRIAGE_PROVIDER": "ollama",
            "TRIAGE_TEMPERATURE": "0.4",
            "TRIAGE_REQUEST_TIMEOUT": "45",
            "TRIAGE_AGENT_MAX_ROUNDS": "6",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.screening.temperature, 0.4)
        self.assertEqual(settings.screening.timeout, 45)
        self.assertEqual(settings.agent_max_rounds, 6)
        with patch.dict("os.environ", {**environment, "TRIAGE_TEMPERATURE": "9"}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "between 0 and 2"):
                Settings.from_env()

    def test_reply_closing_is_read_from_the_environment(self) -> None:
        environment = {
            **self.BASE,
            "TRIAGE_PROVIDER": "ollama",
            "TRIAGE_REPLY_CLOSING": "Regards,\nAlex",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.reply_closing, "Regards,\nAlex")
        with patch.dict("os.environ", {**environment, "TRIAGE_REPLY_CLOSING": "<b>Nick</b>"}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "HTML"):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
