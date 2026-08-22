from __future__ import annotations

import json
import os
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from email_triage.models import GraphMessage

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
READ_SCOPES = "openid offline_access https://graph.microsoft.com/Mail.Read"
READ_WRITE_SCOPES = "openid offline_access https://graph.microsoft.com/Mail.ReadWrite"


class GraphError(RuntimeError):
    """A Microsoft Graph failure with machine-readable context."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None,
                 operation: str | None = None, retryable: bool = False, detail: str | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.operation = status, code, operation
        self.retryable, self.detail = retryable, detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code or "graph_error",
            "status": self.status,
            "operation": self.operation,
            "retryable": self.retryable,
            "message": str(self),
        }

    def is_missing_item(self) -> bool:
        """True when one mailbox item is gone; other Graph errors are backend failures."""

        if self.status == 404:
            return True
        return (self.code or "").casefold() in {
            "erroritemnotfound",
            "itemnotfound",
            "errorinvalidmailboxitemid",
        }


class GraphMailbox:
    def __init__(self, tenant_id: str, client_id: str, cache_path: Path, read_write: bool = False,
                 interactive: bool = True, *, max_scan_pages: int = 10) -> None:
        if max_scan_pages < 1:
            raise ValueError("max_scan_pages must be positive")
        self.tenant_id, self.client_id, self.cache_path = tenant_id, client_id, cache_path
        self.scopes = READ_WRITE_SCOPES if read_write else READ_SCOPES
        self.interactive, self.max_scan_pages = interactive, max_scan_pages
        self._folder_ids: dict[str, str] = {}

    def _token_url(self, path: str) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/{path}"

    def _post_form(self, url: str, values: dict[str, str]) -> dict[str, Any]:
        request = Request(url, data=urlencode(values).encode("ascii"),
                          headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            try: payload = json.loads(exc.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError): payload = {}
            payload.setdefault("error", f"http_{exc.code}")
            return payload
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GraphError("Microsoft authentication endpoint was unavailable", operation="token") from exc

    def _load_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists(): return {}
        try: return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): return {}

    def _save_cache(self, token: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.cache_path.parent, 0o700)
        cached = {"access_token": token.get("access_token"), "refresh_token": token.get("refresh_token"),
                  "expires_at": int(time.time()) + int(token.get("expires_in", 0)), "scopes": self.scopes}
        self.cache_path.write_text(json.dumps(cached), encoding="utf-8")
        os.chmod(self.cache_path, 0o600)

    def _refresh_access_token(
        self,
        cached: dict[str, Any] | None = None,
        *,
        persist: bool = True,
    ) -> str | None:
        cached = cached or self._load_cache(); refresh_token = cached.get("refresh_token")
        if not refresh_token: return None
        refreshed = self._post_form(self._token_url("token"), {"client_id": self.client_id,
            "grant_type": "refresh_token", "refresh_token": str(refresh_token), "scope": self.scopes})
        if not refreshed.get("access_token"): return None
        refreshed.setdefault("refresh_token", refresh_token)
        if persist:
            self._save_cache(refreshed)
        return str(refreshed["access_token"])

    def access_token(self, *, persist: bool = True) -> str:
        cached = self._load_cache()
        if cached.get("scopes", READ_SCOPES) != self.scopes: cached = {}
        if cached.get("access_token") and int(cached.get("expires_at", 0)) > time.time() + 120:
            return str(cached["access_token"])
        refreshed = self._refresh_access_token(cached, persist=persist)
        if refreshed: return refreshed
        if not self.interactive:
            raise GraphError("No usable cached Microsoft credential. Sign in once from terminal with "
                             "`email-triage --login` (add --apply read-write scope); scheduled runs then silently.", code="authentication_required", operation="token")
        flow = self._post_form(self._token_url("devicecode"), {"client_id": self.client_id, "scope": self.scopes})
        if "device_code" not in flow: raise GraphError("Microsoft device authorization could not be started", operation="devicecode")
        print(flow.get("message", "Complete Microsoft device-code sign-in."))
        interval = max(int(flow.get("interval", 5)), 1); deadline = time.time() + int(flow.get("expires_in", 900))
        while time.time() < deadline:
            token = self._post_form(self._token_url("token"), {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                                                  "client_id": self.client_id, "device_code": str(flow["device_code"])})
            if token.get("access_token"):
                if persist:
                    self._save_cache(token)
                return str(token["access_token"])
            error = token.get("error")
            if error not in {"authorization_pending", "slow_down"}:
                raise GraphError(f"Microsoft authentication failed: {error or 'unknown_error'}", operation="devicecode", code=error)
            if error == "slow_down": interval += 5
            time.sleep(interval)
        raise GraphError("Microsoft device authorization expired before completion", operation="devicecode")

    @staticmethod
    def _validated_next_link(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com":
            raise GraphError("Microsoft Graph returned an unsafe pagination link", code="unsafe_next_link", operation="pagination")
        return url

    def _request_json(self, request: Request, *, token: str, operation: str) -> tuple[dict[str, Any], str]:
        try:
            with urlopen(request, timeout=30) as response: return json.load(response), token
        except HTTPError as exc:
            if exc.code == 401:
                refreshed = self._refresh_access_token()
                if refreshed:
                    retry = Request(
                        request.full_url,
                        headers=dict(request.headers),
                        method=request.get_method(),
                    )
                    retry.add_header("Authorization", f"Bearer {refreshed}")
                    try:
                        with urlopen(retry, timeout=30) as response: return json.load(response), refreshed
                    except HTTPError as retry_exc:
                        detail = retry_exc.read().decode("utf-8", errors="replace")[:400]
                        raise GraphError(f"Microsoft Graph returned HTTP {retry_exc.code}; verify delegated Mail.Read consent",
                                         status=retry_exc.code, operation=operation, detail=detail) from retry_exc
                    except (URLError, TimeoutError, json.JSONDecodeError) as retry_exc:
                        raise GraphError(
                            "Microsoft Graph retry failed",
                            code="retry_transport_error",
                            operation=operation,
                            retryable=True,
                        ) from retry_exc
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise GraphError(f"Microsoft Graph returned HTTP {exc.code}; verify delegated Mail.Read consent",
                             status=exc.code, operation=operation, retryable=exc.code >= 500, detail=detail) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GraphError("Microsoft Graph message retrieval failed", operation=operation, retryable=True) from exc

    def unread_messages(self, limit: int, exclude_ids: set[str] | None = None) -> list[GraphMessage]:
        if limit <= 0: return []
        excluded = exclude_ids or set(); token = self.access_token()
        query = urlencode({"$filter": "isRead eq false", "$top": str(min(max(limit, 1), 50)),
                           "$select": "id,internetMessageId,subject,from,receivedDateTime,sensitivity,hasAttachments,isRead,@odata.type"})
        url: str | None = f"{GRAPH_ROOT}/me/mailFolders/inbox/messages?{query}"
        messages: list[GraphMessage] = []; pages = 0
        while url and len(messages) < limit:
            pages += 1
            if pages > self.max_scan_pages: raise GraphError("Microsoft Graph scan exceeded max_scan_pages", code="scan_limit", operation="pagination")
            request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            payload, token = self._request_json(request, token=token, operation="list_messages")
            for raw in payload.get("value", []):
                item_id = str(raw.get("id", "")); odata_type = str(raw.get("@odata.type", "")).lower()
                if not item_id or item_id in excluded or "event" in odata_type or "calendar" in odata_type: continue
                detail_query = urlencode({"$select": "id,internetMessageId,subject,from,receivedDateTime,body,sensitivity,hasAttachments,isRead,@odata.type"})
                detail_request = Request(f"{GRAPH_ROOT}/me/messages/{quote(item_id, safe='')}?{detail_query}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Prefer": 'outlook.body-content-type="text"'})
                detailed, token = self._request_json(detail_request, token=token, operation="get_message")
                messages.append(_parse_message(detailed))
                if len(messages) >= limit: break
            next_link = payload.get("@odata.nextLink")
            url = self._validated_next_link(str(next_link)) if next_link else None
        return messages

    def probe_access(self) -> None:
        """Validate read access without parsing or retaining the response body."""

        token = self.access_token(persist=False)
        query = urlencode({"$top": "1", "$select": "id"})
        request = Request(
            f"{GRAPH_ROOT}/me/mailFolders/inbox/messages?{query}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=30):
                return
        except HTTPError as exc:
            if exc.code == 401:
                refreshed = self._refresh_access_token(persist=False)
                if refreshed:
                    retry = Request(
                        request.full_url,
                        headers={
                            "Authorization": f"Bearer {refreshed}",
                            "Accept": "application/json",
                        },
                    )
                    try:
                        with urlopen(retry, timeout=30):
                            return
                    except HTTPError as retry_exc:
                        raise GraphError(
                            "Microsoft Graph live probe was denied",
                            status=retry_exc.code,
                            code="probe_denied",
                            operation="live_probe",
                            retryable=retry_exc.code >= 500,
                        ) from retry_exc
                    except (URLError, TimeoutError) as retry_exc:
                        raise GraphError(
                            "Microsoft Graph live probe transport failed",
                            code="probe_transport_error",
                            operation="live_probe",
                            retryable=True,
                        ) from retry_exc
            raise GraphError(
                "Microsoft Graph live probe was denied",
                status=exc.code,
                code="probe_denied",
                operation="live_probe",
                retryable=exc.code >= 500,
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise GraphError(
                "Microsoft Graph live probe transport failed",
                code="probe_transport_error",
                operation="live_probe",
                retryable=True,
            ) from exc

    def _json_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.scopes != READ_WRITE_SCOPES: raise GraphError("write operations require a read-write Graph session", operation=method)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        token = self.access_token()
        request = Request(f"{GRAPH_ROOT}{path}", data=body,
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read(); return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if exc.code == 401:
                refreshed = self._refresh_access_token()
                if refreshed:
                    retry = Request(f"{GRAPH_ROOT}{path}", data=body,
                                    headers={"Authorization": f"Bearer {refreshed}", "Accept": "application/json", "Content-Type": "application/json"}, method=method)
                    try:
                        with urlopen(retry, timeout=30) as response:
                            raw = response.read(); return json.loads(raw) if raw else {}
                    except HTTPError as retry_exc:
                        detail = retry_exc.read().decode("utf-8", errors="replace")[:400]
                        raise GraphError(f"Microsoft Graph {method} {path} failed ({retry_exc.code}): {detail}", status=retry_exc.code, operation=method, detail=detail) from retry_exc
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise GraphError(f"Microsoft Graph {method} {path} failed ({exc.code}): {detail}", status=exc.code, operation=method, detail=detail) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GraphError(f"Microsoft Graph {method} {path} failed", operation=method, retryable=True) from exc

    def ensure_folder_path(self, folder_path: str) -> str:
        if folder_path in self._folder_ids: return self._folder_ids[folder_path]
        parent, walked = "inbox", ""
        for segment in (part.strip() for part in folder_path.split("/") if part.strip()):
            walked = f"{walked}/{segment}" if walked else segment; cached = self._folder_ids.get(walked)
            if cached: parent = cached; continue
            escaped = segment.replace("'", "''")
            query = urlencode({"$filter": f"displayName eq '{escaped}'", "$select": "id,displayName", "$top": "10"})
            found = self._json_request("GET", f"/me/mailFolders/{quote(parent, safe='')}/childFolders?{query}")
            matches = [item for item in found.get("value", []) if item.get("displayName") == segment and item.get("id")]
            if len(matches) > 1: raise GraphError(f"Multiple exact-match folders found for {segment!r}", code="ambiguous_folder", operation="ensure_folder_path")
            if matches: folder_id = str(matches[0]["id"])
            else:
                created = self._json_request("POST", f"/me/mailFolders/{quote(parent, safe='')}/childFolders", {"displayName": segment})
                folder_id = str(created.get("id") or "")
            if not folder_id: raise GraphError(f"Microsoft Graph did not return an id for folder {segment!r}", operation="ensure_folder_path")
            self._folder_ids[walked] = folder_id; parent = folder_id
        return parent

    def update_message(self, message_id: str, categories: tuple[str, ...] | None = None, is_read: bool | None = None) -> None:
        payload: dict[str, Any] = {}
        if categories is not None: payload["categories"] = list(categories)
        if is_read is not None: payload["isRead"] = is_read
        if payload: self._json_request("PATCH", f"/me/messages/{quote(message_id, safe='')}", payload)

    def create_reply_draft(self, message_id: str, reply_text: str) -> str:
        draft = self._json_request("POST", f"/me/messages/{quote(message_id, safe='')}/createReply", {"comment": _text_to_html(reply_text)})
        return str(draft.get("id") or "")

    def move_message(self, message_id: str, folder_id: str) -> str:
        moved = self._json_request("POST", f"/me/messages/{quote(message_id, safe='')}/move", {"destinationId": folder_id})
        return str(moved.get("id") or message_id)


def _text_to_html(text: str) -> str:
    lines = escape(text).splitlines() or [""]
    return "<div>" + "<br>".join(lines) + "</div>"


def _parse_message(raw: dict[str, Any]) -> GraphMessage:
    sender = raw.get("from", {}).get("emailAddress", {}); received = raw.get("receivedDateTime")
    return GraphMessage(id=raw["id"], internet_message_id=raw.get("internetMessageId"), subject=raw.get("subject") or "",
        sender_name=sender.get("name") or "", sender_address=sender.get("address") or "",
        received_at=datetime.fromisoformat(received.replace("Z", "+00:00")) if received else None,
        body=(raw.get("body") or {}).get("content") or "", sensitivity=raw.get("sensitivity") or "normal",
        has_attachments=bool(raw.get("hasAttachments")), odata_type=raw.get("@odata.type") or "")
