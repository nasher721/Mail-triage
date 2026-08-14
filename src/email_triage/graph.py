from __future__ import annotations

import json
import os
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from email_triage.models import GraphMessage


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
READ_SCOPES = "openid offline_access https://graph.microsoft.com/Mail.Read"
#: Mail.ReadWrite covers folders, categories, and draft creation. Mail.Send is never
#: requested, so this application cannot send mail even if it were asked to.
READ_WRITE_SCOPES = "openid offline_access https://graph.microsoft.com/Mail.ReadWrite"


class GraphError(RuntimeError):
    """Raised when Microsoft Graph authentication or retrieval fails."""


class GraphMailbox:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        cache_path: Path,
        read_write: bool = False,
        interactive: bool = True,
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.cache_path = cache_path
        self.scopes = READ_WRITE_SCOPES if read_write else READ_SCOPES
        #: Scheduled runs set this False so a run never blocks on a device-code prompt.
        self.interactive = interactive
        self._folder_ids: dict[str, str] = {}

    def _token_url(self, path: str) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/{path}"

    def _post_form(self, url: str, values: dict[str, str]) -> dict[str, Any]:
        request = Request(
            url,
            data=urlencode(values).encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {}
            payload.setdefault("error", f"http_{exc.code}")
            return payload
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GraphError("Microsoft authentication endpoint was unavailable") from exc

    def _load_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self, token: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.cache_path.parent, 0o700)
        cached = {
            "access_token": token.get("access_token"),
            "refresh_token": token.get("refresh_token"),
            "expires_at": int(time.time()) + int(token.get("expires_in", 0)),
            "scopes": self.scopes,
        }
        self.cache_path.write_text(json.dumps(cached), encoding="utf-8")
        os.chmod(self.cache_path, 0o600)

    def access_token(self) -> str:
        cached = self._load_cache()
        # A cached token issued for narrower scopes cannot authorize write calls.
        if cached.get("scopes", READ_SCOPES) != self.scopes:
            cached = {}
        if cached.get("access_token") and int(cached.get("expires_at", 0)) > time.time() + 120:
            return str(cached["access_token"])
        if cached.get("refresh_token"):
            refreshed = self._post_form(
                self._token_url("token"),
                {
                    "client_id": self.client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": str(cached["refresh_token"]),
                    "scope": self.scopes,
                },
            )
            if refreshed.get("access_token"):
                refreshed.setdefault("refresh_token", cached["refresh_token"])
                self._save_cache(refreshed)
                return str(refreshed["access_token"])

        if not self.interactive:
            raise GraphError(
                "No usable cached Microsoft credential. Sign in once from a terminal with "
                "`email-triage --login` (add --apply for read-write scope); scheduled runs "
                "then refresh silently."
            )

        flow = self._post_form(
            self._token_url("devicecode"),
            {"client_id": self.client_id, "scope": self.scopes},
        )
        if "device_code" not in flow:
            raise GraphError("Microsoft device authorization could not be started")
        print(flow.get("message", "Complete the Microsoft device-code sign-in."))
        interval = max(int(flow.get("interval", 5)), 1)
        deadline = time.time() + int(flow.get("expires_in", 900))
        while time.time() < deadline:
            token = self._post_form(
                self._token_url("token"),
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": self.client_id,
                    "device_code": str(flow["device_code"]),
                },
            )
            if token.get("access_token"):
                self._save_cache(token)
                return str(token["access_token"])
            error = token.get("error")
            if error not in {"authorization_pending", "slow_down"}:
                raise GraphError(f"Microsoft authentication failed: {error or 'unknown_error'}")
            if error == "slow_down":
                interval += 5
            time.sleep(interval)
        raise GraphError("Microsoft device authorization expired before completion")

    def unread_messages(self, limit: int) -> list[GraphMessage]:
        token = self.access_token()
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
        url: str | None = f"{GRAPH_ROOT}/me/mailFolders/inbox/messages?{query}"
        messages: list[GraphMessage] = []
        while url and len(messages) < limit:
            request = Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Prefer": 'outlook.body-content-type="text"',
                },
            )
            try:
                with urlopen(request, timeout=30) as response:
                    payload = json.load(response)
            except HTTPError as exc:
                raise GraphError(
                    f"Microsoft Graph returned HTTP {exc.code}; verify delegated Mail.Read consent"
                ) from exc
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                raise GraphError("Microsoft Graph message retrieval failed") from exc
            for raw in payload.get("value", []):
                messages.append(_parse_message(raw))
                if len(messages) >= limit:
                    break
            url = payload.get("@odata.nextLink")
        return messages

    # --- write operations (require read_write=True) -------------------------------

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.scopes != READ_WRITE_SCOPES:
            raise GraphError("write operations require a read-write Graph session")
        request = Request(
            f"{GRAPH_ROOT}{path}",
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise GraphError(f"Microsoft Graph {method} {path} failed ({exc.code}): {detail}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GraphError(f"Microsoft Graph {method} {path} failed") from exc

    def ensure_folder_path(self, folder_path: str) -> str:
        """Create or reuse a nested mail folder such as 'AI Triage/Needs Reply'."""

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
                    raise GraphError(f"Microsoft Graph did not return an id for folder {segment!r}")
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
        if not payload:
            return
        self._json_request("PATCH", f"/me/messages/{message_id}", payload)

    def create_reply_draft(self, message_id: str, reply_text: str) -> str:
        """Create an unsent reply draft. This never sends; no Mail.Send scope is held."""

        draft = self._json_request(
            "POST",
            f"/me/messages/{message_id}/createReply",
            {"comment": _text_to_html(reply_text)},
        )
        return str(draft.get("id") or "")

    def move_message(self, message_id: str, folder_id: str) -> str:
        """Move a message and return its new identifier."""

        moved = self._json_request(
            "POST",
            f"/me/messages/{message_id}/move",
            {"destinationId": folder_id},
        )
        return str(moved.get("id") or message_id)


def _text_to_html(text: str) -> str:
    lines = escape(text).splitlines() or [""]
    return "<div>" + "<br>".join(lines) + "</div>"


def _parse_message(raw: dict[str, Any]) -> GraphMessage:
    sender = raw.get("from", {}).get("emailAddress", {})
    received = raw.get("receivedDateTime")
    return GraphMessage(
        id=raw["id"],
        internet_message_id=raw.get("internetMessageId"),
        subject=raw.get("subject") or "",
        sender_name=sender.get("name") or "",
        sender_address=sender.get("address") or "",
        received_at=datetime.fromisoformat(received.replace("Z", "+00:00")) if received else None,
        body=(raw.get("body") or {}).get("content") or "",
        sensitivity=raw.get("sensitivity") or "normal",
        has_attachments=bool(raw.get("hasAttachments")),
        odata_type=raw.get("@odata.type") or "",
    )
