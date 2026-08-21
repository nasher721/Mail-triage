"""Read an already-open Outlook on the web session through Edge CDP."""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from email_triage.config import ConfigurationError
from email_triage.graph import GRAPH_ROOT, GraphError, _parse_message, _text_to_html
from email_triage.models import GraphMessage

OWA_HOSTS = ("outlook.office.com", "outlook.office365.com", "outlook.live.com", "outlook.cloud.microsoft")
OUTLOOK_REST_ROOT = "https://outlook.office.com/api/v2.0"
EDGE_DEBUG_HELP = ("No Microsoft Edge debugging port is open. Outlook on the web can be used "
                   "without Graph admin, but Edge must be started with remote debugging so "
                   "this script can reuse a normal signed-in tab. Run: "
                   "bash scripts/open_outlook_in_edge.sh")


@dataclass(frozen=True)
class CapturedCookie:
    name: str
    value: str = field(repr=False)
    domain: str


@dataclass(frozen=True)
class CapturedAuth:
    token: str = field(repr=False)
    api_root: str = GRAPH_ROOT
    anchor_mailbox: str = field(default="", repr=False)
    cookies: tuple[CapturedCookie, ...] = field(default=(), repr=False)


def _outlook_cookie_domain(domain: str) -> bool:
    normalized = domain.lower().lstrip(".")
    return normalized == "outlook.office.com" or normalized.endswith(
        ".outlook.office.com"
    )


def _safe_cookie(cookie: CapturedCookie) -> bool:
    """Allow only cookie material that cannot inject another HTTP header."""
    if not cookie.name or any(character in cookie.name for character in "=;\r\n\0"):
        return False
    return not any(character in cookie.value for character in "\r\n\0")


def _capture_cookies(page: Any) -> tuple[CapturedCookie, ...]:
    try:
        raw_cookies = page.context.cookies()
    except Exception:
        return ()
    cookies: list[CapturedCookie] = []
    for raw in raw_cookies:
        name = str(raw.get("name", ""))
        domain = str(raw.get("domain", ""))
        if name and _outlook_cookie_domain(domain):
            cookies.append(CapturedCookie(name, str(raw.get("value", "")), domain))
    return tuple(cookies)


def _anchor_from_token(token: str) -> str:
    """Derive Outlook's PUID anchor in memory; never log or persist claims."""

    try:
        encoded = token.split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        claims = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        puid = str(claims.get("oid") or claims.get("puid") or "")
        tenant = str(claims.get("tid") or "")
        return f"PUID:{puid}@{tenant}" if puid and tenant else ""
    except (
        IndexError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ):
        return ""


def _session_headers(
    auth: CapturedAuth,
    *,
    token: str | None = None,
    json_body: bool = False,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token or auth.token}",
        "Accept": "application/json",
    }
    if auth.api_root == OUTLOOK_REST_ROOT:
        if auth.anchor_mailbox:
            headers["X-AnchorMailbox"] = auth.anchor_mailbox
        cookie = "; ".join(
        f"{item.name}={item.value}"
        for item in auth.cookies
        if _safe_cookie(item) and _outlook_cookie_domain(item.domain)
        )
        if cookie:
            headers["Cookie"] = cookie
    headers["Prefer"] = 'outlook.body-content-type="text"'
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _error(message: str, *, status_code: int | None = None, url: str | None = None,
           response_body: str | None = None, retryable: bool = False) -> GraphError:
    exc = GraphError(
        message,
        status=status_code,
        code="owa_http_error" if status_code is not None else "owa_error",
        operation="owa_request",
        retryable=retryable,
        detail=response_body,
    )
    exc.status_code = status_code
    exc.url = url
    exc.response_body = response_body
    exc.retryable = retryable
    exc.metadata = {"status_code": status_code, "url": url, "retryable": retryable}
    return exc


def _root_for_url(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
            return None
        if (parsed.hostname or "").lower() == "graph.microsoft.com":
            return GRAPH_ROOT
        if (parsed.hostname or "").lower() in OWA_HOSTS:
            return OUTLOOK_REST_ROOT
    except ValueError:
        pass
    return None


def _is_loopback_cdp_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "http"
            and not parsed.username
            and not parsed.password
            and (parsed.hostname or "").lower() in {"127.0.0.1", "::1", "localhost"}
        )
    except ValueError:
        return False


