from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from email_triage.providers import (
    PROVIDER_NAMES,
    ProviderProfile,
    ProviderSelection,
    api_key_from_environment,
    is_loopback_url,
    provider_profile,
)

__all__ = ["ConfigurationError", "Settings", "is_loopback_url"]


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


def _is_loopback_http_url(url: str) -> bool:
    """Validate a local browser-debugging endpoint without resolving DNS."""
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme == "http"
            and parsed.username is None
            and parsed.password is None
            and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
            and parsed.port is not None
        )
    except ValueError:
        return False


def _float(name: str, default: float | None = None) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not 0.0 <= value <= 2.0:
        raise ConfigurationError(f"{name} must be between 0 and 2")
    return value


def _env_prefix(profile: ProviderProfile) -> str:
    return profile.name.upper().replace("-", "_")


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _resolve_selection(
    profile: ProviderProfile,
    *,
    prefix: str,
    timeout: int,
    temperature: float | None,
    share_generic_env: bool = True,
    override_base_url: str | None = None,
    override_model: str | None = None,
) -> ProviderSelection:
    """Build one provider binding from the environment.

    ``prefix`` is empty for screening and ``AGENT_`` for the sorting agent, so
    ``TRIAGE_AGENT_MODEL`` overrides ``TRIAGE_MODEL`` for that role only.
    """

    vendor = _env_prefix(profile)
    base_names = [f"TRIAGE_{prefix}BASE_URL"]
    model_names = [f"TRIAGE_{prefix}MODEL"]
    key_names = [f"TRIAGE_{prefix}API_KEY"]
    if prefix and share_generic_env:
        base_names.append("TRIAGE_BASE_URL")
        model_names.append("TRIAGE_MODEL")
        key_names.append("TRIAGE_API_KEY")
    base_names.append(f"{vendor}_BASE_URL")
    model_names.append(f"{vendor}_MODEL")
    if profile.name == "ollama":
        base_names.append("OLLAMA_HOST")

    base_url = (override_base_url or "").strip() or _first_env(*base_names) or (
        profile.default_base_url
    )
    model = (override_model or "").strip() or _first_env(*model_names) or profile.default_model
    api_key = _first_env(*key_names) or api_key_from_environment(profile)
    return ProviderSelection(
        profile=profile,
        base_url=base_url.rstrip("/"),
        model=model,
        api_key=api_key,
        temperature=temperature,
        timeout=timeout,
    )


def _require_credentials(selection: ProviderSelection, role: str) -> None:
    profile = selection.profile
    if not selection.base_url:
        raise ConfigurationError(
            f"{profile.label} needs a base URL for {role}. "
            f"Set TRIAGE_BASE_URL or {_env_prefix(profile)}_BASE_URL."
        )
    if not selection.model:
        raise ConfigurationError(
            f"{profile.label} needs a model name for {role}. "
            f"Set TRIAGE_MODEL or {_env_prefix(profile)}_MODEL."
        )
    if profile.requires_api_key and not selection.api_key:
        variables = ", ".join(profile.api_key_env) or "TRIAGE_API_KEY"
        raise ConfigurationError(f"Missing required environment variables: {variables}")


