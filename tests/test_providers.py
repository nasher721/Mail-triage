import io
import json
import unittest
from unittest.mock import patch

from email_triage.providers import (
    PROVIDERS,
    AssistantReply,
    ProviderError,
    ProviderSelection,
    ToolCall,
    ToolSpec,
    assistant_message,
    build_client,
    describe_providers,
    provider_profile,
    resolve_model,
    system_message,
    tool_message,
    user_message,
)


SCHEMA = {"type": "object", "properties": {"route": {"type": "string"}}}
TOOLS = [
    ToolSpec(
        name="file_message",
        description="Move the message into one triage folder.",
        parameters={"type": "object", "properties": {"folder": {"type": "string"}}},
    )
]


def selection(name: str, **overrides) -> ProviderSelection:
    profile = provider_profile(name)
    values = {
        "base_url": profile.default_base_url,
        "model": profile.default_model,
        "api_key": "" if not profile.requires_api_key else "synthetic-key",
    }
    values.update(overrides)
    return ProviderSelection(profile=profile, **values)


class Recorder:
    """Capture one outbound request and replay a canned response."""

    def __init__(self, response):
        self.response = response
        self.url = ""
        self.payload: dict = {}
        self.headers: dict = {}

    def __call__(self, request, timeout=None):
        self.url = request.full_url
        self.payload = json.loads(request.data) if request.data else {}
        self.headers = dict(request.headers)
        return io.BytesIO(json.dumps(self.response).encode())


class RegistryTests(unittest.TestCase):
    def test_registry_covers_the_documented_ai_systems(self) -> None:
        for name in (
            "ollama",
            "openai",
            "anthropic",
            "openrouter",
            "opencode",
            "lmstudio",
            "gemini",
            "groq",
            "azure-openai",
            "custom",
        ):
            self.assertIn(name, PROVIDERS)
        self.assertEqual(len(describe_providers()), len(PROVIDERS))

    def test_friendly_aliases_resolve_to_registry_entries(self) -> None:
        self.assertEqual(provider_profile("claude").name, "anthropic")
        self.assertEqual(provider_profile("ChatGPT").name, "openai")
        self.assertEqual(provider_profile("azure").name, "azure-openai")
        with self.assertRaises(KeyError):
            provider_profile("not-a-provider")

    def test_only_loopback_local_providers_keep_data_on_this_machine(self) -> None:
        self.assertTrue(selection("ollama").keeps_data_local)
        self.assertFalse(
            selection("ollama", base_url="http://ollama.example.org:11434").keeps_data_local
        )
        self.assertFalse(selection("openai").keeps_data_local)
        self.assertTrue(selection("lmstudio").keeps_data_local)