class OwaMailbox:
    def __init__(self, cdp_url: str, read_write: bool = False, max_scan_pages: int = 10):
        if not _is_loopback_cdp_url(cdp_url):
            raise ConfigurationError(
                "EDGE_CDP_URL must be an HTTP loopback endpoint with an explicit port"
            )
        self.cdp_url = cdp_url.rstrip("/")
        self.read_write = read_write
        self.max_scan_pages = max(1, int(max_scan_pages))
        self._auth: CapturedAuth | None = None
        self._folder_ids: dict[str, str] = {}

    def access_token(self) -> str:
        if self._auth is None:
            self._auth = capture_edge_auth(self.cdp_url)
        if not self._auth.token:
            raise _error("Outlook session token was empty")
        return self._auth.token

    def unread_messages(self, limit: int, exclude_ids: set[str] | None = None) -> list[GraphMessage]:
        if limit <= 0:
            return []
        token = self.access_token()
        auth = self._auth
        if auth is None:
            raise _error("Outlook session token not captured")
        rest = auth.api_root == OUTLOOK_REST_ROOT
        select = ("Id,InternetMessageId,Subject,From,ReceivedDateTime,HasAttachments,IsRead"
                  if rest else "id,internetMessageId,subject,from,receivedDateTime,hasAttachments,isRead,@odata.type")
        query = urlencode({"$filter": "IsRead eq false" if rest else "isRead eq false",
                           "$top": str(min(max(limit, 1), 50)), "$select": select})
        url: str | None = f"{auth.api_root}/me/mailFolders/inbox/messages?{query}"
        excluded = {str(item) for item in (exclude_ids or set())}
        messages: list[GraphMessage] = []
        pages = 0
        while url and len(messages) < limit and pages < self.max_scan_pages:
            if _root_for_url(url) != auth.api_root:
                raise _error("Outlook pagination link is not an approved API URL", url=url)
            payload = self._json_request("GET", url, absolute=True, token=token)
            token = self.access_token()
            pages += 1
            for raw in payload.get("value", []):
                message_id = str(_value(raw, "id") or "")
                if not message_id or message_id in excluded or _is_calendar_item(raw):
                    continue
                # Some Outlook deployments ignore $select and include the body in
                # the listing response. Reuse it; otherwise fetch the eligible body.
                detail = raw if _value(raw, "body") is not None else None
                if detail is None:
                    detail_query = urlencode({"$select": (
                        "Id,InternetMessageId,Subject,From,ReceivedDateTime,Body,HasAttachments,IsRead" if rest else
                        "id,internetMessageId,subject,from,receivedDateTime,body,sensitivity,hasAttachments,isRead")})
                    detail = self._json_request(
                        "GET",
                        f"{auth.api_root}/me/messages/{quote(message_id, safe='')}?{detail_query}",
                        absolute=True,
                        token=token,
                    )
                    token = self.access_token()
                messages.append(_parse_message(_normalize_outlook_message(detail) if rest else detail))
                if len(messages) >= limit:
                    break
            next_url = payload.get("@odata.nextLink")
            url = str(next_url) if next_url else None
        if url and len(messages) < limit:
            raise GraphError(
                "Outlook scan exceeded max_scan_pages",
                code="scan_limit",
                operation="pagination",
                retryable=True,
            )
        return messages

    def probe_access(self) -> None:
        """Validate the captured session without parsing or retaining response data."""

        if not _is_loopback_cdp_url(self.cdp_url):
            raise GraphError(
                "Outlook live probe requires a loopback Edge debugging endpoint",
                code="unsafe_cdp_url",
                operation="live_probe",
            )
        auth = self._auth or capture_edge_auth(self.cdp_url)
        try:
            rest = auth.api_root == OUTLOOK_REST_ROOT
            query = urlencode({"$top": "1", "$select": "Id" if rest else "id"})
            url = f"{auth.api_root}/me/mailFolders/inbox/messages?{query}"
            if _root_for_url(url) != auth.api_root:
                raise GraphError(
                    "Outlook live probe rejected an unapproved API origin",
                    code="unsafe_probe_origin",
                    operation="live_probe",
                )
            request = Request(
                url,
                headers=_session_headers(auth),
                method="GET",
            )
            try:
                with urlopen(request, timeout=30):
                    return
            except HTTPError as exc:
                if exc.code == 401:
                    refreshed = capture_edge_auth(self.cdp_url)
                    retry = Request(
                        url,
                        headers=_session_headers(refreshed),
                        method="GET",
                    )
                    try:
                        with urlopen(retry, timeout=30):
                            return
                    except HTTPError as retry_exc:
                        raise GraphError(
                            "Outlook live probe was denied",
                            status=retry_exc.code,
                            code="probe_denied",
                            operation="live_probe",
                            retryable=retry_exc.code >= 500,
                        ) from retry_exc
                    except (URLError, TimeoutError) as retry_exc:
                        raise GraphError(
                            "Outlook live probe transport failed",
                            code="probe_transport_error",
                            operation="live_probe",
                            retryable=True,
                        ) from retry_exc
                raise GraphError(
                    "Outlook live probe was denied",
                    status=exc.code,
                    code="probe_denied",
                    operation="live_probe",
                    retryable=exc.code >= 500,
                ) from exc
            except (URLError, TimeoutError) as exc:
                raise GraphError(
                    "Outlook live probe transport failed",
                    code="probe_transport_error",
                    operation="live_probe",
                    retryable=True,
                ) from exc
        finally:
            self._auth = None

    def _json_request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                      *, absolute: bool = False, token: str | None = None,
                      _allow_401_retry: bool = True) -> dict[str, Any]:
        if not self.read_write and method not in {"GET"}:
            raise _error("write operations require --apply with Outlook on the web")
        root = self._auth.api_root if self._auth else GRAPH_ROOT
        url = path if absolute else f"{root}{path}"
        if _root_for_url(url) != root:
            raise _error("Outlook request URL is not an approved HTTPS API URL", url=url)
        bearer = token or self.access_token()
        auth = self._auth or CapturedAuth(bearer, root)
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers=_session_headers(auth, token=bearer, json_body=payload is not None),
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
            return json.loads(body) if body else {}
        except HTTPError as exc:
            if exc.code == 401 and _allow_401_retry:
                self._auth = capture_edge_auth(self.cdp_url)
                return self._json_request(method, path, payload, absolute=absolute, token=self._auth.token,
                                          _allow_401_retry=False)
            raise _error(f"Outlook request failed ({exc.code})", status_code=exc.code, url=url,
                         retryable=exc.code in {408, 429, 500, 502, 503, 504}) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise _error("Outlook session request failed", url=url, retryable=True) from exc

    def ensure_folder_path(self, folder_path: str) -> str:
        if folder_path in self._folder_ids:
            return self._folder_ids[folder_path]
        parent, walked = "inbox", ""
        rest = self._auth is not None and self._auth.api_root == OUTLOOK_REST_ROOT
        for segment in [p.strip() for p in folder_path.split("/") if p.strip()]:
            walked = f"{walked}/{segment}" if walked else segment
            if walked in self._folder_ids:
                parent = self._folder_ids[walked]
                continue
            escaped = segment.replace("'", "''")
            query = urlencode({"$filter": f"{'DisplayName' if rest else 'displayName'} eq '{escaped}'",
                               "$select": "Id,DisplayName" if rest else "id,displayName", "$top": "10"})
            found = self._json_request("GET", f"/me/mailFolders/{quote(parent, safe='')}/childFolders?{query}")
            matches = [i for i in found.get("value", []) if _value(i, "displayName") == segment and _value(i, "id")]
            if len(matches) > 1:
                raise _error(f"Outlook folder path is ambiguous: {segment!r}")
            if matches:
                folder_id = str(_value(matches[0], "id"))
            else:
                created = self._json_request("POST", f"/me/mailFolders/{quote(parent, safe='')}/childFolders",
                                              {"DisplayName" if rest else "displayName": segment})
                folder_id = str(_value(created, "id") or "")
                if not folder_id:
                    raise _error(f"Outlook session did not return an id for folder {segment!r}")
            self._folder_ids[walked] = folder_id
            parent = folder_id
        return parent

    def update_message(self, message_id: str, categories: tuple[str, ...] | None = None, is_read: bool | None = None) -> None:
        rest = self._auth is not None and self._auth.api_root == OUTLOOK_REST_ROOT
        payload: dict[str, Any] = {}
        if categories is not None: payload["Categories" if rest else "categories"] = list(categories)
        if is_read is not None: payload["IsRead" if rest else "isRead"] = is_read
        if payload: self._json_request("PATCH", f"/me/messages/{quote(message_id, safe='')}", payload)

    def create_reply_draft(self, message_id: str, reply_text: str) -> str:
        rest = self._auth is not None and self._auth.api_root == OUTLOOK_REST_ROOT
        # createReply accepts a comment, not a Message.body payload. Supplying a
        # body is rejected by Outlook on the web with HTTP 400.
        payload = {"Comment" if rest else "comment": reply_text}
        return str(_value(self._json_request("POST", f"/me/messages/{quote(message_id, safe='')}/createReply", payload), "id") or "")

    def move_message(self, message_id: str, folder_id: str) -> str:
        rest = self._auth is not None and self._auth.api_root == OUTLOOK_REST_ROOT
        payload = {"DestinationId" if rest else "destinationId": folder_id}
        return str(_value(self._json_request("POST", f"/me/messages/{quote(message_id, safe='')}/move", payload), "id") or "")


