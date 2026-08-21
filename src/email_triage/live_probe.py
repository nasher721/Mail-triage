"""Explicit live-backend probes with a fixed, non-sensitive result boundary."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from email_triage.config import ConfigurationError
from email_triage.desktop import OutlookDesktopMailbox
from email_triage.graph import GraphError, GraphMailbox
from email_triage.owa import OwaMailbox, _is_loopback_cdp_url, edge_debug_available


@dataclass(frozen=True)
class LiveProbeResult:
    source: str
    available: bool
    code: str
    detail: str
    request_scope: str = "metadata_only"
    retained_mail_data: bool = False
    model_contacted: bool = False
    mailbox_mutated: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def _failed(source: str, code: str, detail: str) -> LiveProbeResult:
    return LiveProbeResult(source, False, code, detail)


def run_live_probe(source: str) -> LiveProbeResult:
    """Perform one bounded read-only readiness check and return only fixed text."""

    if source == "graph":
        tenant_id = os.getenv("MS_TENANT_ID", "").strip()
        client_id = os.getenv("MS_CLIENT_ID", "").strip()
        if not tenant_id or not client_id:
            return _failed(
                source,
                "credentials_missing",
                "Set MS_TENANT_ID and MS_CLIENT_ID before running the live probe.",
            )
        output_dir = Path(os.getenv("TRIAGE_OUTPUT_DIR", "var")).expanduser()
        mailbox = GraphMailbox(
            tenant_id,
            client_id,
            output_dir / "oauth_token_cache.json",
            read_write=False,
            interactive=False,
        )
        try:
            mailbox.probe_access()
        except GraphError as exc:
            if exc.code == "authentication_required":
                return _failed(
                    source,
                    "authentication_required",
                    "Run `email-triage --login` once, then retry the live probe.",
                )
            code = (
                exc.code
                if exc.code in {"probe_denied", "probe_transport_error"}
                else "probe_failed"
            )
            return _failed(
                source,
                code,
                "The metadata-only Microsoft Graph request did not succeed.",
            )
        return LiveProbeResult(
            source,
            True,
            "ready",
            "Microsoft Graph accepted the metadata-only request.",
        )

    if source == "owa":
        cdp_url = (
            os.getenv("EDGE_CDP_URL", "").strip()
            or os.getenv("OWA_CDP_URL", "").strip()
            or "http://127.0.0.1:9222"
        )
        if not _is_loopback_cdp_url(cdp_url):
            return _failed(
                source,
                "unsafe_cdp_url",
                "The Edge debugging endpoint must be loopback-only.",
            )
        if not edge_debug_available(cdp_url):
            return _failed(
                source,
                "cdp_unreachable",
                "Open Outlook with scripts/open_outlook_in_edge.sh, then retry.",
            )
        try:
            OwaMailbox(cdp_url, read_write=False).probe_access()
        except (ConfigurationError, GraphError):
            return _failed(
                source,
                "session_probe_failed",
                "The signed-in Edge session did not complete the metadata-only request.",
            )
        return LiveProbeResult(
            source,
            True,
            "ready",
            "The signed-in Edge session accepted the metadata-only request.",
        )

    if source == "desktop":
        try:
            OutlookDesktopMailbox().probe_access()
        except ConfigurationError:
            return _failed(
                source,
                "desktop_probe_failed",
                "Open Outlook with a front window and allow Accessibility access.",
            )
        return LiveProbeResult(
            source,
            True,
            "ready",
            "Outlook front-window Accessibility is available; no visible text was read.",
            request_scope="front_window_presence_only",
        )

    return _failed(
        source,
        "unsupported_source",
        "Live probing supports only graph, owa, or desktop sources.",
    )
