"""Provider registry and message routing for every supported AI system.

One triage run talks to exactly one screening provider and one sorting-agent
provider. Both are described by a :class:`ProviderProfile` and reached through a
:class:`ProviderClient`, so the rest of the package never encodes a vendor wire
format. Four wire dialects cover the ecosystem:

``ollama``            local Ollama ``/api/chat`` with JSON-schema output
``openai_chat``       OpenAI-compatible ``/chat/completions`` (OpenRouter, Groq,
                      LM Studio, OpenCode, Gemini's compatibility endpoint, ...)
``openai_responses``  OpenAI's own ``/responses`` endpoint
``anthropic``         Claude's ``/v1/messages`` with tool-shaped structured output

Neutral message and tool records are translated to the selected dialect on every
request, which keeps the policy gate in :mod:`email_triage.actions` the single
authority over what a model is allowed to do, whichever vendor answers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

OLLAMA = "ollama"
OPENAI_CHAT = "openai_chat"
OPENAI_RESPONSES = "openai_responses"
ANTHROPIC = "anthropic"


class ProviderError(RuntimeError):
    """Raised when a provider cannot be reached or answers unusably."""


@dataclass(frozen=True)
class ProviderProfile:
    """Everything the router needs to talk to one AI system."""

    name: str
    label: str
    api_style: str
    default_base_url: str
    default_model: str
    #: Environment variables searched, in order, for this provider's key.
    api_key_env: tuple[str, ...] = ()
    requires_api_key: bool = True
    #: True when inference runs on the operator's own machine or network.
    local: bool = False
    supports_tools: bool = True
    supports_json_schema: bool = True
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    extra_headers: tuple[tuple[str, str], ...] = ()
    notes: str = ""

    @property
    def is_openai_compatible(self) -> bool:
        return self.api_style in {OPENAI_CHAT, OPENAI_RESPONSES}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "api_style": self.api_style,
            "default_base_url": self.default_base_url,
            "default_model": self.default_model,
            "api_key_env": list(self.api_key_env),
            "requires_api_key": self.requires_api_key,
            "local": self.local,
            "supports_tools": self.supports_tools,
            "supports_json_schema": self.supports_json_schema,
            "notes": self.notes,
        }


PROVIDERS: dict[str, ProviderProfile] = {
    profile.name: profile
    for profile in (
        ProviderProfile(
            name="ollama",
            label="Ollama (local)",
            api_style=OLLAMA,
            default_base_url="http://127.0.0.1:11434",
            default_model="qwen3:8b",
            api_key_env=(),
            requires_api_key=False,
            local=True,
            notes="Runs entirely on this machine. No approval required on loopback.",
        ),
        ProviderProfile(
            name="lmstudio",
            label="LM Studio (local)",
            api_style=OPENAI_CHAT,
            default_base_url="http://127.0.0.1:1234/v1",
            default_model="local-model",
            api_key_env=("LMSTUDIO_API_KEY",),
            requires_api_key=False,
            local=True,
            notes="LM Studio's OpenAI-compatible local server.",
        ),
        ProviderProfile(
            name="llamacpp",
            label="llama.cpp server (local)",
            api_style=OPENAI_CHAT,
            default_base_url="http://127.0.0.1:8080/v1",
            default_model="local-model",
            requires_api_key=False,
            local=True,
            supports_json_schema=False,
            notes="llama-server --api-key-free OpenAI-compatible endpoint.",
        ),
        ProviderProfile(
            name="opencode",
            label="OpenCode (local server)",
            api_style=OPENAI_CHAT,
            default_base_url="http://127.0.0.1:4096/v1",
            default_model="opencode/default",
            api_key_env=("OPENCODE_API_KEY",),
            requires_api_key=False,
            local=True,
            supports_json_schema=False,
            notes="Start with `opencode serve`; point the base URL at its OpenAI-compatible route.",
        ),
        ProviderProfile(
            name="openai",
            label="OpenAI (ChatGPT models)",
            api_style=OPENAI_RESPONSES,
            default_base_url="https://api.openai.com/v1",
            default_model="gpt-4o",
            api_key_env=("OPENAI_API_KEY",),
            notes="Uses the Responses API for screening and chat completions for the agent.",
        ),
        ProviderProfile(
            name="anthropic",
            label="Anthropic (Claude)",
            api_style=ANTHROPIC,
            default_base_url="https://api.anthropic.com",
            default_model="claude-sonnet-4-5",
            api_key_env=("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
            auth_header="x-api-key",
            auth_prefix="",
            extra_headers=(("anthropic-version", "2023-06-01"),),
            notes="Structured screening is requested through a single forced tool call.",
        ),
        ProviderProfile(
            name="openrouter",
            label="OpenRouter (multi-vendor)",
            api_style=OPENAI_CHAT,
            default_base_url="https://openrouter.ai/api/v1",
            default_model="anthropic/claude-sonnet-4.5",
            api_key_env=("OPENROUTER_API_KEY",),
            extra_headers=(("X-Title", "Mail Triage"),),
            notes="One key, many upstream models. Model ids are vendor-prefixed.",
        ),
        ProviderProfile(
            name="azure-openai",
            label="Azure OpenAI",
            api_style=OPENAI_CHAT,
            default_base_url="",
            default_model="gpt-4o",
            api_key_env=("AZURE_OPENAI_API_KEY",),
            auth_header="api-key",
            auth_prefix="",
            notes=(
                "Set the base URL to "
                "https://<resource>.openai.azure.com/openai/deployments/<deployment>"
                "?api-version=2024-10-21 style routes."
            ),
        ),
        ProviderProfile(
            name="gemini",
            label="Google Gemini",
            api_style=OPENAI_CHAT,
            default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            default_model="gemini-2.5-flash",
            api_key_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            notes="Google's OpenAI-compatible compatibility layer.",
        ),
        ProviderProfile(
            name="groq",
            label="Groq",
            api_style=OPENAI_CHAT,
            default_base_url="https://api.groq.com/openai/v1",
            default_model="llama-3.3-70b-versatile",
            api_key_env=("GROQ_API_KEY",),
        ),
        ProviderProfile(
            name="mistral",
            label="Mistral",
            api_style=OPENAI_CHAT,
            default_base_url="https://api.mistral.ai/v1",
            default_model="mistral-large-latest",
            api_key_env=("MISTRAL_API_KEY",),
            supports_json_schema=False,
        ),
        ProviderProfile(
            name="deepseek",
            label="DeepSeek",
            api_style=OPENAI_CHAT,
            default_base_url="https://api.deepseek.com/v1",
            default_model="deepseek-chat",
            api_key_env=("DEEPSEEK_API_KEY",),
            supports_json_schema=False,
        ),
        ProviderProfile(
            name="together",
            label="Together AI",
            api_style=OPENAI_CHAT,
            default_base_url="https://api.together.xyz/v1",
            default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
            api_key_env=("TOGETHER_API_KEY",),
        ),
        ProviderProfile(
            name="xai",
            label="xAI (Grok)",
            api_style=OPENAI_CHAT,
            default_base_url="https://api.x.ai/v1",
            default_model="grok-4",
            api_key_env=("XAI_API_KEY",),
        ),
        ProviderProfile(
            name="custom",
            label="Custom OpenAI-compatible endpoint",
            api_style=OPENAI_CHAT,
            default_base_url="",
            default_model="",
            api_key_env=("TRIAGE_API_KEY",),
            requires_api_key=False,
            supports_json_schema=False,
            notes="Any gateway that speaks /chat/completions. Set the base URL and model.",
        ),
    )
}

#: Friendly names people type, including the pre-registry TRIAGE_BACKEND values.
PROVIDER_ALIASES = {
    "chatgpt": "openai",
    "claude": "anthropic",
    "google": "gemini",
    "azure": "azure-openai",
    "openai-compatible": "custom",
    "local": "ollama",
}

PROVIDER_NAMES = tuple(PROVIDERS)


def resolve_provider_name(value: str) -> str:
    """Map a user-supplied provider name (or alias) onto a registry key."""

    key = value.strip().lower().replace("_", "-")
    key = PROVIDER_ALIASES.get(key, key)
    if key not in PROVIDERS:
        raise KeyError(value)
    return key


def provider_profile(name: str) -> ProviderProfile:
    return PROVIDERS[resolve_provider_name(name)]


def api_key_from_environment(
    profile: ProviderProfile, environment: Mapping[str, str] | None = None
) -> str:
    env = os.environ if environment is None else environment
    for variable in profile.api_key_env:
        value = env.get(variable, "").strip()
        if value:
            return value
    return ""


@dataclass(frozen=True)
class ProviderSelection:
    """A fully resolved provider binding for one role (screening or sorting)."""

    profile: ProviderProfile
    base_url: str
    model: str
    api_key: str = ""
    temperature: float | None = None
    timeout: int = 180

    @property
    def name(self) -> str:
        return self.profile.name

    @property
    def is_loopback(self) -> bool:
        return is_loopback_url(self.base_url)

    @property
    def keeps_data_local(self) -> bool:
        """True when message text never leaves this machine."""

        return self.profile.local and self.is_loopback

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.profile.name,
            "label": self.profile.label,
            "api_style": self.profile.api_style,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_present": bool(self.api_key),
            "keeps_data_local": self.keeps_data_local,
        }


def is_loopback_url(url: str) -> bool:
    """True when a base URL points at this machine."""

    if not url:
        return False
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.startswith("127.")


# ---------------------------------------------------------------------------
# Neutral conversation records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AssistantReply:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


def system_message(content: str) -> dict[str, Any]:
    return {"role": "system", "content": content}


def user_message(content: str) -> dict[str, Any]:
    return {"role": "user", "content": content}


def assistant_message(reply: AssistantReply) -> dict[str, Any]:
    return {"role": "assistant", "content": reply.content, "tool_calls": list(reply.tool_calls)}


def tool_message(call: ToolCall, content: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": content,
    }


def _coerce_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def parse_json_object(text: Any) -> dict[str, Any]:
    """Read a JSON object out of model output, tolerating surrounding prose."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty model output")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        _, _, stripped = stripped.partition("\n")
        stripped = stripped.strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end <= start:
            raise
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output is not a JSON object")
    return value


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