def edge_debug_available(cdp_url: str) -> bool:
    if not _is_loopback_cdp_url(cdp_url):
        return False
    try:
        with urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=2) as response:
            return 200 <= getattr(response, "status", 200) < 300
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def session_diagnostic(cdp_url: str) -> dict[str, object]:
    available = edge_debug_available(cdp_url)
    return {"available": available, "code": "ready" if available else "cdp_unreachable",
            "detail": "Edge debugging endpoint only; no session token or mail was read."}


# Descriptive aliases kept for callers that expose a backend diagnostic verb.
owa_session_diagnostic = session_diagnostic
diagnose_session = session_diagnostic


def capture_edge_auth(cdp_url: str) -> CapturedAuth:
    if not _is_loopback_cdp_url(cdp_url):
        raise ConfigurationError(
            "EDGE_CDP_URL must be an HTTP loopback endpoint with an explicit port"
        )
    if not edge_debug_available(cdp_url):
        raise ConfigurationError(EDGE_DEBUG_HELP)
    try:
        from playwright.sync_api import sync_playwright
        playwright = sync_playwright().start()
        browser = None
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            page = _outlook_page(browser)
            auth = _auth_from_page(page)
            if auth is not None:
                auth = replace(
                    auth,
                    anchor_mailbox=(
                        auth.anchor_mailbox or _anchor_from_token(auth.token)
                    ),
                    cookies=_capture_cookies(page),
                )
        finally:
            # Playwright.stop() closes browsers it owns. This browser belongs to the
            # user and was only attached over CDP, so replace close during teardown.
            if browser is not None:
                browser.close = lambda *args, **kwargs: None
            playwright.stop()
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError("Could not inspect the signed-in Outlook tab") from exc
    if auth is None:
        raise ConfigurationError("No approved Outlook API bearer token was observed in the tab")
    return auth


