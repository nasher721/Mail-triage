"""Execute a validated mailbox plan against Microsoft Graph, or preview it.

Only four operations are reachable from here: create an unsent reply draft, set
categories, mark read, and move to a triage folder. There is no send, forward, or
delete path anywhere in this module.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from email_triage.actions import ActionKind, MailboxAction
from email_triage.graph import GraphError
from email_triage.models import ReviewRecord


@dataclass(frozen=True)
class AppliedAction:
    kind: ActionKind
    description: str
    status: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "description": self.description,
            "status": self.status,
            "detail": self.detail,
        }


class WritableMailbox(Protocol):
    """GraphMailbox and OwaMailbox both satisfy this; there is no send/delete method."""

    def create_reply_draft(self, message_id: str, reply_text: str) -> str: ...

    def update_message(
        self,
        message_id: str,
        categories: tuple[str, ...] | None = None,
        is_read: bool | None = None,
    ) -> None: ...

    def ensure_folder_path(self, folder_path: str) -> str: ...

    def move_message(self, message_id: str, folder_id: str) -> str: ...


class Actuator(Protocol):
    def execute(self, message_id: str, action: MailboxAction) -> str: ...


class DryRunActuator:
    """Preview actuator. Records the plan and touches nothing."""

    mode = "dry-run"

    def __init__(self) -> None:
        self.calls: list[tuple[str, MailboxAction]] = []

    def execute(self, message_id: str, action: MailboxAction) -> str:
        self.calls.append((message_id, action))
        return "not executed (dry run)"


class GraphActuator:
    """Apply actions to the signed-in mailbox (Graph app or Outlook-in-Edge session)."""

    mode = "apply"

    def __init__(self, mailbox: WritableMailbox):
        self.mailbox = mailbox
        self.current_ids: dict[str, str] = {}

    def _resolve(self, message_id: str) -> str:
        return self.current_ids.get(message_id, message_id)

    def execute(self, message_id: str, action: MailboxAction) -> str:
        live_id = self._resolve(message_id)
        if action.kind == ActionKind.DRAFT_REPLY:
            draft_id = self.mailbox.create_reply_draft(live_id, action.reply_body or "")
            return f"draft {draft_id or 'created'} saved in Drafts (unsent)"
        if action.kind == ActionKind.TAG_MESSAGE:
            self.mailbox.update_message(live_id, categories=action.categories)
            return ", ".join(action.categories)
        if action.kind == ActionKind.MARK_READ:
            self.mailbox.update_message(live_id, is_read=True)
            return "isRead=true"
        if action.kind == ActionKind.FILE_MESSAGE:
            folder_id = self.mailbox.ensure_folder_path(action.folder or "")
            moved_id = self.mailbox.move_message(live_id, folder_id)
            self.current_ids[message_id] = moved_id
            return f"moved to {action.folder}"
        raise GraphError(f"unsupported action {action.kind!r}")


def apply_plan(
    record: ReviewRecord,
    plan: list[MailboxAction],
    actuator: Actuator,
) -> list[AppliedAction]:
    """Run each action in order, stopping that message's plan on the first failure."""

    applied: list[AppliedAction] = []
    for action in plan:
        try:
            detail = actuator.execute(record.message_id, action)
        except GraphError as exc:
            applied.append(
                AppliedAction(action.kind, action.describe(), "failed", str(exc))
            )
            break
        status = "planned" if getattr(actuator, "mode", "apply") == "dry-run" else "applied"
        applied.append(AppliedAction(action.kind, action.describe(), status, detail))
    return applied


class ActionLog:
    """Owner-only audit trail of every action planned or applied. No bodies are stored."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.path = output_dir / "applied_actions.jsonl"

    def append(
        self,
        record: ReviewRecord,
        plan_source: str,
        mode: str,
        applied: list[AppliedAction],
    ) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.output_dir, 0o700)
        entry = {
            "message_id": record.message_id,
            "internet_message_id": record.internet_message_id,
            "subject": record.subject,
            "sender_address": record.sender_address,
            "route": str(record.analysis.route),
            "plan_source": plan_source,
            "mode": mode,
            "actions": [item.to_dict() for item in applied],
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.chmod(self.path, 0o600)
