"""Local operator preferences that improve future triage runs safely."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from email_triage.models import Route, ScreeningResult


@dataclass(frozen=True)
class FeedbackPreference:
    sender_domain: str
    route: Route | None = None
    reply_guidance: str = ""


class FeedbackPreferences:
    """Read narrow, local-only preferences saved by the macOS app."""

    def __init__(self, preferences: tuple[FeedbackPreference, ...] = ()) -> None:
        self._by_domain = {item.sender_domain: item for item in preferences}

    @classmethod
    def from_path(cls, path: Path | None) -> "FeedbackPreferences":
        if path is None or not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = raw.get("preferences", [])
            if not isinstance(entries, list):
                return cls()
            preferences: list[FeedbackPreference] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                domain = str(entry.get("sender_domain", "")).lower().strip()
                route_value = entry.get("route")
                guidance = str(entry.get("reply_guidance", "")).strip()[:400]
                if not domain or "." not in domain:
                    continue
                route = Route(route_value) if route_value in {Route.NO_REPLY, Route.NEEDS_REVIEW} else None
                if route is not None or guidance:
                    preferences.append(FeedbackPreference(domain, route, guidance))
            return cls(tuple(preferences))
        except (OSError, json.JSONDecodeError, ValueError):
            return cls()

    def for_sender(self, sender_address: str) -> FeedbackPreference | None:
        _, separator, domain = sender_address.lower().rpartition("@")
        return self._by_domain.get(domain) if separator else None

    @staticmethod
    def apply(result: ScreeningResult, preference: FeedbackPreference | None) -> ScreeningResult:
        """Apply only safe route preferences after the model's safety result."""

        if preference is None or preference.route is None:
            return result
        if result.manual_review_reason is not None or result.confidence.value == "low":
            return result
        if preference.route == Route.NO_REPLY:
            return replace(result, route=Route.NO_REPLY, response_required=False, suggested_reply=None)
        return replace(result, route=Route.NEEDS_REVIEW, response_required=False, suggested_reply=None)
