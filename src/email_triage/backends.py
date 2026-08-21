"""Explicit, metadata-only mailbox capability reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class BackendCapabilities:
    source: str
    authentication: str
    read_scope: str
    supports_apply: bool
    supports_incremental_scan: bool
    metadata_prefilter: bool
    bulk_body_export: bool = False
    attachment_content: bool = False
    send_mail: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CAPABILITIES = {
    "graph": BackendCapabilities(
        "graph", "delegated_oauth", "unread_inbox", True, True, True
    ),
    "owa": BackendCapabilities(
        "owa", "existing_edge_session", "unread_inbox", True, True, True
    ),
    "local": BackendCapabilities(
        "local", "none", "user_selected_files", False, False, False
    ),
    "desktop": BackendCapabilities(
        "desktop",
        "macos_accessibility",
        "frontmost_outlook_message_only",
        False,
        False,
        False,
    ),
    "accessibility": BackendCapabilities(
        "accessibility",
        "macos_accessibility",
        "visible_unread_inbox_rows",
        False,
        False,
        True,
    ),
}


def backend_capabilities(source: str) -> BackendCapabilities:
    return CAPABILITIES[source]