class ScreeningRoutingTests(unittest.TestCase):
    def test_ollama_posts_the_schema_to_the_local_chat_endpoint(self) -> None:
        recorder = Recorder({"message": {"content": json.dumps({"route": "no_reply"})}})
        client = build_client(selection("ollama"))
        with patch("email_triage.providers.urlopen", recorder):
            result = client.complete_json("instructions", "payload", SCHEMA, "email_screening")

        self.assertEqual(recorder.url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(recorder.payload["format"], SCHEMA)
        self.assertFalse(recorder.payload["think"])
        self.assertEqual(result, {"route": "no_reply"})

    def test_openai_uses_the_responses_endpoint_with_a_strict_schema(self) -> None:
        recorder = Recorder(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps({"route": "needs_reply"})}
                        ],
                    }
                ]
            }
        )
        client = build_client(selection("openai"))
        with patch("email_triage.providers.urlopen", recorder):
            result = client.complete_json("instructions", "payload", SCHEMA, "email_screening")

        self.assertEqual(recorder.url, "https://api.openai.com/v1/responses")
        self.assertTrue(recorder.payload["text"]["format"]["strict"])
        self.assertFalse(recorder.payload["store"])
        self.assertEqual(recorder.headers["Authorization"], "Bearer synthetic-key")
        self.assertEqual(result, {"route": "needs_reply"})

    def test_anthropic_forces_a_single_structured_tool_call(self) -> None:
        recorder = Recorder(
            {"content": [{"type": "tool_use", "name": "email_screening", "input": {"route": "no_reply"}}]}
        )
        client = build_client(selection("anthropic"))
        with patch("email_triage.providers.urlopen", recorder):
            result = client.complete_json("instructions", "payload", SCHEMA, "email_screening")

        self.assertEqual(recorder.url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(recorder.payload["tool_choice"]["name"], "email_screening")
        self.assertEqual(recorder.headers["X-api-key"], "synthetic-key")
        self.assertEqual(recorder.headers["Anthropic-version"], "2023-06-01")
        self.assertEqual(result, {"route": "no_reply"})

    def test_openrouter_uses_chat_completions_with_a_json_schema(self) -> None:
        recorder = Recorder(
            {"choices": [{"message": {"content": json.dumps({"route": "needs_review"})}}]}
        )
        client = build_client(selection("openrouter"))
        with patch("email_triage.providers.urlopen", recorder):
            result = client.complete_json("instructions", "payload", SCHEMA, "email_screening")

        self.assertEqual(recorder.url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(recorder.payload["response_format"]["type"], "json_schema")
        self.assertEqual(result, {"route": "needs_review"})

    def test_providers_without_schema_support_get_the_schema_in_the_prompt(self) -> None:
        recorder = Recorder(
            {"choices": [{"message": {"content": "here you go: " + json.dumps({"route": "no_reply"})}}]}
        )
        client = build_client(selection("opencode"))
        with patch("email_triage.providers.urlopen", recorder):
            result = client.complete_json("instructions", "payload", SCHEMA, "email_screening")

        self.assertEqual(recorder.payload["response_format"], {"type": "json_object"})
        self.assertIn('"route"', recorder.payload["messages"][0]["content"])
        self.assertEqual(result, {"route": "no_reply"})

    def test_azure_style_query_strings_survive_path_joining(self) -> None:
        recorder = Recorder({"choices": [{"message": {"content": "{}"}}]})
        client = build_client(
            selection(
                "azure-openai",
                base_url="https://example.openai.azure.com/openai/deployments/gpt?api-version=2024-10-21",
            )
        )
        with patch("email_triage.providers.urlopen", recorder):
            client.complete_json("instructions", "payload", SCHEMA, "email_screening")

        self.assertEqual(
            recorder.url,
            "https://example.openai.azure.com/openai/deployments/gpt/chat/completions"
            "?api-version=2024-10-21",
        )
        self.assertEqual(recorder.headers["Api-key"], "synthetic-key")

    def test_unreachable_providers_raise_provider_error(self) -> None:
        client = build_client(selection("ollama"))
        with patch("email_triage.providers.urlopen", side_effect=TimeoutError):
            with self.assertRaises(ProviderError):
                client.complete_json("instructions", "payload", SCHEMA, "email_screening")


class ToolRoutingTests(unittest.TestCase):
    """The neutral conversation must survive translation to every dialect."""

    def conversation(self) -> list[dict]:
        call = ToolCall(id="call_1", name="file_message", arguments={"folder": "AI Triage"})
        return [
            system_message("rules"),
            user_message("briefing"),
            assistant_message(AssistantReply("filing", (call,))),
            tool_message(call, "accepted"),
        ]

    def test_ollama_tool_calls_round_trip(self) -> None:
        recorder = Recorder(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "file_message", "arguments": {"folder": "AI Triage"}}}
                    ],
                }
            }
        )
        client = build_client(selection("ollama"))
        with patch("email_triage.providers.urlopen", recorder):
            reply = client.chat(self.conversation(), TOOLS)

        self.assertEqual(recorder.payload["tools"][0]["function"]["name"], "file_message")
        self.assertEqual(recorder.payload["messages"][3]["tool_name"], "file_message")
        self.assertEqual(reply.tool_calls[0].arguments, {"folder": "AI Triage"})

    def test_openai_compatible_tool_calls_round_trip(self) -> None:
        recorder = Recorder(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_9",
                                    "function": {
                                        "name": "file_message",
                                        "arguments": '{"folder": "AI Triage"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )
        client = build_client(selection("groq"))
        with patch("email_triage.providers.urlopen", recorder):
            reply = client.chat(self.conversation(), TOOLS)

        sent = recorder.payload["messages"]
        self.assertEqual(sent[2]["tool_calls"][0]["function"]["arguments"], '{"folder": "AI Triage"}')
        self.assertEqual(sent[3], {"role": "tool", "tool_call_id": "call_1", "content": "accepted"})
        self.assertEqual(reply.tool_calls[0].id, "call_9")
        self.assertEqual(reply.tool_calls[0].arguments, {"folder": "AI Triage"})

    def test_anthropic_tool_calls_round_trip_as_content_blocks(self) -> None:
        recorder = Recorder(
            {
                "content": [
                    {"type": "text", "text": "filing it"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "file_message",
                        "input": {"folder": "AI Triage"},
                    },
                ]
            }
        )
        client = build_client(selection("anthropic"))
        with patch("email_triage.providers.urlopen", recorder):
            reply = client.chat(self.conversation(), TOOLS)

        self.assertEqual(recorder.payload["system"], "rules")
        self.assertEqual(recorder.payload["tools"][0]["input_schema"], TOOLS[0].parameters)
        assistant = recorder.payload["messages"][1]
        self.assertEqual(assistant["content"][1]["type"], "tool_use")
        result_block = recorder.payload["messages"][2]["content"][0]
        self.assertEqual(result_block["type"], "tool_result")
        self.assertEqual(result_block["tool_use_id"], "call_1")
        self.assertEqual(reply.content, "filing it")
        self.assertEqual(reply.tool_calls[0].name, "file_message")

    def test_consecutive_tool_results_are_merged_into_one_claude_turn(self) -> None:
        first = ToolCall(id="a", name="tag_message", arguments={})
        second = ToolCall(id="b", name="file_message", arguments={})
        messages = [
            system_message("rules"),
            user_message("briefing"),
            assistant_message(AssistantReply("", (first, second))),
            tool_message(first, "accepted"),
            tool_message(second, "accepted"),
        ]
        recorder = Recorder({"content": [{"type": "text", "text": "done"}]})
        client = build_client(selection("anthropic"))
        with patch("email_triage.providers.urlopen", recorder):
            client.chat(messages, TOOLS)

        self.assertEqual(len(recorder.payload["messages"]), 3)
        self.assertEqual(len(recorder.payload["messages"][2]["content"]), 2)


class ModelResolutionTests(unittest.TestCase):
    def test_ollama_falls_back_to_the_strongest_installed_tag(self) -> None:
        client = build_client(selection("ollama"))
        listing = Recorder({"models": [{"name": "qwen3:0.6b"}, {"name": "qwen3:8b"}]})
        with patch("email_triage.providers.urlopen", listing):
            self.assertEqual(resolve_model(client, "qwen3:8b", ("qwen3:8b",)), "qwen3:8b")
            self.assertEqual(resolve_model(client, "missing:model", ("qwen3:8b",)), "qwen3:8b")

    def test_hosted_providers_keep_the_requested_model(self) -> None:
        client = build_client(selection("openrouter"))
        with patch("email_triage.providers.urlopen", side_effect=AssertionError("no listing")):
            self.assertEqual(resolve_model(client, "anthropic/claude-sonnet-4.5"), "anthropic/claude-sonnet-4.5")

    def test_missing_keys_are_reported_before_any_request(self) -> None:
        client = build_client(selection("openai", api_key=""))
        ready, detail = client.reachable()
        self.assertFalse(ready)
        self.assertIn("OPENAI_API_KEY", detail)


if __name__ == "__main__":
    unittest.main()
