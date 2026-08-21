"""Read the currently visible, unread rows from an already-open Outlook window.

This adapter is deliberately narrow.  Its visible-row parsing
approach is adapted from the MIT-licensed reader in
https://github.com/Arkya-AI/outlook-email-scanner (MIT License).  It does not
activate Outlook, select rows, navigate, scroll, open attachments, export data, or mutate
mail.  ``atomacos`` is imported only when this backend is used, so the package
continues to work on non-macOS systems without optional dependencies.
"""

from __future__ import annotations

import hashlib
import importlib
import platform
import re
import time
from datetime import datetime
from typing import Any, Callable

from email_triage.config import ConfigurationError
from email_triage.desktop import DesktopDiagnostic
from email_triage.models import GraphMessage


def _get(value: Any, *names: str) -> Any:
    """Read an AX attribute from either a fake or an atomacos element."""
    for name in names:
        try:
            if hasattr(value, "getAttribute"):
                result = value.getAttribute(name)
            elif isinstance(value, dict):
                result = value.get(name)
            else:
                result = getattr(value, name, None)
        except Exception:
            result = None
        if result not in (None, "", []):
            return result
    return None


def _children(value: Any) -> list[Any]:
    result = _get(value, "AXChildren", "children")
    if result is None:
        try:
            result = value.children()
        except Exception:
            result = []
    return list(result or [])


