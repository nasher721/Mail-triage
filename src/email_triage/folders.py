"""AI Triage folder taxonomy and mailbox folder creation.

Folders are created under ``AI Triage/`` only. This module never sends, forwards,
deletes, or downloads mail.
"""

from __future__ import annotations

from typing import Protocol

from email_triage.models import (
    Confidence,
    ManualReviewReason,
    Route,
    ScreeningResult,
    Topic,
    Urgency,
)

FOLDER_NAMES = {
    Route.NEEDS_REVIEW: "AI Triage/Needs Review",
    Route.NEEDS_REPLY: "AI Triage/Needs Reply",
    Route.NO_REPLY: "AI Triage/No Reply Needed",
}

NEWSLETTER_FOLDER = "AI Triage/Newsletters"
READING_FOLDER = "AI Triage/Read Later"
ADMIN_FOLDER = "AI Triage/Administrative"

TOPIC_FOLDER_NAMES = {
    Topic.CLINICAL: "Clinical",
    Topic.SCHEDULING: "Scheduling",
    Topic.ADMINISTRATIVE: "Administrative",
    Topic.EDUCATION_RESEARCH: "Education Research",
    Topic.OTHER: "Other",
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


class FolderMailbox(Protocol):
    def ensure_folder_path(self, folder_path: str) -> str: ...


def topic_folder(route_folder: str, topic: Topic) -> str:
    """Return a route/topic path, keeping Other at the route root."""

    if topic == Topic.OTHER:
        return route_folder
    return f"{route_folder}/{TOPIC_FOLDER_NAMES[topic]}"


def organization_folders() -> tuple[str, ...]:
    """Stable mailbox tree created before filing, including legacy top-level names."""

    roots = (
        FOLDER_NAMES[Route.NEEDS_REVIEW],
        FOLDER_NAMES[Route.NEEDS_REPLY],
        FOLDER_NAMES[Route.NO_REPLY],
        NEWSLETTER_FOLDER,
        READING_FOLDER,
        ADMIN_FOLDER,
    )
    children: list[str] = []
    for route_folder in (
        FOLDER_NAMES[Route.NEEDS_REVIEW],
        FOLDER_NAMES[Route.NEEDS_REPLY],
        FOLDER_NAMES[Route.NO_REPLY],
    ):
        for topic in Topic:
            child = topic_folder(route_folder, topic)
            if child != route_folder:
                children.append(child)
    return roots + tuple(children)


def cleanup_folder(result: ScreeningResult, unsubscribe_suggestion: bool) -> str:
    """Choose a reviewable subfolder from route, topic, and newsletter hints."""

    if result.manual_review_reason == ManualReviewReason.CLINICAL_OR_PATIENT:
        return topic_folder(FOLDER_NAMES[Route.NEEDS_REVIEW], Topic.CLINICAL)
    if result.manual_review_reason is not None or result.confidence == Confidence.LOW:
        return FOLDER_NAMES[Route.NEEDS_REVIEW]
    if result.route == Route.NEEDS_REPLY:
        return topic_folder(FOLDER_NAMES[Route.NEEDS_REPLY], result.topic)
    if result.route == Route.NEEDS_REVIEW:
        return topic_folder(FOLDER_NAMES[Route.NEEDS_REVIEW], result.topic)
    if unsubscribe_suggestion:
        return NEWSLETTER_FOLDER
    if result.topic == Topic.EDUCATION_RESEARCH:
        return READING_FOLDER
    if result.topic == Topic.ADMINISTRATIVE:
        return ADMIN_FOLDER
    return FOLDER_NAMES[Route.NO_REPLY]


def ensure_organization_folders(mailbox: FolderMailbox) -> tuple[str, ...]:
    """Create the full AI Triage tree. Existing folders are left in place."""

    created: list[str] = []
    for folder in organization_folders():
        mailbox.ensure_folder_path(folder)
        created.append(folder)
    return tuple(created)