class ProviderClient:
    """Route neutral requests to one provider and normalise the answer."""

    def __init__(self, selection: ProviderSelection):
        self.selection = selection
        self.profile = selection.profile
        self.base_url = selection.base_url.rstrip("/")
        self.model = selection.model
        self.timeout = selection.timeout

    # -- transport ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update(dict(self.profile.extra_headers))
        if self.selection.api_key:
            headers[self.profile.auth_header] = (
                f"{self.profile.auth_prefix}{self.selection.api_key}"
            )
        return headers

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._url(path)
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise ProviderError(
                f"{self.profile.label} request failed ({exc.code}): {detail}"
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"{self.profile.label} is unreachable at {url}") from exc

    def _url(self, path: str) -> str:
        base = self.base_url
        if "?" in base:  # Azure-style routes carry an api-version query string.
            head, _, query = base.partition("?")
            return f"{head.rstrip('/')}{path}?{query}"
        return f"{base}{path}"

    # -- capabilities ------------------------------------------------------
    def available_models(self) -> list[str]:
        """Best-effort model listing; an empty list means "could not ask"."""

        return []

    def reachable(self) -> tuple[bool, str]:
        """Cheap readiness check that never sends message content."""

        if self.profile.requires_api_key and not self.selection.api_key:
            variables = " or ".join(self.profile.api_key_env) or "an API key"
            return False, f"{self.profile.label} needs {variables}."
        if not self.base_url:
            return False, f"{self.profile.label} needs a base URL."
        return True, f"{self.profile.label} is configured for {self.model}."

    # -- inference ---------------------------------------------------------
    def complete_json(
        self, instructions: str, payload: str, schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        raise NotImplementedError

    def chat(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolSpec]
    ) -> AssistantReply:
        raise NotImplementedError