def _walk(value: Any, *, seen: set[int] | None = None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    yield value
    for child in _children(value):
        yield from _walk(child, seen=seen)


def _text(value: Any) -> str:
    raw = _get(value, "AXValue", "AXTitle", "AXDescription", "value", "title")
    if isinstance(raw, (str, int, float)):
        return str(raw).strip()
    return ""


def _role(value: Any) -> str:
    return str(_get(value, "AXRole", "role") or "").lower()


def _explicitly_unread(row: Any) -> bool:
    """Require a real AX unread marker; never infer unread from row styling."""
    for element in _walk(row):
        role = _role(element)
        for attribute in ("AXDescription", "AXTitle", "AXValue", "AXIdentifier"):
            marker = str(_get(element, attribute) or "").strip().lower()
            if not marker:
                continue
            if marker in {"unread", "read: false", "is unread", "unread message"}:
                return True
            if marker in {"•", "●", "⬤"}:
                return True
            if "unread" in marker and role in {"aximage", "axcheckbox", "axstatictext", "axbutton", ""}:
                return True
    return False


def _rows(table: Any) -> list[Any]:
    rows = _get(table, "AXVisibleRows")
    if rows is None:
        rows = _get(table, "AXRows")
    if rows is None:
        rows = [child for child in _children(table) if _role(child) in {"axrow", "row"}]
    return list(rows or [])


def _find_table(window: Any) -> Any | None:
    for element in _walk(window):
        if _role(element) in {"axtable", "table"} and _rows(element):
            return element
    return None


def _find_inbox_window(app: Any) -> Any | None:
    try:
        windows = list(app.windows())
    except Exception as exc:
        raise ConfigurationError(
            "Could not inspect Outlook windows through Accessibility; ensure Outlook "
            "is already open and Accessibility permission is granted."
        ) from exc
    for window in windows:
        title = str(_get(window, "AXTitle", "AXDescription", "title") or "").lower()
        if "inbox" in title and _find_table(window) is not None:
            return window
    return None


def _reading_pane(window: Any, table: Any) -> Any:
    # Prefer an explicitly named pane.  Otherwise use the window: AX values
    # are restricted to the already-visible tree and never trigger navigation.
    for element in _walk(window):
        label = str(_get(element, "AXTitle", "AXDescription", "AXIdentifier") or "").lower()
        if "reading" in label or "message body" in label:
            return element
    return window


def _outlook_content_group(window: Any) -> Any:
    """Follow Outlook 16.x split groups, with the window as safe fallback."""

    current = window
    for position in (0, -1, -1):
        split_groups = [item for item in _children(current) if _role(item) == "axsplitgroup"]
        if not split_groups:
            return window
        try:
            current = split_groups[position]
        except IndexError:
            return window
    return current


def _first_role(root: Any, role: str) -> Any | None:
    expected = role.lower()
    return next((item for item in _walk(root) if _role(item) == expected), None)


def _header_and_body(window: Any) -> tuple[Any, Any]:
    content = _outlook_content_group(window)
    children = _children(content)
    header = children[2] if len(children) > 2 else _reading_pane(window, content)
    body_group = children[4] if len(children) > 4 else content
    body = _first_role(body_group, "axwebarea") or body_group
    return header, body


def _sender_parts(raw: str) -> tuple[str, str]:
    match = re.search(r"<([^<>\s]+@[^<>\s]+)>", raw)
    if match:
        return raw[: match.start()].strip().strip('"'), match.group(1)
    email = re.search(r"[\w.+-]+@[\w.-]+", raw)
    return (raw, email.group(0)) if email else (raw, "")


def accessibility_diagnostic(atomacos_module: Any | None = None) -> DesktopDiagnostic:
    """Check platform, optional bridge, and Inbox tree without reading mail text."""

    if platform.system() != "Darwin":
        return DesktopDiagnostic(
            False, "unsupported_platform", "Accessibility scanning requires macOS."
        )
    try:
        module = atomacos_module or importlib.import_module("atomacos")
    except (ImportError, OSError):
        return DesktopDiagnostic(
            False,
            "dependency_missing",
            "The experimental Accessibility source needs a separate atomacos install.",
        )
    try:
        app = module.getAppRefByBundleId("com.microsoft.Outlook")
        available = _find_inbox_window(app) is not None
    except ConfigurationError:
        available = False
    except Exception:
        available = False
    return DesktopDiagnostic(
        available,
        "ready" if available else "outlook_inbox_unavailable",
        (
            "An already-open Outlook Inbox table is available; no row text was read."
            if available
            else "Open Outlook to its Inbox and grant Accessibility permission."
        ),
    )


def _read_field(pane: Any, names: tuple[str, ...]) -> str:
    for element in _walk(pane):
        value = _get(element, *names)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return ""


def _visible_text(pane: Any) -> list[str]:
    values: list[str] = []
    for element in _walk(pane):
        text = _text(element)
        if text and text not in values:
            values.append(text)
    return values


class OutlookAccessibilityMailbox:
    """Read-only mailbox view backed by the existing Outlook AX hierarchy."""

    def __init__(
        self,
        atomacos_module: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._atomacos = atomacos_module
        self._sleep = sleep

    def _module(self) -> Any:
        if self._atomacos is not None:
            return self._atomacos
        try:
            self._atomacos = importlib.import_module("atomacos")
        except (ImportError, OSError) as exc:
            raise ConfigurationError(
                "Outlook Accessibility requires optional 'atomacos' on macOS; "
                "install it, open Outlook to its Inbox, and grant Accessibility permission."
            ) from exc
        return self._atomacos

    def _app(self) -> Any:
        module = self._module()
        try:
            return module.getAppRefByBundleId("com.microsoft.Outlook")
        except Exception as exc:
            raise ConfigurationError(
                "Could not connect to the already-open Microsoft Outlook app through "
                "Accessibility; open Outlook and grant Accessibility permission."
            ) from exc

    def unread_messages(self, limit: int, exclude_ids: set[str] | None = None) -> list[GraphMessage]:
        if limit < 1:
            return []
        exclude_ids = exclude_ids or set()
        window = _find_inbox_window(self._app())
        if window is None:
            raise ConfigurationError(
                "No already-open Outlook Inbox window with a visible message table was found; "
                "open Inbox without scrolling and retry."
            )
        table = _find_table(window)
        assert table is not None
        messages: list[GraphMessage] = []
        for row in _rows(table):
            if len(messages) >= limit or not _explicitly_unread(row):
                continue
            # Read only the already-exposed row subtree. Selecting a row can
            # trigger Outlook's "mark as read on selection" preference, so this
            # fallback intentionally limits content to visible row metadata.
            header = row
            body_element = row
            header_values = _visible_text(row)
            subject = (
                _read_field(header, ("AXSubject", "subject"))
                or (header_values[0] if header_values else _text(row))
            )
            sender_raw = _read_field(header, ("AXSender", "sender")) or (
                header_values[1] if len(header_values) > 1 else ""
            )
            sender_name = _read_field(header, ("AXSenderName", "sender_name"))
            sender_address = _read_field(
                header, ("AXSenderAddress", "sender_address")
            )
            parsed_name, parsed_address = _sender_parts(sender_raw)
            sender_name = sender_name or parsed_name
            sender_address = sender_address or parsed_address
            body = _read_field(body_element, ("AXBody", "body"))
            if not body:
                body = "\n".join(
                    item for item in _visible_text(body_element) if item and item != "\xa0"
                )
            received = _read_field(header, ("AXReceivedAt", "received_at"))
            message_key = "\x1f".join((subject, sender_address, received, body))
            message_id = "outlook-ax-" + hashlib.sha256(message_key.encode()).hexdigest()[:24]
            if message_id in exclude_ids:
                continue
            received_at = None
            if received:
                try:
                    received_at = datetime.fromisoformat(received.replace("Z", "+00:00"))
                except ValueError:
                    pass
            messages.append(GraphMessage(
                id=message_id, subject=subject, sender_name=sender_name,
                sender_address=sender_address, received_at=received_at, body=body,
            ))
        return messages


AccessibilityMailbox = OutlookAccessibilityMailbox
