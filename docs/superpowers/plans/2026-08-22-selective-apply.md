# Selective Apply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Mac app preview a batch, then apply mailbox writes only for operator-checked message IDs, without re-screening or calling a model.

**Architecture:** Preview keeps screening unread mail and persists the stdout payload (record + `plan_source` + `planned_actions` + `actions`) to `review_queue.jsonl`. Apply-selected loads that queue, reconstitutes a policy-gated plan (draft text from `suggested_reply`), and actuates only IDs listed in an owner-only JSON file. CLI `--apply` without the ID file stays the launchd one-shot path.

**Tech Stack:** Python 3.11+ (`unittest`), Swift 5.10 / macOS 14 SwiftUI, generated `email_triage_standalone.py`.

## Global Constraints

- Never send, forward, delete, or download attachments.
- Apply-selected must not call `unread_messages`, `classify()`, or `agent.plan()`.
- Destinations stay under `AI Triage/`; stored JSON is untrusted and must pass `validate_action`.
- Draft body is reconstituted from `analysis.suggested_reply`, never from stored action JSON.
- Apply-selected does not rewrite `review_queue.jsonl`; it only appends `applied_actions.jsonl`.
- `--apply-ids-file` requires `--apply`, forbids `--watch`, allows only sources `owa` and `graph`, ignores `--include-previously-processed`.
- At most 200 IDs. Empty/missing/malformed ID file is configuration error exit `2`.
- Per-message failures continue the batch; process exit `1` if any failed.
- Tests stay synthetic: no Microsoft, no live model.
- Regenerate the standalone bundle whenever Python engine files change.

**Spec:** `docs/superpowers/specs/2026-08-22-five-improvements-design.md` (Slice 1 only). Slices 2–5 are out of scope.

---

## File map

| File | Responsibility |
| --- | --- |
| `src/email_triage/models.py` | `ReviewRecord.from_dict` to reload queue lines |
| `src/email_triage/pipeline.py` | Persist/load full queue payloads; latest-wins by `message_id` |
| `src/email_triage/actions.py` | `plan_from_stored` — rebuild + policy-gate a saved plan |
| `src/email_triage/selected.py` | Load ID file; apply listed IDs with no classifier |
| `src/email_triage/cli.py` | `--apply-ids-file` flag; branch into apply-selected |
| `tests/test_models.py` | Round-trip `ReviewRecord` |
| `tests/test_pipeline.py` | Queue payload + latest-wins |
| `tests/test_actions.py` | Stored-plan reconstruction and rejection |
| `tests/test_selected.py` | ID file + subset actuation |
| `tests/test_cli.py` | Flag validation |
| `Sources/MailTriageApp/Services/EngineService.swift` | `--apply-ids-file` on apply commands |
| `Sources/MailTriageApp/Support/ApplySelection.swift` | Pure selection/merge/ID-file helpers |
| `Sources/MailTriageApp/Models/AppModels.swift` | `TriageRecord.isApplied` |
| `Sources/MailTriageApp/Stores/AppStore.swift` | Selection state, ID file write, result merge |
| `Sources/MailTriageApp/Views/ResultsView.swift` | Checkboxes, Select All/None |
| `Sources/MailTriageApp/Views/ContentView.swift` | Confirmation copy uses selected count |
| `Tests/MailTriageAppTests/EngineTests.swift` | Command + selection tests |
| `email_triage_standalone.py` | Regenerated only via `tools/build_single_file.py` |
| `README.md` | Document `--apply-ids-file` |

---

### Task 1: Reload a ReviewRecord from queue JSON

**Files:**
- Modify: `src/email_triage/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: existing `ReviewRecord.to_dict()`, `ScreeningResult.from_dict`
- Produces: `ReviewRecord.from_dict(raw: dict[str, Any]) -> ReviewRecord`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py` (keep the existing import of `ScreeningResult` types; add `ReviewRecord` and `datetime`):

```python
from datetime import datetime, timezone

from email_triage.models import Confidence, ReviewRecord, Route, ScreeningResult, Topic, Urgency


class ReviewRecordRoundTripTests(unittest.TestCase):
    def test_from_dict_reloads_to_dict_payload(self) -> None:
        record = ReviewRecord(
            message_id="message-1",
            internet_message_id="<1@example.org>",
            subject="Meeting",
            sender_name="Alex",
            sender_address="alex@example.org",
            received_at=datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc),
            sensitivity="normal",
            has_attachments=False,
            target_folder="AI Triage/Needs Reply",
            categories=("AI - Soon", "AI - Scheduling", "AI - Processed"),
            analysis=ScreeningResult(
                summary="A colleague asks to schedule a meeting.",
                priority_score=3,
                action_items=("Confirm availability.",),
                route=Route.NEEDS_REPLY,
                response_required=True,
                confidence=Confidence.HIGH,
                urgency=Urgency.SOON,
                deadline="next Tuesday",
                topic=Topic.SCHEDULING,
                manual_review_reason=None,
                rationale="Direct scheduling request.",
                suggested_reply="Thanks.\n\nBest,\nNick",
            ),
            unsubscribe_suggestion=False,
            processing_error=None,
        )
        loaded = ReviewRecord.from_dict(record.to_dict())
        self.assertEqual(loaded.message_id, "message-1")
        self.assertEqual(loaded.target_folder, "AI Triage/Needs Reply")
        self.assertEqual(loaded.analysis.route, Route.NEEDS_REPLY)
        self.assertEqual(loaded.analysis.suggested_reply, "Thanks.\n\nBest,\nNick")
        self.assertEqual(loaded.categories, ("AI - Soon", "AI - Scheduling", "AI - Processed"))
        self.assertEqual(loaded.received_at, record.received_at)

    def test_from_dict_rejects_non_object(self) -> None:
        with self.assertRaises(ValueError):
            ReviewRecord.from_dict([])  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_models.ReviewRecordRoundTripTests -v`

Expected: FAIL with `type object 'ReviewRecord' has no attribute 'from_dict'`

- [ ] **Step 3: Write minimal implementation**

