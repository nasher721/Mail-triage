from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from email_triage.classifier import ClassificationError
from email_triage.feedback import FeedbackPreferences
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

NEWSLETTER_FOLDER = "AI Triage/Newsletters"
READING_FOLDER = "AI Triage/Read Later"
ADMIN_FOLDER = "AI Triage/Administrative"
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
    def classify(
        self, body: str, has_attachments: bool, reply_guidance: str = ""
    ) -> ScreeningResult: ...


def suggest_unsubscribe(message: GraphMessage, body: str, result: ScreeningResult) -> bool:
    """Offer a manual newsletter hint without following links or changing subscriptions."""

    if result.manual_review_reason is not None or result.route != Route.NO_REPLY:
        return False
    text = f"{message.subject}\n{body}".lower()
    has_opt_out = any(token in text for token in ("unsubscribe", "manage preferences", "email preferences"))
    has_newsletter_signal = message.is_no_reply_sender or any(
        token in text for token in ("newsletter", "weekly digest", "promotions", "subscribe")
    )
    return has_opt_out and has_newsletter_signal


def cleanup_folder(result: ScreeningResult, unsubscribe_suggestion: bool) -> str:
    """Choose a reviewable subfolder for messages that need no reply."""

    if result.route != Route.NO_REPLY:
        return FOLDER_NAMES[result.route]
    if unsubscribe_suggestion:
        return NEWSLETTER_FOLDER
    if result.topic == Topic.EDUCATION_RESEARCH:
        return READING_FOLDER
    if result.topic == Topic.ADMINISTRATIVE:
        return ADMIN_FOLDER
    return FOLDER_NAMES[Route.NO_REPLY]


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
    preferences: FeedbackPreferences | None = None,
) -> ReviewRecord | None:
    if message.is_calendar_message:
        return None

    body = body_to_text(message.body, max_body_characters)
    preference = preferences.for_sender(message.sender_address) if preferences else None
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
            if preference and preference.reply_guidance:
                result = classifier.classify(body, message.has_attachments, preference.reply_guidance)
            else:
                result = classifier.classify(body, message.has_attachments)
            result = enforce_route(result, message.is_no_reply_sender)
            result = FeedbackPreferences.apply(result, preference)
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

    unsubscribe_suggestion = suggest_unsubscribe(message, body, result)
    preferred_folder = FeedbackPreferences.destination_for(result, preference)
    return ReviewRecord(
        message_id=message.id,
        internet_message_id=message.internet_message_id,
        subject=message.subject,
        sender_name=message.sender_name,
        sender_address=message.sender_address,
        received_at=message.received_at,
        sensitivity=message.sensitivity,
        has_attachments=message.has_attachments,
        target_folder=preferred_folder or cleanup_folder(result, unsubscribe_suggestion),
        categories=tuple(categories),
        analysis=result,
        unsubscribe_suggestion=unsubscribe_suggestion,
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