def _outlook_page(browser: Any) -> Any:
    for context in browser.contexts:
        for page in context.pages:
            if (urlsplit(page.url or "").hostname or "").lower() in OWA_HOSTS:
                return page
    raise ConfigurationError("No Outlook on the web tab is open in Edge")


def _auth_from_page(page: Any) -> CapturedAuth | None:
    captured: dict[str, CapturedAuth] = {}
    def on_request(request: Any) -> None:
        headers = {str(key).lower(): value for key, value in request.headers.items()}
        authorization = headers.get("authorization", "")
        if not authorization.lower().startswith("bearer "): return
        root = _root_for_url(request.url)
        if root:
            token = authorization.split(None, 1)[1].strip()
            if token:
                captured[root] = CapturedAuth(
                    token,
                    root,
                    anchor_mailbox=str(headers.get("x-anchormailbox", "")),
                )
    page.on("request", on_request)
    _nudge_inbox(page)
    for _ in range(12):
        if captured:
            break
        page.wait_for_timeout(250)
    if not captured:
        try:
            page.reload(wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        for _ in range(80):
            if captured:
                break
            page.wait_for_timeout(250)
    return captured.get(OUTLOOK_REST_ROOT) or captured.get(GRAPH_ROOT)


def _nudge_inbox(page: Any) -> None:
    """Trigger a normal Inbox request without exposing or persisting its token."""

    for selector in ('[aria-label="Inbox"]', 'span:text-is("Inbox")'):
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click(timeout=2000)
                return
        except Exception:
            continue


def _value(payload: dict[str, Any], camel_name: str) -> Any:
    return payload.get(camel_name, payload.get(camel_name[:1].upper() + camel_name[1:]))


def _is_calendar_item(raw: dict[str, Any]) -> bool:
    kind = str(raw.get("@odata.type") or raw.get("odata.type") or "").lower()
    item_class = str(raw.get("itemClass") or raw.get("ItemClass") or "").lower()
    return "event" in kind or item_class.startswith("ipm.appointment")


def _normalize_outlook_message(raw: dict[str, Any]) -> dict[str, Any]:
    sender, body = _value(raw, "from") or {}, _value(raw, "body") or {}
    address = _value(sender, "emailAddress") or {}
    return {"id": _value(raw, "id"), "internetMessageId": _value(raw, "internetMessageId"),
            "subject": _value(raw, "subject") or "", "from": {"emailAddress": {
                "name": _value(address, "name") or "", "address": _value(address, "address") or ""}},
            "receivedDateTime": _value(raw, "receivedDateTime"),
            "body": {"contentType": _value(body, "contentType") or "text", "content": _value(body, "content") or ""},
            "hasAttachments": bool(_value(raw, "hasAttachments")), "isRead": bool(_value(raw, "isRead"))}