class OllamaClient(ProviderClient):
    def available_models(self) -> list[str]:
        request = Request(f"{self.base_url}/api/tags", method="GET")
        try:
            with urlopen(request, timeout=5) as response:
                body = json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            return []
        return [
            item["name"]
            for item in body.get("models", [])
            if isinstance(item.get("name"), str) and item["name"]
        ]

    def reachable(self) -> tuple[bool, str]:
        models = self.available_models()
        if not models:
            return False, (
                f"Ollama is unavailable at {self.base_url} or has no models. "
                f"Start it with `ollama serve` and run `ollama pull {self.model}`."
            )
        return True, f"Ollama is ready with {len(models)} installed model(s)."

    def complete_json(
        self, instructions: str, payload: str, schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        body = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": schema,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": payload},
            ],
        }
        if self.selection.temperature is not None:
            body["options"] = {"temperature": self.selection.temperature}
        try:
            raw = self._post("/api/chat", body)
        except ProviderError as exc:
            if "(404)" in str(exc):
                raise ProviderError(
                    f"Ollama model {self.model!r} is not installed. "
                    f"Run: ollama pull {self.model}"
                ) from exc
            raise
        return parse_json_object(raw.get("message", {}).get("content"))

    def chat(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolSpec]
    ) -> AssistantReply:
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "think": False,
            "messages": [_ollama_message(message) for message in messages],
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        if self.selection.temperature is not None:
            body["options"] = {"temperature": self.selection.temperature}
        raw = self._post("/api/chat", body)
        message = raw.get("message")
        if not isinstance(message, dict):
            raise ProviderError("Ollama returned no message")
        calls: list[ToolCall] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function", {}) if isinstance(call, dict) else {}
            calls.append(
                ToolCall(
                    id=str(call.get("id") or f"call_{index}"),
                    name=str(function.get("name", "")),
                    arguments=_coerce_arguments(function.get("arguments")),
                )
            )
        return AssistantReply(str(message.get("content") or ""), tuple(calls))


