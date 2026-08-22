from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterator

DEFAULT_REPLY_CLOSING = "Best,\nNick"
_REPLY_CLOSING: ContextVar[str] = ContextVar("reply_closing", default=DEFAULT_REPLY_CLOSING)


def normalize_reply_closing(value: str | None) -> str:
    """Return a safe operator-authored sign-off, or the default when empty."""

    text = (value or "").strip()
    if not text:
        return DEFAULT_REPLY_CLOSING
    if len(text) > 120:
        raise ValueError("reply closing must be at most 120 characters")
    if text.count("\n") > 3 or len(text.splitlines()) > 4:
        raise ValueError("reply closing must be at most four lines")
    if any(ord(character) < 32 and character != "\n" for character in text):
        raise ValueError("reply closing may only use newline as a control character")
    if "<" in text or ">" in text:
        raise ValueError("reply closing cannot contain HTML")
    return text


def current_reply_closing() -> str:
    return _REPLY_CLOSING.get()


@contextmanager
def use_reply_closing(value: str | None) -> Iterator[str]:
    closing = normalize_reply_closing(value)
    token = _REPLY_CLOSING.set(closing)
    try:
        yield closing
    finally:
        _REPLY_CLOSING.reset(token)


class Route(StrEnum):
    NEEDS_REVIEW = "needs_review"
    NEEDS_REPLY = "needs_reply"
    NO_REPLY = "no_reply"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Urgency(StrEnum):
    URGENT = "urgent"
    SOON = "soon"
    ROUTINE = "routine"


class Topic(StrEnum):
    CLINICAL = "clinical"
    SCHEDULING = "scheduling"
    ADMINISTRATIVE = "administrative"
    EDUCATION_RESEARCH = "education_research"
    OTHER = "other"


class ManualReviewReason(StrEnum):
    CLINICAL_OR_PATIENT = "clinical_or_patient"
    SUSPECTED_PROMPT_INJECTION = "suspected_prompt_injection"
    LOW_CONFIDENCE = "low_confidence"


SCREENING_KEYS = {
    "summary",
    "priority_score",
    "action_items",
    "route",
    "response_required",
    "confidence",
    "urgency",
    "deadline",
    "topic",
    "manual_review_reason",
    "rationale",
    "suggested_reply",
}


@dataclass(frozen=True)
class ScreeningResult:
    summary: str
    priority_score: int
    action_items: tuple[str, ...]
    route: Route
    response_required: bool
    confidence: Confidence
    urgency: Urgency
    deadline: str | None
    topic: Topic
    manual_review_reason: ManualReviewReason | None
    rationale: str
    suggested_reply: str | None

    def __post_init__(self) -> None:
        if not self.summary or len(self.summary) > 600:
            raise ValueError("summary must contain 1-600 characters")
        if isinstance(self.priority_score, bool) or not 1 <= self.priority_score <= 5:
            raise ValueError("priority_score must be an integer from 1 to 5")
        if len(self.action_items) > 5 or len(self.action_items) != len(set(self.action_items)):
            raise ValueError("action_items must contain at most five unique strings")
        if any(not isinstance(item, str) or not item.strip() for item in self.action_items):
            raise ValueError("action_items must be non-empty strings")
        if not self.rationale or len(self.rationale) > 240:
            raise ValueError("rationale must contain 1-240 characters")
        review_required = self.manual_review_reason is not None or self.confidence == Confidence.LOW
        if review_required and self.route != Route.NEEDS_REVIEW:
            raise ValueError("manual-review and low-confidence results must route to needs_review")
        if (
            self.confidence == Confidence.LOW
            and self.manual_review_reason != ManualReviewReason.LOW_CONFIDENCE
        ):
            raise ValueError("low confidence must use manual_review_reason=low_confidence")
        if (
            self.manual_review_reason == ManualReviewReason.LOW_CONFIDENCE
            and self.confidence != Confidence.LOW
        ):
            raise ValueError("low_confidence reason requires low confidence")
        if self.topic == Topic.CLINICAL and (
            self.route != Route.NEEDS_REVIEW
            or self.manual_review_reason != ManualReviewReason.CLINICAL_OR_PATIENT
        ):
            raise ValueError("clinical results must route to needs_review with clinical_or_patient")
        if self.route == Route.NEEDS_REPLY:
            if not self.response_required or self.confidence == Confidence.LOW:
                raise ValueError("needs_reply requires a response and non-low confidence")
            closing = current_reply_closing()
            if not self.suggested_reply or not self.suggested_reply.rstrip().endswith(closing):
                raise ValueError(f"suggested replies must end with {closing!r}")
        elif self.suggested_reply is not None:
            raise ValueError("only needs_reply results may contain a suggested reply")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ScreeningResult":
        if set(raw) != SCREENING_KEYS:
            missing = sorted(SCREENING_KEYS - set(raw))
            extra = sorted(set(raw) - SCREENING_KEYS)
            raise ValueError(f"invalid result keys; missing={missing}, extra={extra}")
        if not isinstance(raw["response_required"], bool):
            raise ValueError("response_required must be Boolean")
        if not isinstance(raw["priority_score"], int):
            raise ValueError("priority_score must be an integer")
        if not isinstance(raw["action_items"], list):
            raise ValueError("action_items must be a list")
        for name in ("summary", "route", "confidence", "urgency", "topic", "rationale"):
            if not isinstance(raw[name], str):
                raise ValueError(f"{name} must be a string")
        if raw["manual_review_reason"] is not None and not isinstance(
            raw["manual_review_reason"], str
        ):
            raise ValueError("manual_review_reason must be a string or null")
        if raw["deadline"] is not None and not isinstance(raw["deadline"], str):
            raise ValueError("deadline must be a string or null")
        if raw["suggested_reply"] is not None and not isinstance(raw["suggested_reply"], str):
            raise ValueError("suggested_reply must be a string or null")
        manual_reason = raw["manual_review_reason"]
        return cls(
            summary=raw["summary"],
            priority_score=raw["priority_score"],
            action_items=tuple(raw["action_items"]),
            route=Route(raw["route"]),
            response_required=raw["response_required"],
            confidence=Confidence(raw["confidence"]),
            urgency=Urgency(raw["urgency"]),
            deadline=raw["deadline"],
            topic=Topic(raw["topic"]),
            manual_review_reason=(
                ManualReviewReason(manual_reason) if manual_reason is not None else None
            ),
            rationale=raw["rationale"],
            suggested_reply=raw["suggested_reply"],
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True)
class GraphMessage:
    id: str
    internet_message_id: str | None = None
    subject: str = ""
    sender_name: str = ""
    sender_address: str = ""
    received_at: datetime | None = None
    body: str = ""
    sensitivity: str = "normal"
    has_attachments: bool = False
    odata_type: str = ""

    @property
    def is_calendar_message(self) -> bool:
        return "eventmessage" in self.odata_type.lower()

    @property
    def is_no_reply_sender(self) -> bool:
        address = self.sender_address.lower()
        return any(token in address for token in ("no-reply", "noreply", "donotreply"))


@dataclass(frozen=True)
class ReviewRecord:
    message_id: str
    internet_message_id: str | None
    subject: str
    sender_name: str
    sender_address: str
    received_at: datetime | None
    sensitivity: str
    has_attachments: bool
    target_folder: str
    categories: tuple[str, ...]
    analysis: ScreeningResult
    unsubscribe_suggestion: bool = False
    processing_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

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
        message_id = str(raw.get("message_id") or "")
        if not message_id:
            raise ValueError("message_id is required")
        return cls(
            message_id=message_id,
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


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (StrEnum, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    return value