In `src/email_triage/models.py`, add this classmethod on `ReviewRecord` (keep `to_dict`):

```python
    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReviewRecord":
        if not isinstance(raw, dict):
            raise ValueError("review record must be an object")
        analysis_raw = raw.get("analysis")
        if not isinstance(analysis_raw, dict):
            raise ValueError("analysis must be an object")
        received_raw = raw.get("received_at")
        received_at: datetime | None
        if received_raw in (None, ""):
            received_at = None
        elif isinstance(received_raw, datetime):
            received_at = received_raw
        elif isinstance(received_raw, str):
            received_at = datetime.fromisoformat(received_raw)
        else:
            raise ValueError("received_at must be a string, datetime, or null")
        categories = raw.get("categories") or ()
        if not isinstance(categories, (list, tuple)) or not all(
            isinstance(item, str) for item in categories
        ):
            raise ValueError("categories must be a list of strings")
        internet_id = raw.get("internet_message_id")
        if internet_id is not None and not isinstance(internet_id, str):
            raise ValueError("internet_message_id must be a string or null")
        return cls(
            message_id=str(raw.get("message_id") or ""),
            internet_message_id=internet_id,
            subject=str(raw.get("subject") or ""),
            sender_name=str(raw.get("sender_name") or ""),
            sender_address=str(raw.get("sender_address") or ""),
            received_at=received_at,
            sensitivity=str(raw.get("sensitivity") or "normal"),
            has_attachments=bool(raw.get("has_attachments")),
            target_folder=str(raw.get("target_folder") or ""),
            categories=tuple(categories),
            analysis=ScreeningResult.from_dict(analysis_raw),
            unsubscribe_suggestion=bool(raw.get("unsubscribe_suggestion")),
            processing_error=(
                str(raw["processing_error"])
                if raw.get("processing_error") is not None
                else None
            ),
        )
```

If `message_id` is missing/empty, raise `ValueError("message_id is required")` before constructing.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_models -v`

Expected: PASS (existing ScreeningResult tests plus the new class)

- [ ] **Step 5: Commit**

```bash
git add src/email_triage/models.py tests/test_models.py
git commit -m "feat: reload review records from queue JSON"
```

---

### Task 2: Persist and reload full queue payloads

**Files:**
- Modify: `src/email_triage/pipeline.py` (`LocalQueue`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `ReviewRecord.to_dict` / `from_dict`
- Produces:
  - `LocalQueue.append(self, record: ReviewRecord, extra: dict[str, Any] | None = None) -> None`
  - `LocalQueue.latest_payloads(self) -> dict[str, dict[str, Any]]` — last line wins per `message_id`; skip malformed lines

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py`:

```python
class QueuePayloadTests(unittest.TestCase):
    def test_append_extra_fields_and_latest_wins(self) -> None:
        record = process_message(self.message(), FakeClassifier(), 12_000)
        with tempfile.TemporaryDirectory() as directory:
            queue = LocalQueue(Path(directory))
            queue.append(record, extra={"plan_source": "agent", "actions": []})
            queue.append(record, extra={"plan_source": "deterministic", "actions": [{"kind": "file_message"}]})
            (Path(directory) / "review_queue.jsonl").write_text(
                (Path(directory) / "review_queue.jsonl").read_text(encoding="utf-8")
                + "not-json\n",
                encoding="utf-8",
            )
            payloads = queue.latest_payloads()
        self.assertEqual(set(payloads), {"message-1"})
        self.assertEqual(payloads["message-1"]["plan_source"], "deterministic")
        self.assertEqual(payloads["message-1"]["actions"][0]["kind"], "file_message")
        self.assertNotIn("Can we meet", json.dumps(payloads["message-1"]))
```

