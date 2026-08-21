from __future__ import annotations

import json
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from email_triage.config import ConfigurationError
from email_triage.models import GraphMessage


class LocalMailbox:
    """Read synthetic or exported messages from JSONL, JSON, or .eml files."""

    def __init__(self, path: Path):
        self.path = path

    def unread_messages(
        self, limit: int, exclude_ids: set[str] | None = None
    ) -> list[GraphMessage]:
        if not self.path.exists():
            raise ConfigurationError(f"Input path does not exist: {self.path}")
        messages: list[GraphMessage] = []
        for index, source in enumerate(_iter_source_files(self.path), start=1):
            messages.extend(_load_file(source, index))
            if len(messages) >= limit:
                break
        excluded = exclude_ids or set()
        return [message for message in messages if message.id not in excluded][:limit]


def _iter_source_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    files = sorted(
        item
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in {".jsonl", ".json", ".eml"}
    )
    if not files:
        raise ConfigurationError(
            f"No .jsonl, .json, or .eml files found in {path}"
        )
    return files


def _load_file(path: Path, file_index: int) -> list[GraphMessage]:
    suffix = path.suffix.lower()
    if suffix == ".eml":
        return [_parse_eml(path, file_index)]
    raw = path.read_text(encoding="utf-8")
    if suffix == ".jsonl" or path.name.endswith(".jsonl"):
        records = _parse_jsonl(raw)
    else:
        records = _parse_json_document(raw)
    return [
        _message_from_dict(record, f"{path.stem}-{offset}")
        for offset, record in enumerate(records, start=1)
    ]


def _parse_jsonl(raw: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"Invalid JSONL on line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(value, dict):
            raise ConfigurationError(f"JSONL line {line_number} must be an object")
        records.append(value)
    return records


def _parse_json_document(raw: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON: {exc.msg}") from exc
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    raise ConfigurationError("JSON input must be an object or an array of objects")


def _parse_eml(path: Path, file_index: int) -> GraphMessage:
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    sender = str(message.get("from", ""))
    name, address = _split_from(sender)
    received = message.get("date")
    body_part = message.get_body(preferencelist=("plain", "html"))
    body = str(body_part.get_content()) if body_part is not None else ""
    received_at = None
    if received:
        try:
            received_at = parsedate_to_datetime(received)
        except (TypeError, ValueError, IndexError):
            received_at = None
    return GraphMessage(
        id=str(message.get("message-id") or f"eml-{file_index}-{path.stem}"),
        internet_message_id=str(message.get("message-id") or "") or None,
        subject=str(message.get("subject") or ""),
        sender_name=name,
        sender_address=address,
        received_at=received_at,
        body=body,
        has_attachments=any(part.get_filename() for part in message.iter_attachments()),
    )


def _split_from(value: str) -> tuple[str, str]:
    if "<" in value and value.endswith(">"):
        name, address = value.rsplit("<", 1)
        return name.strip().strip('"'), address[:-1].strip()
    return "", value.strip()


def _message_from_dict(raw: dict[str, Any], fallback_id: str) -> GraphMessage:
    sender = raw.get("from") if isinstance(raw.get("from"), dict) else {}
    received = raw.get("received_at") or raw.get("receivedDateTime")
    received_at = None
    if isinstance(received, str) and received:
        try:
            received_at = datetime.fromisoformat(received.replace("Z", "+00:00"))
        except ValueError:
            received_at = None
    return GraphMessage(
        id=str(raw.get("id") or fallback_id),
        internet_message_id=(
            str(raw["internet_message_id"]) if raw.get("internet_message_id") else None
        ),
        subject=str(raw.get("subject") or ""),
        sender_name=str(raw.get("sender_name") or sender.get("name") or ""),
        sender_address=str(raw.get("sender_address") or sender.get("address") or ""),
        received_at=received_at,
        body=str(raw.get("body") or ""),
        sensitivity=str(raw.get("sensitivity") or "normal"),
        has_attachments=bool(raw.get("has_attachments") or raw.get("hasAttachments")),
    )
