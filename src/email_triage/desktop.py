"""Read one user-selected, frontmost Outlook message through macOS Accessibility.

This optional adapter cannot enumerate a mailbox, read attachments, or mutate mail.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from email_triage.config import ConfigurationError
from email_triage.models import GraphMessage


_VISIBLE_MESSAGE_SCRIPT = r'''
tell application "System Events"
    if not (exists process "Microsoft Outlook") then error "outlook_not_running"
    tell process "Microsoft Outlook"
        if not (exists front window) then error "outlook_has_no_window"
        return name of front window
    end tell
end tell
'''

_FRONT_WINDOW_PROBE_SCRIPT = r'''
tell application "System Events"
    if not (exists process "Microsoft Outlook") then error "outlook_not_running"
    tell process "Microsoft Outlook"
        if not (exists front window) then error "outlook_has_no_window"
    end tell
end tell
return "ready"
'''


@dataclass(frozen=True)
class DesktopDiagnostic:
    available: bool
    code: str
    detail: str

    def to_dict(self) -> dict[str, str | bool]:
        return {"available": self.available, "code": self.code, "detail": self.detail}


Runner = Callable[..., subprocess.CompletedProcess[str]]


def desktop_diagnostic(runner: Runner = subprocess.run) -> DesktopDiagnostic:
    if platform.system() != "Darwin":
        return DesktopDiagnostic(False, "unsupported_platform", "Requires macOS.")
    if shutil.which("osascript") is None:
        return DesktopDiagnostic(False, "osascript_missing", "macOS osascript was not found.")
    probe = runner(
        ["osascript", "-e", 'tell application "System Events" to exists process "Microsoft Outlook"'],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if probe.returncode != 0:
        return DesktopDiagnostic(
            False,
            "accessibility_denied",
            "Allow the invoking app under System Settings > Privacy & Security > Accessibility.",
        )
    if probe.stdout.strip().lower() != "true":
        return DesktopDiagnostic(False, "outlook_not_running", "Open Microsoft Outlook first.")
    return DesktopDiagnostic(
        True,
        "ready",
        "Reads only visible text from Outlook's single frontmost message window.",
    )


class OutlookDesktopMailbox:
    """Read-only adapter for the one message explicitly opened by the user."""

    def __init__(self, runner: Runner = subprocess.run):
        self._runner = runner

    def unread_messages(
        self, limit: int, exclude_ids: set[str] | None = None
    ) -> list[GraphMessage]:
        if limit < 1:
            return []
        diagnostic = desktop_diagnostic(self._runner)
        if not diagnostic.available:
            raise ConfigurationError(f"Outlook desktop adapter: {diagnostic.detail}")
        result = self._runner(
            ["osascript", "-e", _VISIBLE_MESSAGE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            raise ConfigurationError(
                "Could not read Outlook's front window through Accessibility. Open one "
                "message in its own frontmost window and verify Accessibility permission."
            )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            raise ConfigurationError(
                "Outlook's front window did not expose a message title. Open one "
                "message in its own window and retry."
            )
        subject, body = lines[0], ""
        message_id = "outlook-desktop-" + hashlib.sha256(
            result.stdout.encode("utf-8")
        ).hexdigest()[:24]
        if exclude_ids and message_id in exclude_ids:
            return []
        return [
            GraphMessage(
                id=message_id,
                internet_message_id=None,
                subject=subject,
                sender_name="",
                sender_address="",
                received_at=None,
                body=body,
                sensitivity="normal",
                has_attachments=False,
                odata_type="",
            )
        ]

    def probe_access(self) -> None:
        """Validate front-window Accessibility without reading visible message text."""

        diagnostic = desktop_diagnostic(self._runner)
        if not diagnostic.available:
            raise ConfigurationError(f"Outlook desktop adapter: {diagnostic.detail}")
        result = self._runner(
            ["osascript", "-e", _FRONT_WINDOW_PROBE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0 or result.stdout.strip().lower() != "ready":
            raise ConfigurationError(
                "Outlook desktop adapter could not verify a frontmost window."
            )