def _ollama_message(message: Mapping[str, Any]) -> dict[str, Any]:
    role = message.get("role")
    if role == "tool":
        return {
            "role": "tool",
            "tool_name": message.get("name", ""),
            "content": message.get("content", ""),
        }
    rendered: dict[str, Any] = {"role": role, "content": message.get("content", "")}
    calls = message.get("tool_calls") or []
    if calls:
        rendered["tool_calls"] = [
            {"function": {"name": call.name, "arguments": call.arguments}} for call in calls
        ]
    return rendered


class OpenAIChatClient(ProviderClient):
    """OpenAI-compatible ``/chat/completions``, used by most hosted vendors."""

    def available_models(self) -> list[str]:
        request = Request(self._url("/models"), headers=self._headers(), method="GET")
        try:
            with urlopen(request, timeout=8) as response:
                body = json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            return []
        return [
            item["id"]
            for item in body.get("data", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]

    def complete_json(
        self, instructions: str, payload: str, schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": payload},
            ],
        }
        if self.profile.supports_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        else:
            body["response_format"] = {"type": "json_object"}
            body["messages"][0]["content"] = (
                f"{instructions}\n\nReturn a single JSON object matching this schema "
                f"exactly:\n{json.dumps(schema, ensure_ascii=False)}"
            )
        if self.selection.temperature is not None:
            body["temperature"] = self.selection.temperature
        raw = self._post("/chat/completions", body)
        return parse_json_object(_openai_choice(raw).get("content"))

    def chat(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolSpec]
    ) -> AssistantReply:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_openai_message(message) for message in messages],
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        if self.selection.temperature is not None:
            body["temperature"] = self.selection.temperature
        raw = self._post("/chat/completions", body)
        message = _openai_choice(raw)
        calls: list[ToolCall] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function", {}) if isinstance(call, dict) else {}
            calls.append(
                ToolCall(
                    id=str(call.get("id") or f"call_{index}"),
                    name=str(function.get("name", "")),
                    arguments=_coerce_arguments(function.get("arguments")),
                )
            )
        return AssistantReply(str(message.get("content") or ""), tuple(calls))


def _openai_choice(raw: Mapping[str, Any]) -> dict[str, Any]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError("provider returned no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ProviderError("provider returned no message")
    return message


def _openai_message(message: Mapping[str, Any]) -> dict[str, Any]:
    role = message.get("role")
    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.get("tool_call_id", ""),
            "content": message.get("content", ""),
        }
    rendered: dict[str, Any] = {"role": role, "content": message.get("content", "")}
    calls = message.get("tool_calls") or []
    if calls:
        rendered["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in calls
        ]
    return rendered