def _approval_message(selection: ProviderSelection) -> str:
    if selection.profile.local:
        return (
            f"{selection.base_url} is not a loopback address. Obtain institutional "
            "privacy/security approval before transmitting email bodies to a remote "
            "inference host."
        )
    return (
        f"EXTERNAL_AI_APPROVED is false. Obtain institutional privacy/security "
        f"approval before transmitting email bodies to an external AI service "
        f"({selection.profile.label})."
    )


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    client_id: str
    screening: ProviderSelection
    agent: ProviderSelection
    external_ai_approved: bool
    intercept_clinical: bool
    mailbox_source: str
    input_path: Path | None
    owa_cdp_url: str
    max_unread_messages: int
    max_retrieval_pages: int
    max_body_characters: int
    output_dir: Path
    feedback_path: Path | None = None
    agent_max_rounds: int = 4
    apply_changes: bool = False
    mark_read: bool = False
    use_agent: bool = True

    @property
    def ai_provider(self) -> str:
        return self.screening.profile.name

    @property
    def ai_backend(self) -> str:
        """Historic alias for :attr:`ai_provider`."""

        return self.ai_provider

    @property
    def uses_local_inference(self) -> bool:
        """True when no message text leaves this machine for either role."""

        if not self.screening.keeps_data_local:
            return False
        return not self.use_agent or self.agent.keeps_data_local

    @classmethod
    def from_env(
        cls,
        input_path: str | None = None,
        apply_changes: bool | None = None,
        mark_read: bool | None = None,
        use_agent: bool | None = None,
        source: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        agent_provider: str | None = None,
        agent_model: str | None = None,
    ) -> "Settings":
        resolved_input = (input_path or os.getenv("TRIAGE_INPUT", "")).strip()
        tenant_id = os.getenv("MS_TENANT_ID", "").strip()
        client_id = os.getenv("MS_CLIENT_ID", "").strip()
        requested_source = (source or os.getenv("TRIAGE_SOURCE", "")).strip().lower()
        allowed_sources = {"graph", "local", "owa", "desktop", "accessibility"}
        if requested_source and requested_source not in allowed_sources:
            raise ConfigurationError(
                "TRIAGE_SOURCE must be graph, local, owa, desktop, or accessibility"
            )
        if requested_source:
            mailbox_source = requested_source
        elif resolved_input:
            mailbox_source = "local"
        else:
            # Browser-session Outlook is the credential-free default. Graph is
            # selected automatically only when both app-registration values exist.
            mailbox_source = "graph" if tenant_id and client_id else "owa"
        if mailbox_source == "local" and not resolved_input:
            raise ConfigurationError(
                "Local source needs --input or TRIAGE_INPUT pointing at JSONL/JSON/.eml files."
            )
        if mailbox_source == "graph":
            missing_graph = [
                name
                for name, value in (("MS_TENANT_ID", tenant_id), ("MS_CLIENT_ID", client_id))
                if not value
            ]
            if missing_graph:
                raise ConfigurationError(
                    "Microsoft Graph is not configured. For Outlook already open in Edge, "
                    "run with --owa (no admin app). To screen exported files, pass "
                    "--input path/to/messages.jsonl. To use Graph, set MS_TENANT_ID and "
                    "MS_CLIENT_ID."
                )

        apply_enabled = _bool("TRIAGE_APPLY") if apply_changes is None else apply_changes
        mark_read_enabled = _bool("TRIAGE_MARK_READ") if mark_read is None else mark_read
        agent_enabled = _bool("TRIAGE_AGENT", True) if use_agent is None else use_agent

        timeout = _positive_int("TRIAGE_REQUEST_TIMEOUT", 180)
        temperature = _float("TRIAGE_TEMPERATURE")
        requested_provider = (
            provider or _first_env("TRIAGE_PROVIDER", "TRIAGE_BACKEND") or "ollama"
        )
        try:
            screening_profile = provider_profile(requested_provider)
        except KeyError as exc:
            raise ConfigurationError(
                f"Unknown AI provider {requested_provider!r}. "
                f"Choose one of: {', '.join(PROVIDER_NAMES)}."
            ) from exc
        screening = _resolve_selection(
            screening_profile,
            prefix="",
            timeout=timeout,
            temperature=temperature,
            override_base_url=base_url,
            override_model=model,
        )
        _require_credentials(screening, "screening")

        requested_agent = (
            agent_provider
            or _first_env("TRIAGE_AGENT_PROVIDER")
            or screening_profile.name
        )
        try:
            agent_profile = provider_profile(requested_agent)
        except KeyError as exc:
            raise ConfigurationError(
                f"Unknown sorting-agent provider {requested_agent!r}. "
                f"Choose one of: {', '.join(PROVIDER_NAMES)}."
            ) from exc
        agent_selection = _resolve_selection(
            agent_profile,
            prefix="AGENT_",
            timeout=timeout,
            temperature=temperature,
            share_generic_env=agent_profile.name == screening_profile.name,
            override_model=agent_model,
        )
        if agent_enabled:
            if not agent_profile.supports_tools:
                raise ConfigurationError(
                    f"{agent_profile.label} cannot run the sorting agent. "
                    "Use --no-agent or choose a tool-calling provider."
                )
            _require_credentials(agent_selection, "the sorting agent")

        remote = [
            selection
            for selection in ((screening,) + ((agent_selection,) if agent_enabled else ()))
            if not selection.keeps_data_local
        ]
        if remote:
            approved = _bool("EXTERNAL_AI_APPROVED")
            if not approved:
                raise ConfigurationError(_approval_message(remote[0]))
        else:
            approved = False
        local_inference = not remote

        if apply_enabled and mailbox_source == "local":
            raise ConfigurationError(
                "--apply writes to the live Outlook mailbox and cannot be combined "
                "with --input. Use --owa --apply for Outlook in Edge, or drop --apply "
                "to preview a local file."
            )
        if apply_enabled and mailbox_source in {"desktop", "accessibility"}:
            raise ConfigurationError(
                "The macOS Outlook desktop adapter is read-only and cannot be combined "
                "with --apply. Use Graph or --owa for policy-approved mailbox writes."
            )
        if mark_read_enabled and mailbox_source in {"desktop", "accessibility"}:
            raise ConfigurationError(
                "The macOS Outlook desktop adapter cannot mark messages read."
            )
        owa_cdp_url = (
            os.getenv("EDGE_CDP_URL", "").strip()
            or os.getenv("OWA_CDP_URL", "").strip()
            or "http://127.0.0.1:9222"
        )
        if mailbox_source == "owa" and not _is_loopback_http_url(owa_cdp_url):
            raise ConfigurationError(
                "EDGE_CDP_URL must be an HTTP loopback endpoint with an explicit port"
            )

        return cls(
            tenant_id=tenant_id,
            client_id=client_id,
            screening=screening,
            agent=agent_selection,
            external_ai_approved=approved,
            intercept_clinical=not local_inference,
            mailbox_source=mailbox_source,
            input_path=Path(resolved_input).expanduser() if resolved_input else None,
            owa_cdp_url=owa_cdp_url.rstrip("/"),
            max_unread_messages=_positive_int("MAX_UNREAD_MESSAGES", 20),
            max_retrieval_pages=_positive_int("MAX_RETRIEVAL_PAGES", 10),
        max_body_characters=_positive_int("MAX_BODY_CHARACTERS", 12_000),
        output_dir=Path(os.getenv("TRIAGE_OUTPUT_DIR", "var")).expanduser(),
        feedback_path=(
            Path(os.environ["TRIAGE_FEEDBACK_FILE"]).expanduser()
            if os.getenv("TRIAGE_FEEDBACK_FILE", "").strip()
            else None
        ),
        agent_max_rounds=_positive_int("TRIAGE_AGENT_MAX_ROUNDS", 4),
            apply_changes=apply_enabled,
            mark_read=mark_read_enabled,
            use_agent=agent_enabled,
        )
