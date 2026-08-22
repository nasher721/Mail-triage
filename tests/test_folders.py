import unittest

from email_triage.folders import (
    FOLDER_NAMES,
    NEWSLETTER_FOLDER,
    cleanup_folder,
    ensure_organization_folders,
    organization_folders,
    topic_folder,
)
from email_triage.models import (
    Confidence,
    ManualReviewReason,
    Route,
    ScreeningResult,
    Topic,
    Urgency,
)


class FakeFolderMailbox:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def ensure_folder_path(self, folder_path: str) -> str:
        self.paths.append(folder_path)
        return folder_path


def _result(**overrides: object) -> ScreeningResult:
    values = {
        "summary": "A colleague asks to schedule a meeting.",
        "priority_score": 3,
        "action_items": ("Confirm availability.",),
        "route": Route.NEEDS_REPLY,
        "response_required": True,
        "confidence": Confidence.HIGH,
        "urgency": Urgency.SOON,
        "deadline": "next Tuesday",
        "topic": Topic.SCHEDULING,
        "manual_review_reason": None,
        "rationale": "Direct scheduling request.",
        "suggested_reply": "Tuesday afternoon works for me.\n\nBest,\nNick",
    }
    values.update(overrides)
    return ScreeningResult(**values)


class FolderTaxonomyTests(unittest.TestCase):
    def test_organization_tree_covers_routes_and_topics(self) -> None:
        folders = organization_folders()
        self.assertIn("AI Triage/Needs Reply", folders)
        self.assertIn("AI Triage/Needs Reply/Scheduling", folders)
        self.assertIn("AI Triage/Needs Review/Clinical", folders)
        self.assertIn(NEWSLETTER_FOLDER, folders)
        self.assertNotIn("AI Triage/Needs Reply/Other", folders)
        self.assertTrue(all(item.startswith("AI Triage/") for item in folders))

    def test_cleanup_files_replies_by_topic(self) -> None:
        self.assertEqual(
            cleanup_folder(_result(), False),
            "AI Triage/Needs Reply/Scheduling",
        )
        self.assertEqual(
            cleanup_folder(_result(topic=Topic.OTHER), False),
            FOLDER_NAMES[Route.NEEDS_REPLY],
        )

    def test_clinical_review_uses_clinical_child(self) -> None:
        result = _result(
            route=Route.NEEDS_REVIEW,
            response_required=False,
            topic=Topic.CLINICAL,
            manual_review_reason=ManualReviewReason.CLINICAL_OR_PATIENT,
            suggested_reply=None,
        )
        self.assertEqual(cleanup_folder(result, False), "AI Triage/Needs Review/Clinical")

    def test_ensure_creates_every_organization_folder(self) -> None:
        mailbox = FakeFolderMailbox()
        created = ensure_organization_folders(mailbox)
        self.assertEqual(mailbox.paths, list(organization_folders()))
        self.assertEqual(created, organization_folders())

    def test_topic_folder_keeps_other_at_route_root(self) -> None:
        self.assertEqual(topic_folder("AI Triage/Needs Reply", Topic.OTHER), "AI Triage/Needs Reply")
