from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from email_triage.classifier import ClassificationError
from email_triage.models import (
    Confidence,
    GraphMessage,
    ManualReviewReason,
    ReviewRecord,
    Route,
    ScreeningResult,
    Topic,
    Urgency,
)
from email_triage.safety import body_to_text, local_manual_review_reason


FOLDER_NAMES = {
    Route.NEEDS_REVIEW: "AI Triage/Needs Review",
    Route.NEEDS_REPLY: "AI Triage/Needs Reply",
    Route.NO_REPLY: "AI Triage/No Reply Needed",
}
URGENCY_CATEGORIES = {
    Urgency.URGENT: "AI - Urgent",
    Urgency.SOON: "AI - Soon",
    Urgency.ROUTINE: "AI - Routine",
}
TOPIC_CATEGORIES = {
    Topic.CLINICAL: "AI - Clinical",
    Topic.SCHEDULING: "AI - Scheduling",
    Topic.ADMINISTRATIVE: "AI - Administrative",
    Topic.EDUCATION_RESEARCH: "AI - Education/Research",
    Topic.OTHER: "AI - Other",
}


class Classifier(Protocol):
    def classify(self, body: str, has_attachments: bool) -> ScreeningResult: ...


def _manual_review(reason: ManualReviewReason, processing_error: bool = False) -> ScreeningResult:
    label = {
        ManualReviewReason.CLINICAL_OR_PATIENT: (
            "Clinical or patient-related content requires manual review."
        ),
        ManualReviewReason.SUSPECTED_PROMPT_INJECTION: (
            "Possible prompt injection requires manual review."
        ),
        ManualReviewReason.LOW_CONFIDENCE: (
            "Automated screening was unavailable; manual review is required."
        ),
    }[reason]
    return ScreeningResult(
        summary=label,
        priority_score=3 if not processing_error else 2,
        action_items=("Review the original message manually.",),
        route=Route.NEEDS_REVIEW,
        response_required=False,
        confidence=(
            Confidence.LOW
            if reason == ManualReviewReason.LOW_CONFIDENCE
            else Confidence.HIGH
        ),
        urgency=Urgency.ROUTINE,
        deadline=None,
        topic=Topic.CLINICAL if reason == ManualReviewReason.CLINICAL_OR_PATIENT else Topic.OTHER,
        manual_review_reason=reason,
        rationale=label,
        suggested_reply=None,
    )


def _no_reply_sender_result() -> ScreeningResult:
    """Deterministic metadata-only result for automated sender addresses."""

    return ScreeningResult(
        summary="Automated sender address does not accept replies.",
        priority_score=1,
        action_items=(),
        route=Route.NO_REPLY,
        response_required=False,
        confidence=Confidence.HIGH,
        urgency=Urgency.ROUTINE,
        deadline=None,
        topic=Topic.OTHER,
        manual_review_reason=None,
        rationale="No-reply sender; file without body analysis.",
        suggested_reply=None,
    )


def enforce_route(result: ScreeningResult, no_reply_sender: bool) -> ScreeningResult:
    """Apply deterministic routing after schema validation."""

    route = result.route
    response_required = result.response_required
    suggested_reply = result.suggested_reply
    if result.manual_review_reason is not None or result.confidence == Confidence.LOW:
        route = Route.NEEDS_REVIEW
        suggested_reply = None
    elif no_reply_sender:
        route = Route.NO_REPLY
        response_required = False
        suggested_reply = None
    elif result.response_required:
        route = Route.NEEDS_REPLY
    else:
        route = Route.NO_REPLY
        suggested_reply = None
    return replace(
        result,
        route=route,
        response_required=response_required,
        suggested_reply=suggested_reply,
    )


def process_message(
    message: GraphMessage,
    classifier: Classifier,
    max_body_characters: int,
    intercept_clinical: bool = True,
) -> ReviewRecord | None:
    if message.is_calendar_message:
        return None

    body = body_to_text(message.body, max_body_characters)
    local_reason = local_manual_review_reason(body)
    if (
        local_reason == ManualReviewReason.CLINICAL_OR_PATIENT
        and not intercept_clinical
    ):
        local_reason = None
    processing_error: str | None = None
    if local_reason is not None:
        result = _manual_review(local_reason)
    elif message.is_no_reply_sender:
        result = _no_reply_sender_result()
    else:
        try:
            result = classifier.classify(body, message.has_attachments)
            result = enforce_route(result, message.is_no_reply_sender)
        except (ClassificationError, ValueError):
            processing_error = "ai_processing_error"
            result = _manual_review(ManualReviewReason.LOW_CONFIDENCE, processing_error=True)

    categories = [
        URGENCY_CATEGORIES[result.urgency],
        TOPIC_CATEGORIES[result.topic],
    ]
    if processing_error:
        categories.append("AI - Processing Error")
    else:
        categories.append("AI - Processed")

    return ReviewRecord(
        message_id=message.id,
        internet_message_id=message.internet_message_id,
        subject=message.subject,
        sender_name=message.sender_name,
        sender_address=message.sender_address,
        received_at=message.received_at,
        sensitivity=message.sensitivity,
        has_attachments=message.has_attachments,
        target_folder=FOLDER_NAMES[result.route],
        categories=tuple(categories),
        analysis=result,
        processing_error=processing_error,
    )


class LocalQueue:
    """Private local JSONL queue and idempotency state; message bodies are never stored."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.queue_path = output_dir / "review_queue.jsonl"
        self.state_path = output_dir / "processed_message_ids.json"
        self._seen = self._load_seen()

    def _prepare(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.output_dir, 0o700)

    def _load_seen(self) -> set[str]:
        if not self.state_path.exists():
            return set()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return set()
        return {str(value) for value in data if isinstance(value, str)}

    def contains(self, message_id: str) -> bool:
        return message_id in self._seen

    @property
    def seen_ids(self) -> set[str]:
        """Copy IDs for metadata-stage exclusion during incremental scans."""

        return set(self._seen)

    def append(self, record: ReviewRecord) -> None:
        self._prepare()
        with self.queue_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        os.chmod(self.queue_path, 0o600)
        self._seen.add(record.message_id)
        self.state_path.write_text(
            json.dumps(sorted(self._seen), indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(self.state_path, 0o600)