Keep using the existing `PipelineTests.message` helper by making `QueuePayloadTests` a subclass of `PipelineTests`, or copy `message()`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_pipeline.QueuePayloadTests -v`

Expected: FAIL (`append() got an unexpected keyword argument 'extra'` or `latest_payloads`)

- [ ] **Step 3: Write minimal implementation**

In `LocalQueue.append`, accept `extra` and merge it into the JSON object. Add `latest_payloads`:

```python
    def append(self, record: ReviewRecord, extra: dict[str, Any] | None = None) -> None:
        self._prepare()
        payload = record.to_dict()
        if extra:
            payload.update(extra)
        with self.queue_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        os.chmod(self.queue_path, 0o600)
        self._seen.add(record.message_id)
        self.state_path.write_text(
            json.dumps(sorted(self._seen), indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(self.state_path, 0o600)

    def latest_payloads(self) -> dict[str, dict[str, Any]]:
        if not self.queue_path.is_file():
            return {}
        latest: dict[str, dict[str, Any]] = {}
        try:
            lines = self.queue_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            message_id = payload.get("message_id")
            if isinstance(message_id, str) and message_id:
                latest[message_id] = payload
        return latest
```

Import `Any` in `pipeline.py` if it is not already imported.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_pipeline -v`

Expected: PASS, including `test_local_queue_does_not_store_message_body_and_is_idempotent`

- [ ] **Step 5: Commit**

```bash
git add src/email_triage/pipeline.py tests/test_pipeline.py
git commit -m "feat: store full triage payloads in the review queue"
```

---

### Task 3: Rebuild a policy-gated plan from stored JSON

**Files:**
- Modify: `src/email_triage/actions.py`
- Test: `tests/test_actions.py`

**Interfaces:**
- Consumes: `MailboxAction`, `validate_action`, `default_plan`, `ReviewRecord`
- Produces: `plan_from_stored(record: ReviewRecord, payload: dict[str, Any], allow_mark_read: bool) -> list[MailboxAction]`

Rules (lock these in the function docstring):

1. If `payload["planned_actions"]` is a non-empty list of objects, rebuild each `MailboxAction`. For `draft_reply`, set `reply_body` from `record.analysis.suggested_reply` (ignore any stored reply text). Validate each action. Raise `PolicyViolation` on bad kind/folder.
2. Else if `payload["actions"]` is a non-empty list with `kind` values, rebuild from those kinds using `record.target_folder`, `record.categories`, and `suggested_reply`.
3. Else return `default_plan(record, allow_mark_read)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_actions.py`:

```python
from email_triage.actions import plan_from_stored


class StoredPlanTests(unittest.TestCase):
    def test_planned_actions_use_suggested_reply_not_stored_text(self) -> None:
        record = needs_reply_record()
        payload = {
            "planned_actions": [
                {"kind": "tag_message", "folder": None, "categories": list(record.categories), "drafts_reply": False},
                {"kind": "draft_reply", "folder": None, "categories": [], "drafts_reply": True},
                {"kind": "file_message", "folder": "AI Triage/Needs Reply", "categories": [], "drafts_reply": False},
            ]
        }
        plan = plan_from_stored(record, payload, allow_mark_read=False)
        draft = next(action for action in plan if action.kind == ActionKind.DRAFT_REPLY)
        self.assertEqual(draft.reply_body, REPLY_TEXT)

    def test_tampered_folder_is_rejected(self) -> None:
        record = needs_reply_record()
        payload = {
            "planned_actions": [
                {"kind": "file_message", "folder": "Inbox", "categories": [], "drafts_reply": False},
            ]
        }
        with self.assertRaises(PolicyViolation):
            plan_from_stored(record, payload, allow_mark_read=False)

    def test_missing_plan_uses_default_plan(self) -> None:
        record = needs_reply_record()
        plan = plan_from_stored(record, {}, allow_mark_read=False)
        self.assertEqual([action.kind for action in plan], [action.kind for action in default_plan(record, False)])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_actions.StoredPlanTests -v`

Expected: FAIL with `cannot import name 'plan_from_stored'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/email_triage/actions.py`:

```python
def _action_from_stored_item(item: dict[str, Any], record: ReviewRecord) -> MailboxAction:
    if not isinstance(item, dict):
        raise PolicyViolation("stored plan entries must be objects")
    try:
        kind = ActionKind(str(item.get("kind") or ""))
    except ValueError as exc:
        raise PolicyViolation(f"unsupported action {item.get('kind')!r}") from exc
    if kind == ActionKind.FILE_MESSAGE:
        folder = item.get("folder")
        if not isinstance(folder, str):
            raise PolicyViolation("file_message requires a folder string")
        return MailboxAction(kind=kind, folder=folder)
    if kind == ActionKind.TAG_MESSAGE:
        categories = item.get("categories") or record.categories
        if isinstance(categories, str):
            categories = [categories]
        if not isinstance(categories, (list, tuple)) or not all(
            isinstance(entry, str) for entry in categories
        ):
            raise PolicyViolation("tag_message requires a list of category strings")
        return MailboxAction(kind=kind, categories=tuple(categories))
    if kind == ActionKind.DRAFT_REPLY:
        return MailboxAction(kind=kind, reply_body=record.analysis.suggested_reply)
    if kind == ActionKind.MARK_READ:
        return MailboxAction(kind=kind)
    raise PolicyViolation(f"unsupported action {kind!r}")


def plan_from_stored(
    record: ReviewRecord,
    payload: dict[str, Any],
    allow_mark_read: bool,
) -> list[MailboxAction]:
    """Rebuild a plan from queue JSON. Reply text always comes from the record."""

    planned = payload.get("planned_actions")
    kinds_source: list[Any] | None = None
    if isinstance(planned, list) and planned:
        kinds_source = planned
    elif isinstance(payload.get("actions"), list) and payload["actions"]:
        kinds_source = payload["actions"]
    if not kinds_source:
        return default_plan(record, allow_mark_read)
    rebuilt = [_action_from_stored_item(item, record) for item in kinds_source]
    return normalize_plan(
        [validate_action(action, record, allow_mark_read) for action in rebuilt]
    )
```

For the `actions` fallback, `_action_from_stored_item` must treat a missing `folder` on `file_message` as `record.target_folder`:

```python
    if kind == ActionKind.FILE_MESSAGE:
        folder = item.get("folder") or record.target_folder
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_actions -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/email_triage/actions.py tests/test_actions.py
git commit -m "feat: rebuild mailbox plans from stored queue JSON"
```

---

### Task 4: Apply a listed ID subset without screening

**Files:**
- Create: `src/email_triage/selected.py`
- Create: `tests/test_selected.py`
- Create: `tests/test_selected.py` (import `FakeGraphMailbox` from `test_apply`)

**Interfaces:**
- Consumes: `LocalQueue.latest_payloads`, `ReviewRecord.from_dict`, `plan_from_stored`, `apply_plan`, `ActionLog`, `GraphActuator` / `DryRunActuator`, `ConfigurationError`
- Produces:
  - `MAX_APPLY_IDS = 200`
  - `load_apply_ids(path: Path) -> tuple[str, ...]`
  - `apply_selected(*, output_dir: Path, ids: tuple[str, ...], actuator: Actuator, mark_read: bool) -> tuple[int, int]` returning `(emitted, failures)`
  - Prints one JSON line per requested ID (same stdout shape as `cli.run`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_selected.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path
from stat import S_IMODE
from email_triage.actions import default_plan
from email_triage.apply import GraphActuator
from email_triage.config import ConfigurationError
from email_triage.pipeline import LocalQueue
from dataclasses import replace

from email_triage.selected import MAX_APPLY_IDS, apply_selected, load_apply_ids
from test_actions import needs_reply_record
from test_apply import FakeGraphMailbox


class LoadApplyIdsTests(unittest.TestCase):
    def test_loads_owner_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ids.json"
            path.write_text('{"message_ids": ["a", "b"]}\n', encoding="utf-8")
            self.assertEqual(load_apply_ids(path), ("a", "b"))

    def test_empty_or_too_many_or_malformed_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ids.json"
            path.write_text('{"message_ids": []}\n', encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_apply_ids(path)
            path.write_text('{"message_ids": %s}\n' % json.dumps(["x"] * (MAX_APPLY_IDS + 1)), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_apply_ids(path)
            path.write_text("[]\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_apply_ids(path)
            with self.assertRaises(ConfigurationError):
                load_apply_ids(Path(directory) / "missing.json")


class ApplySelectedTests(unittest.TestCase):
    def test_actuates_only_listed_ids_without_classifier(self) -> None:
        first = needs_reply_record()
        second = replace(needs_reply_record(), message_id="message-2")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            queue = LocalQueue(output)
            for record in (first, second):
                extra = {
                    "plan_source": "deterministic",
                    "planned_actions": [action.to_dict() for action in default_plan(record, False)],
                    "actions": [],
                }
                queue.append(record, extra=extra)
            mailbox = FakeGraphMailbox()
            emitted, failures = apply_selected(
                output_dir=output,
                ids=("message-1",),
                actuator=GraphActuator(mailbox),
                mark_read=False,
            )
        self.assertEqual((emitted, failures), (1, 0))
        self.assertTrue(any(call[0] == "move_message" for call in mailbox.calls))
        moved_ids = [call[1] for call in mailbox.calls if call[0] == "move_message"]
        self.assertEqual(moved_ids, ["message-1"])

    def test_missing_id_fails_and_continues(self) -> None:
        record = needs_reply_record()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            LocalQueue(output).append(
                record,
                extra={
                    "plan_source": "deterministic",
                    "planned_actions": [action.to_dict() for action in default_plan(record, False)],
                },
            )
            mailbox = FakeGraphMailbox()
            emitted, failures = apply_selected(
                output_dir=output,
                ids=("message-1", "missing"),
                actuator=GraphActuator(mailbox),
                mark_read=False,
            )
        self.assertEqual(emitted, 2)
        self.assertEqual(failures, 1)

    def test_mailbox_404_fails_that_row(self) -> None:
        record = needs_reply_record()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            LocalQueue(output).append(
                record,
                extra={
                    "plan_source": "deterministic",
                    "planned_actions": [action.to_dict() for action in default_plan(record, False)],
                },
            )
            mailbox = FakeGraphMailbox(fail_on="ensure_folder_path")
            emitted, failures = apply_selected(
                output_dir=output,
                ids=("message-1",),
                actuator=GraphActuator(mailbox),
                mark_read=False,
            )
        self.assertEqual((emitted, failures), (1, 1))

    def test_does_not_rewrite_review_queue(self) -> None:
        record = needs_reply_record()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            queue = LocalQueue(output)
            queue.append(record, extra={"plan_source": "deterministic"})
            before = queue.queue_path.read_text(encoding="utf-8")
            apply_selected(
                output_dir=output,
                ids=("message-1",),
                actuator=GraphActuator(FakeGraphMailbox()),
                mark_read=False,
            )
            self.assertEqual(queue.queue_path.read_text(encoding="utf-8"), before)
            self.assertTrue((output / "applied_actions.jsonl").is_file())
            self.assertEqual(S_IMODE((output / "applied_actions.jsonl").stat().st_mode), 0o600)
```

`ReviewRecord` is frozen; `object.__setattr__(second, "message_id", "message-2")` works. Prefer `dataclasses.replace(second, message_id="message-2")` instead:

```python
from dataclasses import replace
second = replace(needs_reply_record(), message_id="message-2")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_selected -v`

Expected: FAIL (`No module named 'email_triage.selected'`)

- [ ] **Step 3: Write minimal implementation**

Create `src/email_triage/selected.py`:

```python
"""Apply previously screened messages by ID without re-reading the mailbox."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from email_triage.actions import PolicyViolation, plan_from_stored
from email_triage.apply import ActionLog, Actuator, AppliedAction, apply_plan
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
```

Do not import or call a classifier. Do not import `AppliedAction` unless you use it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_selected tests.test_apply tests.test_safety -v`

Expected: PASS. `test_safety` still proves no send/delete surface in safety helpers.

- [ ] **Step 5: Commit**

```bash
git add src/email_triage/selected.py tests/test_selected.py
git commit -m "feat: apply stored triage plans for selected message IDs"
```

---

### Task 5: Wire CLI `--apply-ids-file`

**Files:**
- Modify: `src/email_triage/cli.py`
- Modify: `README.md` (Run / flags paragraph)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `load_apply_ids`, `apply_selected`, `GraphActuator`, `_build_graph_mailbox`, `OwaMailbox`
- Produces: argparse flag `--apply-ids-file`; `run()` returns early into apply-selected when the flag is set; `main()` rejects `--watch` + `--apply-ids-file`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
from email_triage.cli import build_parser, main
from email_triage.config import ConfigurationError


class ApplyIdsCliTests(unittest.TestCase):
    def test_parser_accepts_apply_ids_file(self) -> None:
        args = build_parser().parse_args(
            ["--source", "owa", "--apply", "--apply-ids-file", "/tmp/ids.json"]
        )
        self.assertEqual(args.apply_ids_file, "/tmp/ids.json")
        self.assertTrue(args.apply)

    def test_watch_and_ids_file_are_rejected(self) -> None:
        with patch("sys.argv", ["email-triage", "--owa", "--apply", "--apply-ids-file", "x", "--watch", "30"]):
            self.assertEqual(main(), 2)
```

`main()` prints `error: ...` to stderr and returns 2 for `ConfigurationError`. If `build_parser` does not yet have the flag, parse_args fails in step 2 — that is the expected red test.

Also add a unit test that `run` with `--apply-ids-file` never calls `build_classifier`. After the flag exists, this belongs in `tests/test_cli.py` with patches:

```python
    def test_apply_ids_skips_classifier_and_unread_scan(self) -> None:
        args = build_parser().parse_args(
            ["--source", "graph", "--apply", "--apply-ids-file", "/tmp/ids.json", "--non-interactive"]
        )
        with (
            patch("email_triage.cli.Settings.from_env") as settings_from_env,
            patch("email_triage.cli.load_apply_ids", return_value=("message-1",)) as load_ids,
            patch("email_triage.cli.apply_selected", return_value=(1, 0)) as apply_sel,
            patch("email_triage.cli.build_classifier") as build_classifier,
            patch("email_triage.cli._build_graph_mailbox") as build_mailbox,
            patch("email_triage.cli.GraphActuator") as actuator_cls,
        ):
            settings = settings_from_env.return_value
            settings.mailbox_source = "graph"
            settings.apply_changes = True
            settings.mark_read = False
            settings.output_dir = Path("/tmp")
            from email_triage.cli import run
            code = run(args)
        self.assertEqual(code, 0)
        load_ids.assert_called_once()
        apply_sel.assert_called_once()
        build_classifier.assert_not_called()
        build_mailbox.assert_called_once()
        actuator_cls.assert_called_once()
```

Import `Path` and `run`. For `local` source, `run` should raise `ConfigurationError` before touching a classifier:

```python
    def test_apply_ids_rejects_local_source(self) -> None:
        args = build_parser().parse_args(
            ["--source", "local", "--input", "samples/inbox.jsonl", "--apply", "--apply-ids-file", "/tmp/ids.json"]
        )
        with patch.dict("os.environ", {"TRIAGE_PROVIDER": "ollama"}, clear=False):
            with self.assertRaises(ConfigurationError):
                from email_triage.cli import run
                run(args)
```

If `Settings.from_env` needs a local input, that path is fine. Prefer patching `Settings.from_env` to return `mailbox_source="local"` and assert `ConfigurationError` matching `apply-ids-file supports only owa or graph`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_cli.ApplyIdsCliTests -v`

Expected: FAIL (`unrecognized arguments: --apply-ids-file`)

- [ ] **Step 3: Write minimal implementation**

In `build_parser()`, after `--apply`:

```python
    parser.add_argument(
        "--apply-ids-file",
        help=(
            "JSON file {\"message_ids\": [...]} of already-screened message IDs to apply. "
            "Requires --apply. Does not re-read unread mail or call a model. "
            "Valid only with --source owa or graph."
        ),
    )
```

In `main()`, after `Settings.from_env` validation and before the watch loop (with the other flag conflicts):

```python
        if getattr(args, "apply_ids_file", None):
            if not args.apply:
                raise ConfigurationError("--apply-ids-file requires --apply")
            if watch_seconds is not None:
                raise ConfigurationError(
                    "--apply-ids-file cannot be combined with --watch"
                )
```

At the top of `run()`, after `Settings.from_env` and the `--login` branch:

```python
    if args.apply_ids_file:
        if settings.mailbox_source not in {"owa", "graph"}:
            raise ConfigurationError("--apply-ids-file supports only --source owa or graph")
        if not settings.apply_changes:
            raise ConfigurationError("--apply-ids-file requires --apply")
        ids = load_apply_ids(Path(args.apply_ids_file).expanduser())
        if settings.mailbox_source == "owa":
            mailbox = OwaMailbox(
                settings.owa_cdp_url,
                read_write=True,
                max_scan_pages=settings.max_retrieval_pages,
            )
        else:
            mailbox = _build_graph_mailbox(settings, interactive)
        actuator = GraphActuator(mailbox)
        _emitted, failures = apply_selected(
            output_dir=settings.output_dir,
            ids=ids,
            actuator=actuator,
            mark_read=settings.mark_read,
        )
        print(
            "Mailbox updated for selected messages; no mail was sent, forwarded, or deleted.",
            file=sys.stderr,
        )
        return 1 if failures else 0
```

Import `load_apply_ids`, `apply_selected`, and `Path` if needed. Do not call `build_classifier` or `unread_messages` on this path. `--include-previously-processed` is unused here (ignored).

In `README.md` under the `--apply` flags paragraph, add one sentence: `--apply-ids-file PATH` applies a previously previewed subset (JSON `message_ids`, max 200) without re-screening; requires `--apply` and `--source owa` or `graph`.

Do not change the no-ID `--apply` loop except as needed for Task 6.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_cli tests.test_selected -v`

Expected: PASS, `main()` with `--watch` returns `2`

- [ ] **Step 5: Commit**

```bash
git add src/email_triage/cli.py tests/test_cli.py README.md
git commit -m "feat: add --apply-ids-file for subset mailbox writes"
```

---

### Task 6: Preview writes planned actions onto the queue

**Files:**
- Modify: `src/email_triage/cli.py` (the existing per-message loop only)
- Test: `tests/test_cli.py` or a focused test that drives the loop via a tiny helper

The screening loop currently:

```python
        queue.append(record)
        plan, plan_source = agent.plan(record, settings.mark_read)
        ...
        payload = record.to_dict()
        payload["plan_source"] = plan_source
        payload["actions"] = [item.to_dict() for item in applied]
        print(json.dumps(payload, ensure_ascii=False))
```

Change `queue.append` so it happens **after** the plan exists and stores the same object printed to stdout, including `planned_actions`.

- [ ] **Step 1: Write the failing test**

Extracting the loop is more than needed. Add `tests/test_pipeline.py` coverage is already done; add a CLI-level test that mocks one unread message:

```python
class PreviewQueueTests(unittest.TestCase):
    def test_preview_queue_line_includes_plan(self) -> None:
        from email_triage.actions import default_plan
        from email_triage.cli import run
        from test_actions import needs_reply_record

        record = needs_reply_record()
        args = build_parser().parse_args(["--source", "local", "--input", "samples/inbox.jsonl", "--no-agent"])
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory)
            with (
                patch("email_triage.cli.Settings.from_env") as from_env,
                patch("email_triage.cli.build_classifier") as build_classifier,
                patch("email_triage.cli.build_agent") as build_agent,
                patch("email_triage.cli.LocalMailbox") as local_cls,
                patch("email_triage.cli.process_message", return_value=record),
            ):
                settings = from_env.return_value
                settings.mailbox_source = "local"
                settings.input_path = Path("samples/inbox.jsonl")
                settings.apply_changes = False
                settings.mark_read = False
                settings.max_unread_messages = 1
                settings.max_body_characters = 12000
                settings.intercept_clinical = True
                settings.feedback_path = None
                settings.output_dir = settings_path
                settings.use_agent = False
                settings.screening.keeps_data_local = True
                settings.screening.base_url = "http://127.0.0.1"
                build_classifier.return_value.client.reachable.return_value = (True, "ok")
                build_classifier.return_value.client.profile.label = "Ollama"
                build_classifier.return_value.model = "x"
                build_agent.return_value.plan.return_value = (default_plan(record, False), "deterministic")
                local_cls.return_value.unread_messages.return_value = [object()]
                code = run(args)
            line = json.loads((settings_path / "review_queue.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(code, 0)
        self.assertEqual(line["plan_source"], "deterministic")
        self.assertTrue(line["planned_actions"])
        self.assertEqual(line["planned_actions"][0]["kind"], "tag_message")
        self.assertIn("actions", line)
```

This test is coupled to `Settings` attribute shape. If `from_env` patching is too brittle, instead add a tiny helper in `cli.py`:

```python
def queue_preview_payload(record, plan, plan_source, applied) -> dict[str, object]:
    payload = record.to_dict()
    payload["plan_source"] = plan_source
    payload["planned_actions"] = [action.to_dict() for action in plan]
    payload["actions"] = [item.to_dict() for item in applied]
    return payload
```

Then unit-test **that** helper without mocking Settings:

```python
    def test_queue_preview_payload_includes_plan(self) -> None:
        from email_triage.actions import default_plan
        from email_triage.apply import apply_plan, DryRunActuator
        from email_triage.cli import queue_preview_payload
        from test_actions import needs_reply_record

        record = needs_reply_record()
        plan = default_plan(record, False)
        applied = apply_plan(record, plan, DryRunActuator())
        payload = queue_preview_payload(record, plan, "deterministic", applied)
        self.assertEqual(payload["plan_source"], "deterministic")
        self.assertEqual(payload["planned_actions"][0]["kind"], "tag_message")
        self.assertEqual(payload["actions"][0]["status"], "planned")
        self.assertNotIn("Tuesday afternoon", json.dumps(payload["planned_actions"]))
```

Prefer this helper. The screening loop must call `queue.append(record, extra={k: payload[k] for k in ("plan_source", "planned_actions", "actions")})` **after** `apply_plan`, using the same `payload` that is printed.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_cli.PreviewQueueTests -v`

Expected: FAIL (`cannot import name 'queue_preview_payload'`)

- [ ] **Step 3: Write minimal implementation**

Add `queue_preview_payload` in `cli.py` and replace the loop tail with:

```python
        plan, plan_source = agent.plan(record, settings.mark_read)
        if not plan:
            plan, plan_source = default_plan(record, settings.mark_read), "deterministic"
        applied = apply_plan(record, plan, actuator)
        action_log.append(record, plan_source, actuator.mode, applied)
        payload = queue_preview_payload(record, plan, plan_source, applied)
        queue.append(
            record,
            extra={
                "plan_source": payload["plan_source"],
                "planned_actions": payload["planned_actions"],
                "actions": payload["actions"],
            },
        )
        if any(item.status == "failed" for item in applied):
            failures += 1
        print(json.dumps(payload, ensure_ascii=False))
        processed += 1
```

Remove the previous `queue.append(record)` that ran **before** planning.

Keep `processed_message_ids.json` updates (still inside `queue.append`). Launchd `--apply` without an ID file still screens then files.

- [ ] **Step 4: Run tests**

Run: `python -m unittest discover -s tests -t tests -v`

Expected: all PASS. Then regenerate the bundle:

```bash
python tools/build_single_file.py
python tools/build_single_file.py --check
python email_triage_standalone.py --self-test
```

Expected: `--check` silent/exit 0; self-test PASS.

- [ ] **Step 5: Commit**

```bash
git add src/email_triage/cli.py tests/test_cli.py email_triage_standalone.py
git commit -m "feat: persist planned actions with preview queue rows"
```

---

### Task 7: Swift apply-ids command and pure selection helpers

**Files:**
- Create: `Sources/MailTriageApp/Support/ApplySelection.swift`
- Modify: `Sources/MailTriageApp/Models/AppModels.swift` (`TriageRecord.isApplied`)
- Modify: `Sources/MailTriageApp/Services/EngineService.swift` (`EngineConfiguration.applyIdsFile`, `EngineCommandBuilder.triage`)
- Modify: `Tests/MailTriageAppTests/EngineTests.swift`

**Interfaces:**
- Consumes: existing `TriageRecord` / `EngineConfiguration`
- Produces:
  - `TriageRecord.isApplied: Bool` — true when any action `status == "applied"`
  - `enum ApplySelection` with `selectAllFiltered`, `selectNoneFiltered`, `merge`, `idsForApply`, `document(from:)`
  - `EngineConfiguration.applyIdsFile: String`
  - Apply commands include `--apply` and `--apply-ids-file <path>`; apply without a path throws

- [ ] **Step 1: Write the failing tests**

Add a helper record in `EngineTests.swift` (reuse the JSON from `recordParserDecodesJSONLines`, vary `message_id` / action status):

```swift
private func record(id: String, status: String) throws -> TriageRecord {
    let line = """
    {"message_id":"\(id)","subject":"Quarterly update","sender_name":"Alex","sender_address":"alex@example.org","target_folder":"AI Triage/Needs Reply","categories":["AI Triage"],"analysis":{"route":"needs_reply","urgency":"soon","topic":"administrative","confidence":"high","priority_score":4,"summary":"Needs a response.","action_items":["Reply"],"suggested_reply":"Thanks\\n\\nBest,\\nNick","manual_review_reason":null,"deadline":null},"plan_source":"agent","actions":[{"kind":"file_message","description":"move","status":"\(status)","detail":null}]}
    """
    return try EngineParser.records(from: line)[0]
}

@Test func appliedRecordsAreDetectedFromActionStatus() throws {
    #expect(try record(id: "m1", status: "planned").isApplied == false)
    #expect(try record(id: "m2", status: "applied").isApplied == true)
}

@Test func applySelectionHonorsFilterAndSkipsApplied() throws {
    let planned = try record(id: "a", status: "planned")
    let applied = try record(id: "b", status: "applied")
    let hidden = try record(id: "c", status: "planned")
    var selected: Set<String> = ["c"]
    selected = ApplySelection.selectAllFiltered(current: selected, filtered: [planned, applied])
    #expect(selected == ["a", "c"])
    selected = ApplySelection.selectNoneFiltered(current: selected, filtered: [planned, applied])
    #expect(selected == ["c"])
    #expect(ApplySelection.idsForApply(selected: ["a", "b", "c"], records: [planned, applied, hidden]) == ["a", "c"])
}

@Test func applyMergesByMessageIDWithoutDroppingRows() throws {
    let existing = [try record(id: "a", status: "planned"), try record(id: "b", status: "planned")]
    let applied = [try record(id: "a", status: "applied")]
    let merged = ApplySelection.merge(existing: existing, applied: applied)
    #expect(merged.count == 2)
    #expect(merged[0].isApplied)
    #expect(!merged[1].isApplied)
}

@Test func applyIdsJSONUsesSnakeCaseMessageIds() throws {
    let data = try ApplySelection.jsonDocument(messageIDs: ["m1", "m2"])
    let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
    #expect(object?["message_ids"] as? [String] == ["m1", "m2"])
}

@Test func applyCommandRequiresIdsFileAndForwardsPathLiterally() throws {
    var apply = configuration
    apply.runMode = .apply
    #expect(throws: EngineFailure.self) {
        try EngineCommandBuilder.triage(apply)
    }
    apply.applyIdsFile = "/tmp/mail-triage/apply-ids.json"
    let command = try EngineCommandBuilder.triage(apply)
    #expect(command.arguments.contains("--apply"))
    #expect(command.arguments.contains("--apply-ids-file"))
    #expect(command.arguments.contains("/tmp/mail-triage/apply-ids.json"))

    apply.source = .accessibility
    apply.applyIdsFile = "/tmp/x.json"
    #expect(throws: EngineFailure.self) {
        try EngineCommandBuilder.triage(apply)
    }
}
```

Update the existing `applyCommandIsExplicitAndRejectedForPreviewOnlySource` test so it sets `applyIdsFile` **or delete it** in favor of `applyCommandRequiresIdsFileAndForwardsPathLiterally` — do not leave two contradictory tests.

Add `applyIdsFile: ""` to the `configuration` helper and to `AppStore.configuration` in Task 8. For this task, only the test helper and `EngineConfiguration` need the new stored property so tests compile.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./script/test_mac_app.sh`

Expected: compile error (`isApplied`, `ApplySelection`, `applyIdsFile` missing) or test fail.

If Swift tests cannot load Testing.framework in this environment, still write the tests; note the failure mode. Do not skip adding them.

- [ ] **Step 3: Write minimal implementation**

On `TriageRecord`:

```swift
    var isApplied: Bool {
        actions.contains { $0.status == "applied" }
    }
```

Create `Sources/MailTriageApp/Support/ApplySelection.swift`:

```swift
import Foundation

enum ApplySelection {
    static func selectAllFiltered(current: Set<String>, filtered: [TriageRecord]) -> Set<String> {
        current.union(filtered.filter { !$0.isApplied }.map(\.messageID))
    }

    static func selectNoneFiltered(current: Set<String>, filtered: [TriageRecord]) -> Set<String> {
        current.subtracting(filtered.map(\.messageID))
    }

    static func merge(existing: [TriageRecord], applied: [TriageRecord]) -> [TriageRecord] {
        let updates = Dictionary(uniqueKeysWithValues: applied.map { ($0.messageID, $0) })
        return existing.map { updates[$0.messageID] ?? $0 }
    }

    static func idsForApply(selected: Set<String>, records: [TriageRecord]) -> [String] {
        records
            .filter { selected.contains($0.messageID) && !$0.isApplied }
            .map(\.messageID)
    }

    static func jsonDocument(messageIDs: [String]) throws -> Data {
        let payload = ["message_ids": messageIDs]
        return try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    }
}
```

Add `var applyIdsFile: String = ""` to `EngineConfiguration`. In `EngineCommandBuilder.triage`:

```swift
        if configuration.runMode == .apply {
            guard configuration.source.supportsApply else {
                throw EngineFailure.launchFailed("The selected source is preview-only.")
            }
            let idsFile = configuration.applyIdsFile.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !idsFile.isEmpty else {
                throw EngineFailure.launchFailed("Select at least one previewed message to apply.")
            }
            operation.append("--apply")
            operation.append(contentsOf: ["--apply-ids-file", idsFile])
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./script/test_mac_app.sh`

Expected: PASS (or compile + `swift test --skip-build` PASS)

- [ ] **Step 5: Commit**

```bash
git add Sources/MailTriageApp Tests/MailTriageAppTests
git commit -m "feat: require an apply-ids file on Mac apply commands"
```

---

### Task 8: Results checkboxes, confirmation count, merge

**Files:**
- Modify: `Sources/MailTriageApp/Stores/AppStore.swift`
- Modify: `Sources/MailTriageApp/Views/ResultsView.swift`
- Modify: `Sources/MailTriageApp/Views/ContentView.swift`

**Interfaces:**
- Consumes: `ApplySelection`, `EngineConfiguration.applyIdsFile`
- Produces: `selectedApplyIDs: Set<String>`; `canApplySelection`; `writeApplyIdsFile() throws -> String`; preview selects all; apply merges and unchecks applied rows

- [ ] **Step 1: Confirm Task 7 tests still pass before UI wiring**

Run: `./script/test_mac_app.sh`

Expected: PASS. Task 7 already covers selection, merge, ID JSON, and the apply command. This task only wires those helpers into the app.

Checklist this wiring must satisfy:

1. After preview, every new row is checked.
2. Select All / None changes only `filteredResults`.
3. Apply button disabled when `runMode == .apply` and no selectable checked rows, or source is preview-only.
4. Confirmation message uses `store.selectedApplyCount`, not `results.count`.
5. Apply completion replaces matching rows via `ApplySelection.merge` and removes applied IDs from `selectedApplyIDs`.

- [ ] **Step 2: Write the implementation**

In `AppStore`:

```swift
    @Published var selectedApplyIDs: Set<String> = []

    var selectedApplyCount: Int {
        ApplySelection.idsForApply(selected: selectedApplyIDs, records: results).count
    }

    var canApplySelection: Bool {
        canRun && source.supportsApply && selectedApplyCount > 0
    }
```

Extend `configuration` to pass `applyIdsFile: applyIdsURL.path` where `applyIdsURL` is `URL(fileURLWithPath: outputDirectory).appendingPathComponent("apply-ids.json")`.

```swift
    func selectAllFilteredForApply() {
        selectedApplyIDs = ApplySelection.selectAllFiltered(
            current: selectedApplyIDs,
            filtered: filteredResults
        )
    }

    func selectNoneFilteredForApply() {
        selectedApplyIDs = ApplySelection.selectNoneFiltered(
            current: selectedApplyIDs,
            filtered: filteredResults
        )
    }

    func writeApplyIdsFile() throws -> String {
        let ids = ApplySelection.idsForApply(selected: selectedApplyIDs, records: results)
        guard !ids.isEmpty else {
            throw EngineFailure.launchFailed("Select at least one previewed message to apply.")
        }
        if ids.count > 200 {
            throw EngineFailure.launchFailed("Select at most 200 messages to apply.")
        }
        let directory = URL(fileURLWithPath: outputDirectory, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent("apply-ids.json")
        try ApplySelection.jsonDocument(messageIDs: ids).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        return url.path
    }
```

Hold the path on the configuration for the upcoming launch:

```swift
    private var pendingApplyIdsFile = ""

    var configuration: EngineConfiguration {
        EngineConfiguration(
            // existing fields...
            outputDirectory: outputDirectory,
            applyIdsFile: runMode == .apply ? pendingApplyIdsFile : ""
        )
    }
```

`requestRun` / `runTriage`:

```swift
    func requestRun() {
        if runMode == .apply {
            showApplyConfirmation = true
        } else {
            runTriage()
        }
    }

    func runTriage(using override: EngineConfiguration? = nil) {
        persistSettings()
        if (override ?? self).runMode == .apply || (override == nil && runMode == .apply) {
            do {
                pendingApplyIdsFile = try writeApplyIdsFile()
            } catch {
                handle(error)
                return
            }
        } else {
            pendingApplyIdsFile = ""
        }
        let configuration = override ?? self.configuration
        // existing launch...
    }
```

Scheduled preview must keep `runMode = .preview` on the override (already does) so it never writes an ID file.

In the preview completion:

```swift
                let newRecords = try EngineParser.records(from: output.stdout)
                if configuration.runMode == .apply {
                    self.results = ApplySelection.merge(existing: self.results, applied: newRecords)
                    self.selectedApplyIDs.subtract(self.results.filter(\.isApplied).map(\.messageID))
                } else {
                    self.results = newRecords
                    self.selectedApplyIDs = Set(newRecords.filter { !$0.isApplied }.map(\.messageID))
                    self.selectedResultID = newRecords.first?.id
                }
```

For apply, keep `selectedResultID` if it still exists.

`canRun` for the toolbar: when `runMode == .apply`, disable unless `canApplySelection`. Preview still uses `canRun`.

```swift
    var canStartToolbarRun: Bool {
        runMode == .apply ? canApplySelection : canRun
    }
```

In `ResultsView` list row, add a checkbox `Toggle` bound to membership in `selectedApplyIDs`, disabled when `record.isApplied`. Add buttons “Select All” and “Select None” on the filter bar.

In `ContentView` confirmation:

```swift
            "Apply changes to Outlook?",
            ...
            Button("Apply Moves, Categories, and Drafts", role: .destructive) {
                store.runTriage()
            }
        } message: {
            Text("Mail Triage will update \(store.selectedApplyCount) message(s). It never sends, forwards, or deletes. It may move messages, add categories, save unsent drafts, and optionally mark filed messages read. Unchecked mail stays in Inbox.")
        }
```

Toolbar Apply button `.disabled(!store.canStartToolbarRun)`.

- [ ] **Step 3: Run tests**

Run: `./script/test_mac_app.sh` and `python -m unittest discover -s tests -t tests -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add Sources/MailTriageApp Tests/MailTriageAppTests
git commit -m "feat: apply only checked Mail Triage results"
```

---

## Plan self-review

**Spec coverage (Slice 1):**

| Requirement | Task |
| --- | --- |
| Preview persists plan on queue | 6 |
| Apply does not fetch unread / call model | 4, 5 |
| `--apply-ids-file` + `--apply`, no `--watch` | 5 |
| owa/graph only | 5 |
| include-previously-processed ignored | 5 (unused on that path) |
| max 200 / empty file → exit 2 | 4 |
| Missing ID / 404 / policy fail continue | 3, 4 |
| Draft from `suggested_reply` | 3 |
| Do not rewrite queue on apply | 4 |
| Missing plan → `default_plan` | 3 |
| CLI `--apply` without IDs unchanged | 5, 6 (loop still screens+files) |
| Mac checkboxes, filtered select all/none | 7, 8 |
| Confirmation uses selected count | 8 |
| Merge results, uncheck applied, omit applied IDs | 7, 8 |
| Mac Apply requires previewed selection | 7, 8 |
| Standalone regen | 6 |
| Safety tests still run | 4, 6, 8 |

**Not in this plan (queued slices):** configurable closing, launch restore, teach-this-message, session coach.

**Placeholder scan:** none remaining. Task 8 UI is specified with types, method names, and copy.

**Type consistency:** `load_apply_ids` / `apply_selected` / `plan_from_stored` / `queue_preview_payload` / `ApplySelection` / `applyIdsFile` names match across tasks.
