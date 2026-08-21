"""Mailbox actions and the deterministic policy gate that bounds the sorting agent.

The agent may only *propose* actions. Every proposal is validated here against the
already-validated screening result before it can reach Microsoft Graph. Sending,
forwarding, and deletion are not representable in this module, so they cannot be
requested by a model or by text embedded in an email body.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from email_triage.models import Confidence, ReviewRecord, Route
from email_triage.pipeline import (
    FOLDER_NAMES,
    TOPIC_CATEGORIES,
    URGENCY_CATEGORIES,
)


PROCESSING_CATEGORIES = ("AI - Processed", "AI - Processing Error")
ALLOWED_CATEGORIES = frozenset(
    tuple(URGENCY_CATEGORIES.values())
    + tuple(TOPIC_CATEGORIES.values())
    + PROCESSING_CATEGORIES
)
MAX_CATEGORIES = 5


class ActionKind(StrEnum):
    DRAFT_REPLY = "draft_reply"
    TAG_MESSAGE = "tag_message"
    MARK_READ = "mark_read"
    FILE_MESSAGE = "file_message"


#: Draft before move, because moving a message changes its Graph identifier.
EXECUTION_ORDER = (
    ActionKind.DRAFT_REPLY,
    ActionKind.TAG_MESSAGE,
    ActionKind.MARK_READ,
    ActionKind.FILE_MESSAGE,
)


class PolicyViolation(ValueError):
    """Raised when a proposed action falls outside the permitted action space."""


@dataclass(frozen=True)
class MailboxAction:
    kind: ActionKind
    folder: str | None = None
    categories: tuple[str, ...] = ()
    reply_body: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "folder": self.folder,
            "categories": list(self.categories),
            "drafts_reply": self.reply_body is not None,
        }

    def describe(self) -> str:
        if self.kind == ActionKind.FILE_MESSAGE:
            return f"move to {self.folder}"
        if self.kind == ActionKind.TAG_MESSAGE:
            return f"categorize {', '.join(self.categories)}"
        if self.kind == ActionKind.DRAFT_REPLY:
            return "create unsent reply draft"
        return "mark read"


def permitted_folders(record: ReviewRecord) -> tuple[str, ...]:
    """Folders the agent may choose from for one screened message."""

    analysis = record.analysis
    if (
        analysis.manual_review_reason is not None
        or analysis.confidence == Confidence.LOW
        or record.processing_error
    ):
        return (FOLDER_NAMES[Route.NEEDS_REVIEW],)
    return (record.target_folder,)


def may_draft_reply(record: ReviewRecord) -> bool:
    return record.analysis.route == Route.NEEDS_REPLY and bool(record.analysis.suggested_reply)


def may_mark_read(record: ReviewRecord, allow_mark_read: bool) -> bool:
    return allow_mark_read and record.analysis.route != Route.NEEDS_REVIEW


def validate_action(
    action: MailboxAction,
    record: ReviewRecord,
    allow_mark_read: bool,
) -> MailboxAction:
    """Return a normalized action, or raise PolicyViolation."""

    if action.kind == ActionKind.FILE_MESSAGE:
        folder = (action.folder or "").strip()
        if not folder.startswith("AI Triage/"):
            raise PolicyViolation(f"unknown folder {folder!r}")
        allowed = permitted_folders(record)
        if folder not in allowed:
            raise PolicyViolation(
                f"folder {folder!r} is not permitted for this message; allowed: {list(allowed)}"
            )
        return MailboxAction(kind=ActionKind.FILE_MESSAGE, folder=folder)

    if action.kind == ActionKind.TAG_MESSAGE:
        categories = tuple(dict.fromkeys(item.strip() for item in action.categories if item))
        if not categories:
            raise PolicyViolation("at least one category is required")
        if len(categories) > MAX_CATEGORIES:
            raise PolicyViolation(f"at most {MAX_CATEGORIES} categories are allowed")
        unknown = [item for item in categories if item not in ALLOWED_CATEGORIES]
        if unknown:
            raise PolicyViolation(f"unknown categories {unknown}")
        return MailboxAction(kind=ActionKind.TAG_MESSAGE, categories=categories)

    if action.kind == ActionKind.DRAFT_REPLY:
        if not may_draft_reply(record):
            raise PolicyViolation("this message has no approved reply text")
        approved = record.analysis.suggested_reply or ""
        proposed = action.reply_body if action.reply_body is not None else approved
        if proposed.strip() != approved.strip():
            raise PolicyViolation("reply text must match the screened suggested reply verbatim")
        return MailboxAction(kind=ActionKind.DRAFT_REPLY, reply_body=approved)

    if action.kind == ActionKind.MARK_READ:
        if not may_mark_read(record, allow_mark_read):
            raise PolicyViolation("marking read is not permitted for this message")
        return MailboxAction(kind=ActionKind.MARK_READ)

    raise PolicyViolation(f"unsupported action {action.kind!r}")


def action_from_tool_call(name: str, arguments: dict[str, Any]) -> MailboxAction:
    """Translate one model tool call into an action, before policy validation."""

    try:
        kind = ActionKind(name)
    except ValueError as exc:
        raise PolicyViolation(f"unsupported tool {name!r}") from exc
    if not isinstance(arguments, dict):
        raise PolicyViolation("tool arguments must be an object")
    if kind == ActionKind.FILE_MESSAGE:
        folder = arguments.get("folder")
        if not isinstance(folder, str):
            raise PolicyViolation("file_message requires a folder string")
        return MailboxAction(kind=kind, folder=folder)
    if kind == ActionKind.TAG_MESSAGE:
        categories = arguments.get("categories")
        if isinstance(categories, str):
            categories = [categories]
        if not isinstance(categories, list) or not all(
            isinstance(item, str) for item in categories
        ):
            raise PolicyViolation("tag_message requires a list of category strings")
        return MailboxAction(kind=kind, categories=tuple(categories))
    return MailboxAction(kind=kind)


def normalize_plan(actions: list[MailboxAction]) -> list[MailboxAction]:
    """Keep the first action of each kind and order them safely for Graph."""

    first: dict[ActionKind, MailboxAction] = {}
    for action in actions:
        first.setdefault(action.kind, action)
    return [first[kind] for kind in EXECUTION_ORDER if kind in first]


def default_plan(record: ReviewRecord, allow_mark_read: bool) -> list[MailboxAction]:
    """Deterministic plan used when the agent is disabled or fails."""

    plan = [MailboxAction(kind=ActionKind.TAG_MESSAGE, categories=record.categories)]
    if may_draft_reply(record):
        plan.append(
            MailboxAction(
                kind=ActionKind.DRAFT_REPLY,
                reply_body=record.analysis.suggested_reply,
            )
        )
    if may_mark_read(record, allow_mark_read):
        plan.append(MailboxAction(kind=ActionKind.MARK_READ))
    plan.append(MailboxAction(kind=ActionKind.FILE_MESSAGE, folder=record.target_folder))
    return normalize_plan(
        [validate_action(action, record, allow_mark_read) for action in plan]
    )
