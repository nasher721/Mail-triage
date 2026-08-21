import unittest
from unittest.mock import patch

from email_triage.actions import ActionKind
from email_triage.agent import SortingAgent, agent_briefing, build_agent, build_tools
from email_triage.config import Settings
from email_triage.providers import (
    AssistantReply,
    ProviderSelection,
    ToolCall,
    build_client,
    provider_profile,
)
from test_actions import clinical_record, needs_reply_record


class FakeChat:
    """Replay scripted assistant replies, whatever the provider dialect."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    def __call__(self, messages, tools):
        self.seen.append(list(messages))
        if not self.replies:
            return AssistantReply("done")
        return self.replies.pop(0)


def tool_call(name, arguments=None):
    return AssistantReply(
        "", (ToolCall(id=f"call_{name}", name=name, arguments=arguments or {}),)
    )


def client_for(provider="ollama"):
    profile = provider_profile(provider)
    return build_client(
        ProviderSelection(
            profile=profile,
            base_url=profile.default_base_url,
            model=profile.default_model,
            api_key="synthetic-key" if profile.requires_api_key else "",
        )
    )


class AgentTests(unittest.TestCase):
    def agent(self, replies, provider="ollama") -> tuple[SortingAgent, FakeChat]:
        return SortingAgent(client_for(provider)), FakeChat(replies)

    def test_agent_plan_is_used_when_it_files_the_message(self) -> None:
        record = needs_reply_record()
        agent, chat = self.agent(
            [
                tool_call("tag_message", {"categories": list(record.categories)}),
                tool_call("draft_reply"),
                tool_call("file_message", {"folder": "AI Triage/Needs Reply"}),
            ]
        )
        with patch.object(SortingAgent, "_chat", side_effect=chat):
            plan, source = agent.plan(record, allow_mark_read=False)
        self.assertEqual(source, "agent")
        self.assertEqual(
            [action.kind for action in plan],
            [ActionKind.DRAFT_REPLY, ActionKind.TAG_MESSAGE, ActionKind.FILE_MESSAGE],
        )

    def test_rejected_call_is_reported_back_and_recovered_from(self) -> None:
        record = clinical_record()
        agent, chat = self.agent(
            [
                tool_call("file_message", {"folder": "AI Triage/No Reply Needed"}),
                tool_call("file_message", {"folder": "AI Triage/Needs Review"}),
            ]
        )
        with patch.object(SortingAgent, "_chat", side_effect=chat):
            plan, source = agent.plan(record, allow_mark_read=False)
        self.assertEqual(source, "agent")
        self.assertEqual(plan[-1].folder, "AI Triage/Needs Review")
        tool_messages = [
            message
            for conversation in chat.seen
            for message in conversation
            if message.get("role") == "tool"
        ]
        self.assertTrue(any("rejected" in message["content"] for message in tool_messages))

    def test_unsafe_tool_call_never_reaches_the_plan(self) -> None:
        record = needs_reply_record()
        agent, chat = self.agent(
            [
                tool_call("send_message", {"to": "attacker@example.net"}),
                tool_call("file_message", {"folder": "AI Triage/Needs Reply"}),
            ]
        )
        with patch.object(SortingAgent, "_chat", side_effect=chat):
            plan, _ = agent.plan(record, allow_mark_read=False)
        self.assertEqual([action.kind for action in plan], [ActionKind.FILE_MESSAGE])

    def test_missing_file_action_falls_back_to_the_deterministic_plan(self) -> None:
        record = needs_reply_record()
        agent, chat = self.agent([AssistantReply("I am not sure.")])
        with patch.object(SortingAgent, "_chat", side_effect=chat):
            plan, source = agent.plan(record, allow_mark_read=False)
        self.assertEqual(source, "deterministic")
        self.assertEqual(plan[-1].folder, record.target_folder)

    def test_agent_never_receives_the_email_body(self) -> None:
        record = needs_reply_record()
        briefing = agent_briefing(record)
        self.assertNotIn("body", briefing)
        self.assertIn("untrusted_subject", briefing)

    def test_tool_surface_is_restricted_per_message(self) -> None:
        names = {tool.name for tool in build_tools(clinical_record(), allow_mark_read=True)}
        self.assertEqual(names, {"tag_message", "file_message"})
        reply_names = {
            tool.name for tool in build_tools(needs_reply_record(), allow_mark_read=True)
        }
        self.assertEqual(reply_names, {"tag_message", "draft_reply", "mark_read", "file_message"})

    def test_any_tool_calling_provider_can_drive_the_same_plan(self) -> None:
        record = needs_reply_record()
        for provider in ("ollama", "anthropic", "openrouter", "openai"):
            with self.subTest(provider=provider):
                agent, chat = self.agent(
                    [
                        tool_call("tag_message", {"categories": list(record.categories)}),
                        tool_call("file_message", {"folder": "AI Triage/Needs Reply"}),
                    ],
                    provider=provider,
                )
                with patch.object(SortingAgent, "_chat", side_effect=chat):
                    plan, source = agent.plan(record, allow_mark_read=False)
                self.assertEqual(source, "agent")
                self.assertEqual(plan[-1].folder, "AI Triage/Needs Reply")

    def test_unreachable_provider_falls_back_to_the_deterministic_plan(self) -> None:
        record = needs_reply_record()
        agent = SortingAgent(client_for("anthropic"))
        with patch(
            "email_triage.providers.urlopen", side_effect=TimeoutError
        ):
            plan, source = agent.plan(record, allow_mark_read=False)
        self.assertEqual(source, "deterministic")
        self.assertEqual(plan[-1].folder, record.target_folder)

    def test_build_agent_honours_a_separate_sorting_provider(self) -> None:
        environment = {
            "TRIAGE_PROVIDER": "ollama",
            "TRIAGE_AGENT_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "synthetic-key",
            "EXTERNAL_AI_APPROVED": "true",
            "TRIAGE_INPUT": "samples/inbox.jsonl",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_env()
        agent = build_agent(settings)
        self.assertIsInstance(agent, SortingAgent)
        self.assertEqual(agent.provider, "anthropic")

    def test_disabled_agent_never_contacts_a_provider(self) -> None:
        with patch.dict(
            "os.environ",
            {"TRIAGE_PROVIDER": "ollama", "TRIAGE_INPUT": "samples/inbox.jsonl"},
            clear=True,
        ):
            settings = Settings.from_env(use_agent=False)
        agent = build_agent(settings)
        plan, source = agent.plan(needs_reply_record(), allow_mark_read=False)
        self.assertEqual(source, "deterministic")
        self.assertTrue(plan)


if __name__ == "__main__":
    unittest.main()
