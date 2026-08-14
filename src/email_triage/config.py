from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised when a required secure configuration value is missing."""


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < 1:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def is_loopback_url(url: str) -> bool:
    """True when inference stays on this machine (no email body leaves the host)."""

    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    client_id: str
    ai_backend: str
    openai_api_key: str
    openai_model: str
    ollama_host: str
    ollama_model: str
    external_ai_approved: bool
    intercept_clinical: bool
    mailbox_source: str
    input_path: Path | None
    max_unread_messages: int
    max_body_characters: int
    output_dir: Path

    @property
    def uses_local_inference(self) -> bool:
        return self.ai_backend == "ollama" and is_loopback_url(self.ollama_host)

    @classmethod
    def from_env(cls, input_path: str | None = None) -> "Settings":
        resolved_input = (input_path or os.getenv("TRIAGE_INPUT", "")).strip()
        tenant_id = os.getenv("MS_TENANT_ID", "").strip()
        client_id = os.getenv("MS_CLIENT_ID", "").strip()
        mailbox_source = "local" if resolved_input else "graph"
        if mailbox_source == "graph":
            missing_graph = [
                name
                for name, value in (("MS_TENANT_ID", tenant_id), ("MS_CLIENT_ID", client_id))
                if not value
            ]
            if missing_graph:
                raise ConfigurationError(
                    "Microsoft Graph is not configured. Pass --input path/to/messages.jsonl "
                    "to screen local files without Entra access, or set MS_TENANT_ID and "
                    "MS_CLIENT_ID."
                )

        backend = os.getenv("TRIAGE_BACKEND", "ollama").strip().lower() or "ollama"
        if backend not in {"ollama", "openai"}:
            raise ConfigurationError("TRIAGE_BACKEND must be ollama or openai")

        ollama_host = (
            os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
            or "http://127.0.0.1:11434"
        )
        ollama_model = os.getenv("OLLAMA_MODEL", "qwen3:8b").strip() or "qwen3:8b"
        openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o"
        local_inference = backend == "ollama" and is_loopback_url(ollama_host)

        if backend == "openai":
            if not openai_api_key:
                raise ConfigurationError("Missing required environment variables: OPENAI_API_KEY")
            approved = _bool("EXTERNAL_AI_APPROVED")
            if not approved:
                raise ConfigurationError(
                    "EXTERNAL_AI_APPROVED is false. Obtain institutional privacy/security "
                    "approval before transmitting email bodies to an external AI service."
                )
        elif not local_inference:
            approved = _bool("EXTERNAL_AI_APPROVED")
            if not approved:
                raise ConfigurationError(
                    "OLLAMA_HOST is not loopback. Obtain institutional privacy/security "
                    "approval before transmitting email bodies to a remote inference host."
                )
        else:
            approved = False

        return cls(
            tenant_id=tenant_id,
            client_id=client_id,
            ai_backend=backend,
            openai_api_key=openai_api_key,
            openai_model=openai_model,
            ollama_host=ollama_host.rstrip("/"),
            ollama_model=ollama_model,
            external_ai_approved=approved,
            intercept_clinical=not local_inference,
            mailbox_source=mailbox_source,
            input_path=Path(resolved_input).expanduser() if resolved_input else None,
            max_unread_messages=_positive_int("MAX_UNREAD_MESSAGES", 20),
            max_body_characters=_positive_int("MAX_BODY_CHARACTERS", 12_000),
            output_dir=Path(os.getenv("TRIAGE_OUTPUT_DIR", "var")).expanduser(),
        )