class OpenAIResponsesClient(OpenAIChatClient):
    """OpenAI's own endpoint: Responses for screening, chat completions for tools."""

    def complete_json(
        self, instructions: str, payload: str, schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "store": False,
            "instructions": instructions,
            "input": payload,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        if self.selection.temperature is not None:
            body["temperature"] = self.selection.temperature
        raw = self._post("/responses", body)
        return parse_json_object(_responses_output_text(raw))


def _responses_output_text(raw: Mapping[str, Any]) -> str:
    for item in raw.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
            if content.get("type") == "refusal":
                raise ProviderError("model refused the screening request")
    raise ProviderError("response did not contain output_text")


class AnthropicClient(ProviderClient):
    """Claude's Messages API. Structured output uses one forced tool call."""

    max_tokens = 2048

    def available_models(self) -> list[str]:
        request = Request(f"{self.base_url}/v1/models", headers=self._headers(), method="GET")
        try:
            with urlopen(request, timeout=8) as response:
                body = json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            return []
        return [
            item["id"]
            for item in body.get("data", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]

    def complete_json(
        self, instructions: str, payload: str, schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": instructions,
            "messages": [{"role": "user", "content": [{"type": "text", "text": payload}]}],
            "tools": [
                {
                    "name": schema_name,
                    "description": "Return the screening result for this email.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": schema_name},
        }
        if self.selection.temperature is not None:
            body["temperature"] = self.selection.temperature
        raw = self._post("/v1/messages", body)
        for block in raw.get("content", []):
            if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                return block["input"]
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                return parse_json_object(block["text"])
        raise ProviderError("Claude returned no structured screening result")

    def chat(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolSpec]
    ) -> AssistantReply:
        system, converted = _anthropic_messages(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": converted,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters or {"type": "object", "properties": {}},
                }
                for tool in tools
            ]
        if self.selection.temperature is not None:
            body["temperature"] = self.selection.temperature
        raw = self._post("/v1/messages", body)
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in raw.get("content", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or f"call_{len(calls)}"),
                        name=str(block.get("name", "")),
                        arguments=_coerce_arguments(block.get("input")),
                    )
                )
        return AssistantReply("".join(text_parts), tuple(calls))


def _anthropic_messages(
    messages: Sequence[Mapping[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Split out the system prompt and fold tool results into user turns."""

    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            system_parts.append(str(message.get("content", "")))
            continue
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id", ""),
                "content": str(message.get("content", "")),
            }
            if converted and converted[-1]["role"] == "user" and converted[-1].get("_tool_batch"):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block], "_tool_batch": True})
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            content = str(message.get("content", ""))
            if content:
                blocks.append({"type": "text", "text": content})
            for call in message.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            if not blocks:
                blocks.append({"type": "text", "text": "(no content)"})
            converted.append({"role": "assistant", "content": blocks})
            continue
        converted.append(
            {"role": "user", "content": [{"type": "text", "text": str(message.get("content", ""))}]}
        )
    for message in converted:
        message.pop("_tool_batch", None)
    return "\n\n".join(part for part in system_parts if part), converted


CLIENTS = {
    OLLAMA: OllamaClient,
    OPENAI_CHAT: OpenAIChatClient,
    OPENAI_RESPONSES: OpenAIResponsesClient,
    ANTHROPIC: AnthropicClient,
}


def build_client(selection: ProviderSelection) -> ProviderClient:
    return CLIENTS[selection.profile.api_style](selection)


def describe_providers() -> list[dict[str, Any]]:
    return [profile.to_dict() for profile in PROVIDERS.values()]


def resolve_model(client: ProviderClient, requested: str, preferred: Iterable[str] = ()) -> str:
    """Keep the requested model when the provider has it; otherwise pick a local fallback.

    Only Ollama-style providers publish a trustworthy installed-model list, so a
    hosted provider always keeps the requested id.
    """

    if not isinstance(client, OllamaClient):
        return requested
    installed = client.available_models()
    if not installed:
        return requested
    if requested in installed:
        return requested
    for name in preferred:
        if name in installed:
            return name
    base = requested.split(":")[0]
    for name in installed:
        if name.split(":")[0] == base:
            return name
    return installed[0]
