"""Read and sort Outlook on the web from an already-open Microsoft Edge tab.

This path exists because institutional Outlook often blocks third-party Graph
apps, and Windows COM is unavailable on macOS. Outlook in Edge is already
signed in as the first-party web client. The script attaches to that browser
over Chrome DevTools Protocol, captures the session bearer token Outlook itself
uses, then calls Microsoft Graph with that token. No Entra admin consent and no
.eml export are required.

Edge must be started once with remote debugging (user-level, no admin):

    bash scripts/open_outlook_in_edge.sh

Mail is never sent. Apply mode still only files, categorizes, and saves unsent drafts.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from email_triage.config import ConfigurationError
from email_triage.graph import GRAPH_ROOT, GraphError, _parse_message, _text_to_html
from email_triage.models import GraphMessage

OWA_HOSTS = (
    "outlook.office.com",
    "outlook.office365.com",
    "outlook.live.com",
    "outlook.cloud.microsoft",
)
EDGE_DEBUG_HELP = (
    "No Microsoft Edge debugging port is open. Outlook on the web can be used "
    "without Graph admin, but Edge must be started with remote debugging so this "
    "script can reuse the already-signed-in tab. Quit Edge, then run: "
    "bash scripts/open_outlook_in_edge.sh"
)


@dataclass(frozen=True)
class CapturedAuth:
    token: str
    api_root: str = GRAPH_ROOT


class OwaMailbox:
    """Mailbox backed by the Outlook-in-Edge session instead of an Entra app."""

    def __init__(self, cdp_url: str, read_write: bool = False):
        self.cdp_url = cdp_url.rstrip("/")
        self.read_write = read_write
        self._auth: CapturedAuth | None = None
        self._folder_ids: dict[str, str] = {}

    def access_token(self) -> str:
        if self._auth is None:
            self._auth = capture_edge_auth(self.cdp_url)
        return self._auth.token

    def unread_messages(self, limit: int) -> list[GraphMessage]:
        token = self.access_token()
        auth = self._auth
        if auth is None:
            raise GraphError("Outlook session token was not captured")
        query = urlencode(
            {
                "$filter": "isRead eq false",
                "$top": str(min(limit, 50)),
                "$select": (
                    "id,internetMessageId,subject,from,receivedDateTime,body,"
                    "sensitivity,hasAttachments,isRead"
                ),
            }
        )
        url: str | None = f"{auth.api_root}/me/mailFolders/inbox/messages?{query}"
        messages: list[GraphMessage] = []
        while url and len(messages) < limit:
            payload = self._json_request("GET", url, absolute=True, token=token)
            for raw in payload.get("value", []):
                messages.append(_parse_message(raw))
                if len(messages) >= limit:
                    break
            url = payload.get("@odata.nextLink")
        return messages

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        absolute: bool = False,
        token: str | None = None,
    ) -> dict[str, Any]:
        if self.read_write is False and method not in {"GET"}:
            raise GraphError("write operations require --apply with Outlook on the web")
        root = self._auth.api_root if self._auth else GRAPH_ROOT
        url = path if absolute else f"{root}{path}"
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={
                "Authorization": f"Bearer {token or self.access_token()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Prefer": 'outlook.body-content-type="text"',
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise GraphError(f"Outlook session {method} failed ({exc.code}): {detail}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GraphError("Outlook session request failed") from exc

    def ensure_folder_path(self, folder_path: str) -> str:
        if folder_path in self._folder_ids:
            return self._folder_ids[folder_path]
        parent = "inbox"
        walked = ""
        for segment in [part.strip() for part in folder_path.split("/") if part.strip()]:
            walked = f"{walked}/{segment}" if walked else segment
            cached = self._folder_ids.get(walked)
            if cached:
                parent = cached
                continue
            escaped = segment.replace("'", "''")
            query = urlencode(
                {"$filter": f"displayName eq '{escaped}'", "$select": "id,displayName", "$top": "10"}
            )
            found = self._json_request("GET", f"/me/mailFolders/{parent}/childFolders?{query}")
            matches = [
                item
                for item in found.get("value", [])
                if item.get("displayName") == segment and item.get("id")
            ]
            if matches:
                folder_id = str(matches[0]["id"])
            else:
                created = self._json_request(
                    "POST",
                    f"/me/mailFolders/{parent}/childFolders",
                    {"displayName": segment},
                )
                folder_id = str(created.get("id") or "")
                if not folder_id:
                    raise GraphError(f"Outlook session did not return an id for folder {segment!r}")
            self._folder_ids[walked] = folder_id
            parent = folder_id
        return parent

    def update_message(
        self,
        message_id: str,
        categories: tuple[str, ...] | None = None,
        is_read: bool | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if categories is not None:
            payload["categories"] = list(categories)
        if is_read is not None:
            payload["isRead"] = is_read
        if payload:
            self._json_request("PATCH", f"/me/messages/{message_id}", payload)

    def create_reply_draft(self, message_id: str, reply_text: str) -> str:
        draft = self._json_request(
            "POST",
            f"/me/messages/{message_id}/createReply",
            {"comment": _text_to_html(reply_text)},
        )
        return str(draft.get("id") or "")

    def move_message(self, message_id: str, folder_id: str) -> str:
        moved = self._json_request(
            "POST",
            f"/me/messages/{message_id}/move",
            {"destinationId": folder_id},
        )
        return str(moved.get("id") or message_id)


def edge_debug_available(cdp_url: str) -> bool:
    try:
        with urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=2) as response:
            return 200 <= getattr(response, "status", 200) < 300
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def capture_edge_auth(cdp_url: str) -> CapturedAuth:
    """Attach to Edge, find the Outlook tab, and copy the bearer token it already uses."""

    if not edge_debug_available(cdp_url):
        raise ConfigurationError(EDGE_DEBUG_HELP)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ConfigurationError(
            "Outlook-in-Edge mode needs Playwright installed for this user (no admin): "
            "python3 -m pip install --user playwright"
        ) from exc

    token: str | None = None
    playwright = sync_playwright().start()
    browser = None
    try:
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            raise ConfigurationError(EDGE_DEBUG_HELP) from exc
        page = _outlook_page(browser)
        token = _token_from_page(page)
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(f"Could not attach to Outlook in Edge: {exc}") from exc
    finally:
        # stop() would otherwise Browser.close() the user's Outlook window.
        if browser is not None:
            browser.close = lambda *args, **kwargs: None
        playwright.stop()
    if not token:
        raise ConfigurationError(
            "Connected to Edge but did not see an Outlook bearer token. Open "
            "https://outlook.office.com/mail/inbox in that Edge window and retry."
        )
    return CapturedAuth(token=token, api_root=GRAPH_ROOT)


def _outlook_page(browser: Any) -> Any:
    for context in browser.contexts:
        for page in context.pages:
            url = (page.url or "").lower()
            if any(host in url for host in OWA_HOSTS):
                return page
    raise ConfigurationError(
        "Edge is open with debugging, but no Outlook on the web tab was found. "
        "Keep https://outlook.office.com/mail/ open in Edge and retry."
    )


def _token_from_page(page: Any) -> str | None:
    captured: dict[str, str] = {}

    def on_request(request: Any) -> None:
        if captured.get("token"):
            return
        url = request.url or ""
        if "graph.microsoft.com" not in url and "outlook.office.com" not in url:
            return
        headers = {key.lower(): value for key, value in request.headers.items()}
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and len(auth) > 20:
            captured["token"] = auth.split(" ", 1)[1].strip()

    page.on("request", on_request)
    deadline = time.time() + 20
    _nudge_inbox(page)
    while time.time() < deadline and not captured.get("token"):
        page.wait_for_timeout(250)
    return captured.get("token")


def _nudge_inbox(page: Any) -> None:
    """Trigger mail traffic without a full reload when possible."""

    selectors = (
        '[aria-label="Inbox"]',
        '[title="Inbox"]',
        'span:text-is("Inbox")',
    )
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click(timeout=2000)
                return
        except Exception:
            continue
    try:
        page.reload(wait_until="domcontentloaded", timeout=15000)
    except Exception:
        return
