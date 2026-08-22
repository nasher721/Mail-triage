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
    return ids


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
                    {
                        "message_id": message_id,
                        "plan_source": "stored",
                        "actions": [
                            {
                                "kind": "file_message",
                                "description": "apply stored plan",
                                "status": "failed",
                                "detail": "message was not in the review queue",
                            }
                        ],
                    },
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
                    {
                        **{key: payload.get(key) for key in ("message_id", "subject", "sender_address", "target_folder")},
                        "plan_source": payload.get("plan_source") or "stored",
                        "actions": [
                            {
                                "kind": "file_message",
                                "description": "apply stored plan",
                                "status": "failed",
                                "detail": str(exc),
                            }
                        ],
                    },
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
        line = record.to_dict()
        line["plan_source"] = payload.get("plan_source") or "stored"
        line["planned_actions"] = payload.get("planned_actions")
        line["actions"] = [item.to_dict() for item in applied]
        print(json.dumps(line, ensure_ascii=False))
        emitted += 1
    return emitted, failures
