from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

from email_triage.models import ManualReviewReason


_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

_CLINICAL_PATTERNS = (
    re.compile(r"\bpatient\b", re.IGNORECASE),
    re.compile(r"\bmedical record(?: number)?\b", re.IGNORECASE),
    re.compile(r"\bMRN\b", re.IGNORECASE),
    re.compile(r"\bdiagnos(?:is|ed|tic)\b", re.IGNORECASE),
    re.compile(r"\bmedication(?:s)?\b", re.IGNORECASE),
    re.compile(r"\btreatment plan\b", re.IGNORECASE),
    re.compile(r"\bdate of birth\b", re.IGNORECASE),
)

_INJECTION_PATTERNS = (
    re.compile(r"ignore (?:all |any )?(?:previous|prior|system) instructions", re.IGNORECASE),
    re.compile(r"reveal (?:the )?(?:system|developer) prompt", re.IGNORECASE),
    re.compile(r"change (?:the )?(?:output )?schema", re.IGNORECASE),
    re.compile(r"you are now", re.IGNORECASE),
)


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "head"}:
            self.hidden_depth += 1
        elif tag.lower() in {"br", "p", "div", "li", "tr"} and not self.hidden_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "head"} and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag.lower() in {"p", "div", "li", "tr"} and not self.hidden_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def body_to_text(body: str, max_characters: int) -> str:
    """Normalize an Outlook body without retrieving or interpreting attachments."""

    parser = _VisibleTextParser()
    parser.feed(body or "")
    parser.close()
    text = unescape("".join(parser.parts))
    text = _WHITESPACE_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()[:max_characters]


def local_manual_review_reason(body: str) -> ManualReviewReason | None:
    """Conservative preflight that keeps obvious high-risk bodies out of the AI call."""

    if any(pattern.search(body) for pattern in _CLINICAL_PATTERNS):
        return ManualReviewReason.CLINICAL_OR_PATIENT
    if any(pattern.search(body) for pattern in _INJECTION_PATTERNS):
        return ManualReviewReason.SUSPECTED_PROMPT_INJECTION
    return None
