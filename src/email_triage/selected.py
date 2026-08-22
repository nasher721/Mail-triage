"""Apply previously screened messages by ID without re-reading the mailbox."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from email_triage.actions import PolicyViolation, plan_from_stored
from email_triage.apply import ActionLog, Actuator, apply_plan
from email_triage.config import ConfigurationError
from email_triage.models import ReviewRecord
from email_triage.pipeline import LocalQueue

MAX_APPLY_IDS = 200

_SWIFT_ANALYSIS_DEFAULT: dict[str, Any] = {
    "route": "needs_review",
    "urgency": "routine",
    "topic": "other",
    "confidence": "low",
    "priority_score": 1,
    "summary": "Apply could not use a stored review record.",
    "action_items": [],
    "suggested_reply": None,
    "manual_review_reason": "low_confidence",
    "deadline": None,
}


def load_apply_ids(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        raise ConfigurationError(f"apply IDs file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError("apply IDs file must be JSON") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("apply IDs file must be an object")
    values = raw.get("message_ids")
    if not isinstance(values, list) or not values:
        raise ConfigurationError("message_ids must be a non-empty list")
    if len(values) > MAX_APPLY_IDS:
        raise ConfigurationError(f"message_ids cannot exceed {MAX_APPLY_IDS} entries")
    ids = tuple(str(item) for item in values if isinstance(item, str) and item)
    if len(ids) != len(values):
        raise ConfigurationError("message_ids must contain only non-empty strings")
    if len(set(ids)) != len(ids):
        raise ConfigurationError("message_ids must not contain duplicates")
    return ids


def apply_stdout_line(
    *,
    message_id: str,
    payload: dict[str, Any] | None,
    actions: list[dict[str, Any]],
    plan_source: str = "stored",
) -> dict[str, Any]:
    """Build a stdout object the Mac `TriageRecord` decoder can always parse."""

    if payload is not None:
        try:
            line = ReviewRecord.from_dict(payload).to_dict()
        except (ValueError, TypeError, KeyError):
            categories = payload.get("categories")
            line = {
                "message_id": message_id,
                "subject": str(payload.get("subject") or ""),
                "sender_name": str(payload.get("sender_name") or ""),
                "sender_address": str(payload.get("sender_address") or ""),
                "target_folder": str(payload.get("target_folder") or ""),
                "categories": (
                    [item for item in categories if isinstance(item, str)]
                    if isinstance(categories, list)
                    else []
                ),
                "analysis": _analysis_for_stdout(payload.get("analysis")),
                "unsubscribe_suggestion": bool(payload.get("unsubscribe_suggestion")),
            }
        if payload.get("planned_actions") is not None:
            line["planned_actions"] = payload.get("planned_actions")
        plan_source = str(payload.get("plan_source") or plan_source)
    else:
        line = {
            "message_id": message_id,
            "subject": "",
            "sender_name": "",
            "sender_address": "",
            "target_folder": "",
            "categories": [],
            "analysis": dict(_SWIFT_ANALYSIS_DEFAULT),
            "unsubscribe_suggestion": False,
        }
    line["plan_source"] = plan_source
    line["actions"] = actions
    return line


def _analysis_for_stdout(raw: object) -> dict[str, Any]:
    analysis = dict(_SWIFT_ANALYSIS_DEFAULT)
    if not isinstance(raw, dict):
        return analysis
    for key in _SWIFT_ANALYSIS_DEFAULT:
        if key in raw:
            analysis[key] = raw[key]
    return analysis


def _failed_actions(detail: str) -> list[dict[str, Any]]:
    return [
        {
            "kind": "file_message",
            "description": "apply stored plan",
            "status": "failed",
            "detail": detail,
        }
    ]


def apply_selected(
    *,
    output_dir: Path,
    ids: tuple[str, ...],
    actuator: Actuator,
    mark_read: bool,
) -> tuple[int, int]:
    queue = LocalQueue(output_dir)
    payloads = queue.latest_payloads()
    action_log = ActionLog(output_dir)
    failures = 0
    emitted = 0
    for message_id in ids:
        payload = payloads.get(message_id)
        if payload is None:
            failures += 1
            emitted += 1
            print(
                json.dumps(
                    apply_stdout_line(
                        message_id=message_id,
                        payload=None,
                        actions=_failed_actions("message was not in the review queue"),
                    ),
                    ensure_ascii=False,
                )
            )
            continue
        try:
            record = ReviewRecord.from_dict(payload)
            plan = plan_from_stored(record, payload, mark_read)
        except (ValueError, PolicyViolation) as exc:
            failures += 1
            emitted += 1
            print(
                json.dumps(
                    apply_stdout_line(
                        message_id=message_id,
                        payload=payload,
                        actions=_failed_actions(str(exc)),
                    ),
                    ensure_ascii=False,
                )
            )
            continue
        applied = apply_plan(record, plan, actuator)
        if any(item.status == "failed" for item in applied):
            failures += 1
        action_log.append(
            record,
            str(payload.get("plan_source") or "stored"),
            getattr(actuator, "mode", "apply"),
            applied,
        )
        print(
            json.dumps(
                apply_stdout_line(
                    message_id=record.message_id,
                    payload=payload,
                    actions=[item.to_dict() for item in applied],
                ),
                ensure_ascii=False,
            )
        )
        emitted += 1
    return emitted, failures
