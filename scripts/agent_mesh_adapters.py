#!/usr/bin/env python3
"""Real provider adapters used by the autonomous Agent Mesh supervisor.

Adapters are deliberately data-driven and shell-free.  A provider may be a
local CLI, an HTTP JSON endpoint, an MCP stdio server, or a cooperative agent
which receives work through the durable queue.  The supervisor never invents
an answer when an adapter is unavailable or returns no output.
"""

from __future__ import annotations

import json
import ipaddress
import os
import re
import selectors
import shlex
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent_mesh_core import MeshError, MeshStore, Settings, json_value, redact_text, sanitize


MAX_PROVIDER_OUTPUT = 2 * 1024 * 1024
AUTH_STATUS_CACHE_SECONDS = 15.0
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

CLAUDE_WORKER_JSON_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["complete", "delegate"]},
            "summary": {"type": "string"},
            "files_changed": {"type": "array", "items": {"type": "string"}},
            "files_created": {"type": "array", "items": {"type": "string"}},
            "commands_executed": {"type": "array", "items": {"type": "string"}},
            "tests": {"type": "array"},
            "warnings": {"type": "array"},
            "errors": {"type": "array"},
            "handoff_notes": {"type": "array"},
            "subtasks": {"type": "array"},
            "join_policy": {"type": "string"},
            "idempotency_key": {"type": "string"},
        },
        "required": [
            "action",
            "summary",
            "files_changed",
            "files_created",
            "commands_executed",
            "tests",
            "warnings",
            "errors",
            "handoff_notes",
        ],
        "additionalProperties": True,
    },
    separators=(",", ":"),
)
CLAUDE_PLANNER_JSON_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "tasks": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["tasks"],
        "additionalProperties": True,
    },
    separators=(",", ":"),
)
CLAUDE_AUDIT_JSON_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "valid": {"type": "boolean"},
            "issues": {"type": "array"},
            "revision_instructions": {"type": "string"},
            "tests_to_run": {"type": "array"},
        },
        "required": ["valid", "issues", "revision_instructions", "tests_to_run"],
        "additionalProperties": True,
    },
    separators=(",", ":"),
)
KILO_AUTOMATION_MODEL = os.environ.get(
    "AGENT_MESH_KILO_MODEL", "kilo/nvidia/nemotron-3.5-lightning:free"
)


@dataclass(frozen=True)
class AdapterSpec:
    agent: str
    kind: str
    command: tuple[str, ...] = ()
    endpoint: str = ""
    tool: str = ""
    auth_env: str = ""
    model: str = ""
    timeout: float = 1800.0
    heartbeat_interval: float = 20.0
    max_concurrent_tasks: int = 1
    source: str = "builtin"
    capabilities: tuple[str, ...] = ()
    reason: str = ""

    @property
    def available(self) -> bool:
        if self.kind == "command":
            return bool(self.command) and bool(_executable(self.command[0]))
        if self.kind == "http":
            return bool(self.endpoint)
        if self.kind == "mcp":
            return bool(self.command) and bool(_executable(self.command[0])) and bool(self.tool)
        if self.kind == "ollama":
            return bool(self.endpoint and self.model)
        return False


@dataclass
class AdapterResult:
    agent: str
    kind: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    timed_out: bool = False
    duration_seconds: float = 0.0
    cancelled: bool = False

    @property
    def failure_code(self) -> str:
        return classify_adapter_failure(self)

    @property
    def ok(self) -> bool:
        return not self.failure_code


def _provider_error_messages(output: str) -> list[str]:
    """Read error envelopes, without searching quoted prompts or model text."""
    cleaned = _ANSI.sub("", _bounded(output)).strip()
    try:
        documents = [json.loads(cleaned)]
    except ValueError:
        documents = []
        for line in cleaned.splitlines():
            try:
                documents.append(json.loads(line))
            except ValueError:
                continue
    errors = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        if str(document.get("role") or "") in {"user", "system"}:
            continue
        # MCP wraps its tool result; ordinary response text is deliberately
        # not recursively parsed as an error envelope.
        if isinstance(document.get("result"), dict) and document["result"].get("isError") is True:
            document = document["result"]
        event = str(document.get("type") or "").lower()
        failed = (
            document.get("is_error") is True
            or document.get("isError") is True
            or event in {"error", "failed", "turn.failed", "response.failed", "session.error"}
            or str(document.get("subtype") or "").startswith("error_")
        )
        # A worker's task-level errors/quoted examples are not CLI failures.
        worker_result = "summary" in document or str(document.get("action") or "") in {"complete", "delegate"}
        failed = failed or (not worker_result and (
            bool(document.get("error")) or str(document.get("status") or "") in {"error", "failed"}
        ))
        if failed:
            errors.append(json.dumps({key: value for key, value in document.items()
                                      if key in {"error", "errors", "message", "code", "name", "type", "subtype", "result", "content"}},
                                     ensure_ascii=False))
    return errors


def _diagnostic_failure_code(message: str) -> str:
    message = message.lower()
    categories = (
        ("authentication", r"unauthenticated|authentication|not (?:logged|signed) in|invalid[ _-]?(?:api[ _-]?)?key|invalid[ _-]?token|credentials|unauthorized|\b401\b"),
        ("quota", r"quota|rate[ _-]?limit|resource[ _-]?exhausted|insufficient[ _-]?(?:credits|balance)|(?:no|out of) credits|credits? exhausted|\b429\b"),
        ("model_unavailable", r"ineligibletier|providermodelnotfound|model.{0,100}(?:not[ _-]?found|unavailable|not available|not supported|does not exist|not installed)|unknown model"),
        ("permission", r"permission|access denied|forbidden|\b403\b|\beacces\b|\beperm\b"),
        ("timeout", r"timed? out|timeout|\betimedout\b"),
        ("cancelled", r"cancelled|canceled|interrupted"),
        ("network", r"connection|network|\bdns\b|name resolution|could not resolve|failed to fetch|fetch failed|\beconn\w*|\benet\w*|\beai_again\b|service unavailable|\b50[234]\b"),
        ("unavailable", r"command not found|no such file|not executable|adapter unavailable|command is unavailable"),
    )
    for category, pattern in categories:
        if re.search(pattern, message):
            return category
    return "provider_error"


def classify_adapter_failure(result: AdapterResult) -> str:
    """Return a stable failure category, or an empty string for success."""
    if result.cancelled:
        return "cancelled"
    if result.timed_out or result.returncode == 124:
        return "timeout"
    messages = _provider_error_messages(result.stdout) + _provider_error_messages(result.stderr)
    if result.returncode:
        # Nonzero exits are independently known failures, so their plain
        # diagnostic text can be classified without mistaking a valid answer.
        diagnostic = "\n".join(messages) or result.stderr or result.stdout
        code = _diagnostic_failure_code(_bounded(diagnostic))
        if code == "provider_error" and result.returncode in {126, 127}:
            return "unavailable"
        if code == "provider_error" and result.returncode in {-signal.SIGINT, 130}:
            return "cancelled"
        return code
    if messages:
        return _diagnostic_failure_code("\n".join(messages))
    # Some CLIs print a diagnostic and exit zero. Restrict this fallback to
    # standalone diagnostic lines, never arbitrary JSON fields or quotations.
    for output in (result.stdout, result.stderr):
        cleaned = _ANSI.sub("", _bounded(output)).strip()
        if cleaned.startswith(("{", "[", '"', "```", ">")):
            continue
        if re.match(r"(?i)^(?:(?:api )?error\s*:|fatal\s*:|[A-Za-z]*Error\s*:|authentication required\b|not logged in\b|unauthorized\b)", cleaned):
            return _diagnostic_failure_code(cleaned.splitlines()[0])
    return "" if result.stdout.strip() else "empty_output"


DEFAULT_PROFILE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "Gemini": (
        "analysis",
        "research",
        "web",
        "coding",
        "frontend",
        "backend",
        "testing",
        "security",
        "task_execution",
        "orchestration",
    ),
    "OpenCode": (
        "analysis",
        "coding",
        "frontend",
        "backend",
        "filesystem",
        "shell",
        "testing",
        "task_execution",
        "orchestration",
    ),
    "Claude-FCC": (
        "analysis",
        "research",
        "coding",
        "frontend",
        "backend",
        "testing",
        "security",
        "task_execution",
        "orchestration",
    ),
    "Friday": (
        "analysis",
        "research",
        "local_private",
        "task_execution",
        "orchestration",
    ),
    "Friday-Fast": (
        "analysis",
        "research",
        "local_private",
        "task_execution",
    ),
    "Friday-Pro": (
        "analysis",
        "research",
        "coding",
        "testing",
        "local_private",
        "task_execution",
        "orchestration",
    ),
    "Codex": (
        "analysis",
        "coding",
        "frontend",
        "backend",
        "filesystem",
        "shell",
        "testing",
        "security",
        "task_execution",
        "orchestration",
    ),
    "Cursor": (
        "analysis",
        "coding",
        "frontend",
        "backend",
        "filesystem",
        "shell",
        "testing",
        "task_execution",
        "orchestration",
    ),
    "Kilo": (
        "analysis",
        "coding",
        "frontend",
        "backend",
        "filesystem",
        "shell",
        "testing",
        "task_execution",
        "orchestration",
    ),
    "Kiro": (
        "analysis",
        "coding",
        "frontend",
        "backend",
        "filesystem",
        "shell",
        "testing",
        "task_execution",
        "orchestration",
    ),
    "LocalLLM": (
        "analysis",
        "coding",
        "testing",
        "local_private",
        "task_execution",
        "orchestration",
    ),
}

BUILTIN_AGENT_PROFILES: tuple[tuple[str, str, str], ...] = (
    ("Gemini", "Google", "worker"),
    ("Codex", "OpenAI", "worker"),
    ("Cursor", "Cursor", "worker"),
    ("OpenCode", "OpenCode", "worker"),
    ("Kilo", "Kilo Code", "worker"),
    ("Kiro", "Kiro", "worker"),
    ("Claude-FCC", "Anthropic", "worker"),
    ("Friday", "Local", "worker"),
    ("Friday-Fast", "Local", "worker"),
    ("Friday-Pro", "Local", "worker"),
    ("LocalLLM", "Ollama", "worker"),
)


def _executable(*candidates: str) -> str:
    for candidate in candidates:
        if "/" in candidate:
            path = Path(candidate).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return ""


def _extension_executable(prefix: str, filename: str) -> str:
    """Find versioned IDE-bundled CLIs without relying on PATH."""
    root = Path.home() / ".antigravity-ide" / "extensions"
    for path in sorted(root.glob(prefix + "*/bin/**/" + filename)):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return ""


def _secret_configured(name: str) -> bool:
    """Return whether a non-placeholder secret reference is available."""
    value = os.environ.get(name, "").strip()
    return bool(value) and value.lower() not in {
        "your_api_key_here",
        "your_cursor_api_key_here",
        "your_kiro_api_key_here",
        "your_gemini_api_key_here",
    }


_SAFE_PROVIDER_ENV = {
    "HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TMPDIR",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "NO_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy",
    "AGENT_MESH_BASE_URL", "AGENT_MESH_TOKEN", "AGENT_MESH_PORT",
    "LOCAL_LLM_MODEL", "LOCAL_LLM_OLLAMA_ENDPOINT", "GEMINI_API_KEY",
    "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "CURSOR_API_KEY",
    "KIRO_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "KILO_API_KEY",
}


def _headless_environment(extra: tuple[str, ...] = ()) -> dict[str, str]:
    """Preserve only service/provider settings needed by a headless child."""
    allowed = _SAFE_PROVIDER_ENV | {str(name) for name in extra if name}
    environment = {
        name: value for name, value in os.environ.items()
        if name in allowed
    }
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "SWAYSOCK"):
        environment.pop(name, None)
    environment["NO_OPEN_BROWSER"] = "1"
    return environment


def _cli_reports_authenticated(command: tuple[str, ...]) -> bool:
    """Check a provider's local login state without opening a browser.

    The supervisor runs without a terminal.  Authentication commands must
    therefore be status-only probes: they may inspect the provider's cached
    session, but they must never start an interactive login flow or expose its
    output in the mesh logs.
    """
    environment = _headless_environment()
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1.5,
            env=environment,
            start_new_session=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    lowered = output.lower()
    unauthenticated_markers = (
        "not logged in",
        "not logged-in",
        "not authenticated",
        "unauthenticated",
        "not signed in",
        "no active session",
        "authentication required",
    )
    return result.returncode == 0 and not any(
        marker in lowered for marker in unauthenticated_markers
    )


def _secret_tool_credential_present(service: str, account: str) -> bool:
    """Check for a keyring item without ever emitting its secret value."""
    executable = _executable("secret-tool")
    if not executable:
        return False
    try:
        result = subprocess.run(
            (executable, "lookup", "service", service, "account", account),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            env=_headless_environment(),
            start_new_session=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _gemini_cached_auth_configured() -> bool:
    """Detect a cached Gemini CLI login without reading credential contents.

    Current Gemini CLI releases keep OAuth tokens in the desktop keychain (or
    an encrypted file) and keep only the active account marker in
    ``google_accounts.json``.  The marker alone is not sufficient: a keyring
    lookup or credential file must also exist, otherwise a stale account
    marker would make the supervisor launch an unauthenticated process.
    """
    base = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"
    for filename in ("oauth_creds.json", "gemini-credentials.json"):
        try:
            if (base / filename).is_file() and (base / filename).stat().st_size > 0:
                return True
        except OSError:
            continue
    accounts = base / "google_accounts.json"
    try:
        data = json.loads(accounts.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    active = data.get("active") if isinstance(data, dict) else None
    return (
        isinstance(active, str)
        and bool(active.strip())
        and _secret_tool_credential_present("gemini-cli-oauth", "main-account")
    )


def _gemini_headless_auth_configured() -> bool:
    """Require a non-interactive Gemini authentication path."""
    if (
        _secret_configured("GEMINI_API_KEY")
        or _secret_configured("GOOGLE_API_KEY")
        or _gemini_cached_auth_configured()
    ):
        return True
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if credentials and Path(credentials).expanduser().is_file():
        return True
    return bool(
        os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        and os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip()
    )


def _agent_metadata(agent: dict[str, Any]) -> dict[str, Any]:
    value = agent.get("metadata")
    if isinstance(value, dict):
        return value
    value = json_value(agent.get("metadata_json"), {})
    return value if isinstance(value, dict) else {}


def _capability_names(agent: dict[str, Any]) -> tuple[str, ...]:
    value = agent.get("capabilities")
    if value is None:
        value = json_value(agent.get("capabilities_json"), {})
    names: set[str] = set()
    if isinstance(value, dict):
        names.update(str(key) for key, enabled in value.items() if enabled is not False)
    elif isinstance(value, list):
        names.update(str(item) for item in value)
    return tuple(sorted(names))


def _command_tokens(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            return tuple(shlex.split(value))
        except ValueError as exc:
            raise MeshError("adapter command is not valid shell-style tokenization") from exc
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    return ()


def _provider_endpoint_allowed(endpoint: str) -> tuple[bool, str]:
    """Keep registered HTTP adapters on an explicit, non-SSRF allowlist."""
    try:
        parsed = urllib.parse.urlsplit(endpoint)
    except ValueError:
        return False, "provider endpoint is invalid"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "provider endpoint must use http or https"
    host = parsed.hostname.lower().rstrip(".")
    configured = {
        item.strip().lower().rstrip(".")
        for item in os.environ.get("AGENT_MESH_ALLOWED_PROVIDER_HOSTS", "").split(",")
        if item.strip()
    }
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if host not in local_hosts and host not in configured:
        return False, "provider endpoint host is not allowlisted"
    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        }
    except OSError:
        return False, "provider endpoint host could not be resolved"
    for address in addresses:
        try:
            value = ipaddress.ip_address(address)
        except ValueError:
            continue
        if host not in local_hosts and (value.is_private or value.is_loopback or value.is_link_local or value.is_reserved):
            return False, "provider endpoint resolves to a private address"
    return True, "provider endpoint is allowlisted"


class AdapterRegistry:
    """Resolve configured and known-local providers without per-agent setup."""

    def __init__(self, store: MeshStore, settings: Settings):
        self.store = store
        self.settings = settings
        self._specs: dict[str, AdapterSpec] = {}
        self._auth_status_cache: dict[str, tuple[float, bool]] = {}

    def _cached_cli_auth(self, cache_key: str, command: tuple[str, ...]) -> bool:
        """Avoid making provider status checks stall the supervisor loop."""
        now = time.monotonic()
        cached = self._auth_status_cache.get(cache_key)
        if cached and now - cached[0] < AUTH_STATUS_CACHE_SECONDS:
            return cached[1]
        authenticated = _cli_reports_authenticated(command)
        self._auth_status_cache[cache_key] = (now, authenticated)
        return authenticated

    def _cached_gemini_auth(self) -> bool:
        """Cache keyring probes while allowing login state to converge."""
        now = time.monotonic()
        cached = self._auth_status_cache.get("gemini:auth")
        if cached and now - cached[0] < AUTH_STATUS_CACHE_SECONDS:
            return cached[1]
        configured = _gemini_headless_auth_configured()
        self._auth_status_cache["gemini:auth"] = (now, configured)
        return configured

    def refresh(self) -> dict[str, AdapterSpec]:
        specs: dict[str, AdapterSpec] = {}
        for agent in self.store.list_agents():
            try:
                spec = self._resolve(agent)
            except (MeshError, TypeError, ValueError) as exc:
                # A malformed optional adapter must not hide every other
                # registered agent from the supervisor.
                spec = AdapterSpec(
                    agent=str(agent.get("name") or "unknown"),
                    kind="cooperative",
                    model=str(agent.get("model") or ""),
                    source="invalid",
                    capabilities=_capability_names(agent),
                    reason="invalid adapter configuration: " + redact_text(str(exc)),
                )
            if spec is not None:
                specs[spec.agent] = spec
        self._specs = specs
        self._sync_existing_builtin_states(specs)
        return dict(specs)

    def _sync_existing_builtin_states(self, specs: dict[str, AdapterSpec]) -> None:
        """Keep persisted readiness aligned with real adapter discovery."""
        agents = {
            str(agent.get("name")): agent for agent in self.store.list_agents()
        }
        for name, spec in specs.items():
            agent = agents.get(name)
            if spec is None or agent is None:
                continue
            metadata = agent.get("metadata")
            if not isinstance(metadata, dict):
                metadata = json_value(agent.get("metadata_json"), {})
            if not isinstance(metadata, dict):
                metadata = {}
            autonomy = metadata.get("autonomy")
            if not isinstance(autonomy, dict):
                autonomy = {}
            current_capabilities = agent.get("capabilities")
            if not isinstance(current_capabilities, dict):
                current_capabilities = json_value(agent.get("capabilities_json"), {})
            if not isinstance(current_capabilities, dict):
                current_capabilities = {}
            needs_update = (
                autonomy.get("available") is not bool(spec.available)
                or str(autonomy.get("adapter_kind") or "") != spec.kind
                or str(autonomy.get("adapter_source") or "") != spec.source
                or any(capability not in current_capabilities for capability in spec.capabilities)
                or (spec.available and current_capabilities.get("autonomous_worker") is not True)
                or str(agent.get("endpoint") or "") != str(spec.endpoint or "")
                or str(agent.get("model") or "") != str(spec.model or "")
            )
            if needs_update:
                self.store.update_agent_adapter_state(
                    name,
                    available=spec.available,
                    adapter_kind=spec.kind,
                    adapter_source=spec.source,
                    capabilities=spec.capabilities,
                    endpoint=spec.endpoint,
                    model=spec.model,
                )

    def ensure_missing_builtin_registrations(self) -> None:
        """Add a newly installed builtin without rewriting existing rows."""
        if not self._specs:
            self.refresh()
        known = {str(agent["name"]) for agent in self.store.list_agents()}
        added = False
        for name, provider, agent_type in BUILTIN_AGENT_PROFILES:
            if name in known:
                continue
            spec = self._builtin(
                name,
                provider,
                {
                    "name": name,
                    "provider": provider,
                    "type": agent_type,
                    "capabilities": {
                        capability: True
                        for capability in DEFAULT_PROFILE_CAPABILITIES.get(name, ())
                    },
                },
            )
            if not spec.available:
                continue
            self.store.register_agent(
                {
                    "name": name,
                    "provider": provider,
                    "type": agent_type,
                    "capabilities": {capability: True for capability in spec.capabilities},
                    "status": "active",
                    "health": "online",
                    "metadata": {
                        "autonomy": {
                            "adapter_kind": spec.kind,
                            "adapter_source": spec.source,
                            "available": True,
                        }
                    },
                    "max_concurrent_tasks": spec.max_concurrent_tasks,
                }
            )
            known.add(name)
            added = True
        if added:
            self.refresh()

    def get(self, agent: str) -> AdapterSpec | None:
        if agent not in self._specs:
            self.refresh()
        return self._specs.get(agent)

    def available(self, kind: str | None = None) -> list[AdapterSpec]:
        if not self._specs:
            self.refresh()
        return [
            spec
            for spec in self._specs.values()
            if spec.available and (kind is None or spec.kind == kind)
        ]

    def inventory(self) -> list[dict[str, Any]]:
        if not self._specs:
            self.refresh()
        return [
            {
                "agent": spec.agent,
                "kind": spec.kind,
                "available": spec.available,
                "source": spec.source,
                "model": spec.model,
                "endpoint": _public_endpoint(spec.endpoint),
                "tool": spec.tool,
                "capabilities": list(spec.capabilities),
                "max_concurrent_tasks": spec.max_concurrent_tasks,
                "reason": spec.reason,
            }
            for spec in sorted(self._specs.values(), key=lambda item: item.agent)
        ]

    def preflight(self, spec: AdapterSpec) -> tuple[bool, str]:
        """Validate the selected invocation path immediately before dispatch."""
        if spec.kind == "command":
            if spec.command and _executable(spec.command[0]):
                return True, "command is executable"
            return False, "configured command is unavailable"
        if spec.kind == "mcp":
            if not spec.command or not spec.tool:
                return False, "MCP adapter command/tool is incomplete"
            if not _executable(spec.command[0]):
                return False, "MCP adapter command is unavailable"
            return True, "MCP command is executable"
        if spec.kind == "ollama":
            allowed, reason = _provider_endpoint_allowed(spec.endpoint)
            if not allowed:
                return False, reason
            return (
                (True, "Ollama model is installed")
                if _ollama_model_available(spec.endpoint, spec.model)
                else (False, "Ollama model is unavailable")
            )
        if spec.kind == "http":
            allowed, reason = _provider_endpoint_allowed(spec.endpoint)
            if not allowed:
                return False, reason
            try:
                parsed = urllib.parse.urlsplit(spec.endpoint)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    return False, "HTTP adapter endpoint is invalid"
                request = urllib.request.Request(spec.endpoint, method="HEAD")
                with urllib.request.urlopen(request, timeout=3.0):
                    return True, "HTTP endpoint responded"
            except urllib.error.HTTPError as exc:
                # Auth-required and method-not-allowed responses prove that
                # the endpoint is reachable; invocation will still carry the
                # configured auth reference and handle the real response.
                if exc.code in {400, 401, 403, 405, 501}:
                    return True, "HTTP endpoint is reachable"
                return False, f"HTTP endpoint returned {exc.code}"
            except (OSError, urllib.error.URLError, ValueError) as exc:
                return False, "HTTP endpoint preflight failed: " + redact_text(str(exc))
        return False, "adapter is cooperative"

    def ensure_builtin_registrations(self) -> None:
        """Make known local CLI workers selectable while preserving user data."""
        if not self._specs:
            self.refresh()
        known = {str(agent["name"]) for agent in self.store.list_agents()}
        for name, provider, agent_type in BUILTIN_AGENT_PROFILES:
            if name in known:
                continue
            spec = self._builtin(
                name,
                provider,
                {
                    "name": name,
                    "provider": provider,
                    "type": agent_type,
                    "capabilities": {
                        capability: True
                        for capability in DEFAULT_PROFILE_CAPABILITIES.get(name, ())
                    },
                },
            )
            if not spec.available:
                continue
            self.store.register_agent(
                {
                    "name": name,
                    "provider": provider,
                    "type": agent_type,
                    "capabilities": {
                        capability: True for capability in spec.capabilities
                    },
                    "status": "active",
                    "health": "online",
                    "metadata": {
                        "autonomy": {
                            "adapter_kind": spec.kind,
                            "adapter_source": spec.source,
                            "available": True,
                        }
                    },
                    "max_concurrent_tasks": spec.max_concurrent_tasks,
                }
            )
            known.add(name)
        self.refresh()
        for agent in self.store.list_agents():
            name = str(agent["name"])
            spec = self._specs.get(name)
            if spec is None or not spec.available:
                continue
            current = agent.get("capabilities")
            if not isinstance(current, dict):
                current = {str(item): True for item in _capability_names(agent)}
            capabilities = dict(current)
            for capability in spec.capabilities:
                capabilities.setdefault(capability, True)
            capabilities["autonomous_worker"] = True
            metadata = _agent_metadata(agent)
            autonomy = metadata.get("autonomy")
            if not isinstance(autonomy, dict):
                autonomy = {}
            autonomy.update(
                {
                    "adapter_kind": spec.kind,
                    "adapter_source": spec.source,
                    "available": True,
                }
            )
            metadata["autonomy"] = autonomy
            self.store.register_agent(
                {
                    "name": name,
                    "provider": agent.get("provider") or "",
                    "model": agent.get("model") or spec.model,
                    "type": agent.get("type") or "worker",
                    "capabilities": capabilities,
                    "limitations": agent.get("limitations") or "",
                    "status": "active",
                    "health": "online",
                    "endpoint": agent.get("endpoint") or spec.endpoint,
                    "max_concurrent_tasks": agent.get("max_concurrent_tasks") or 1,
                    "heartbeat_interval_seconds": agent.get("heartbeat_interval_seconds") or 30,
                    "metadata": metadata,
                }
            )
        # Keep Kiro registered even when its CLI is not installed or
        # authenticated so routing can queue it truthfully until a real CLI
        # login or MCP heartbeat is present.
        if "Kiro" not in known:
            self.store.register_agent(
                {
                    "name": "Kiro",
                    "provider": "Kiro",
                    "type": "cooperative",
                    "capabilities": {
                        "mcp": True,
                        "orchestration": True,
                        "task_execution": True,
                    },
                    "limitations": "cooperative GUI client; requires a live MCP heartbeat",
                    "status": "offline",
                    "health": "offline",
                    "metadata": {
                        "autonomy": {
                            "adapter_kind": "cooperative",
                            "adapter_source": "registered-config",
                            "available": False,
                        }
                    },
                }
            )
            known.add("Kiro")
        self.refresh()

    def _resolve(self, agent: dict[str, Any]) -> AdapterSpec | None:
        name = str(agent.get("name") or "")
        if not name:
            return None
        metadata = _agent_metadata(agent)
        custom = metadata.get("autonomy_adapter") or metadata.get("adapter")
        if custom is not None:
            return self._custom(name, custom, agent)
        return self._builtin(name, str(agent.get("provider") or ""), agent)

    def _custom(self, name: str, config: Any, agent: dict[str, Any]) -> AdapterSpec:
        if not isinstance(config, dict):
            raise MeshError(f"adapter configuration for {name} must be an object")
        kind = str(config.get("kind") or config.get("type") or "cooperative").lower()
        capabilities = tuple(sorted(set(_capability_names(agent)) | set(str(item) for item in config.get("capabilities", []))))
        common = {
            "agent": name,
            "kind": kind,
            "model": str(config.get("model") or agent.get("model") or ""),
            "auth_env": str(config.get("auth_env") or ""),
            "timeout": _positive_number(config.get("timeout"), self.settings.autonomy_command_timeout),
            "heartbeat_interval": _positive_number(config.get("heartbeat_interval"), 20.0),
            "max_concurrent_tasks": _positive_int(
                config.get("max_concurrent_tasks"), agent.get("max_concurrent_tasks") or 1
            ),
            "source": "registered",
            "capabilities": capabilities,
        }
        if kind in {"command", "mcp"}:
            command = _command_tokens(config.get("argv") or config.get("command"))
            if not command:
                return AdapterSpec(**common, reason="registered adapter has no command")
            args = _command_tokens(config.get("args"))
            tool = str(config.get("tool") or "")
            if kind == "mcp" and not tool:
                return AdapterSpec(**common, command=command + args, reason="registered MCP adapter has no tool")
            return AdapterSpec(**common, command=command + args, tool=tool)
        if kind in {"http", "ollama"}:
            endpoint = str(config.get("endpoint") or agent.get("endpoint") or "")
            allowed, reason = _provider_endpoint_allowed(endpoint)
            if not allowed:
                return AdapterSpec(**common, endpoint=endpoint, reason=reason)
            auth_env = common["auth_env"]
            allowed_auth = {
                item.strip()
                for item in os.environ.get("AGENT_MESH_ALLOWED_PROVIDER_AUTH_ENVS", "").split(",")
                if item.strip()
            }
            if auth_env and auth_env not in allowed_auth:
                return AdapterSpec(**common, endpoint=endpoint, auth_env="", reason="provider auth reference is not allowlisted")
            return AdapterSpec(
                **common,
                endpoint=endpoint,
                tool=str(config.get("tool") or ""),
            )
        return AdapterSpec(**common, reason="cooperative agent; waiting for its MCP worker loop")

    def _builtin(self, name: str, provider: str, agent: dict[str, Any]) -> AdapterSpec:
        lower = (name + " " + provider).lower()
        capabilities = DEFAULT_PROFILE_CAPABILITIES.get(name, _capability_names(agent))
        common = {
            "agent": name,
            "timeout": self.settings.autonomy_command_timeout,
            "heartbeat_interval": 20.0,
            "max_concurrent_tasks": _positive_int(agent.get("max_concurrent_tasks"), 1),
            "source": "builtin",
            "capabilities": tuple(sorted(set(capabilities) | set(_capability_names(agent)))),
        }
        if name == "LocalLLM":
            model = str(
                agent.get("model")
                or os.environ.get("LOCAL_LLM_MODEL")
                or "qwen2.5-coder:7b-instruct-q4_K_M"
            )
            endpoint = str(
                agent.get("endpoint")
                or os.environ.get("LOCAL_LLM_OLLAMA_ENDPOINT")
                or "http://127.0.0.1:11434/api/chat"
            )
            if _ollama_model_available(endpoint, model):
                return AdapterSpec(
                    **common,
                    kind="ollama",
                    endpoint=endpoint,
                    model=model,
                    reason="Ollama local coder",
                )
            return AdapterSpec(
                **common,
                kind="cooperative",
                model=model,
                endpoint=endpoint,
                reason=f"Ollama model {model} is unavailable",
            )
        if name == "Gemini":
            executable = _executable("gemini")
            if executable and self._cached_gemini_auth():
                approval = os.environ.get("AGENT_MESH_GEMINI_APPROVAL_MODE", "yolo")
                return AdapterSpec(
                    **common,
                    kind="command",
                    command=(
                        executable,
                        "--prompt",
                        "{prompt}",
                        "--output-format",
                        "json",
                        "--approval-mode",
                        approval,
                        "--skip-trust",
                    ),
                    model=str(agent.get("model") or ""),
                )
            reason = (
                "gemini CLI is unavailable"
                if not executable
                else "Gemini CLI requires a cached Google login, GEMINI_API_KEY, GOOGLE_API_KEY, or Vertex AI credentials for unattended execution"
            )
            return AdapterSpec(**common, kind="cooperative", reason=reason)
        if name == "Codex":
            executable = _executable("codex") or _extension_executable(
                "openai.chatgpt", "codex"
            )
            if executable:
                return AdapterSpec(
                    **common,
                    kind="command",
                    command=(
                        executable,
                        "exec",
                        "--json",
                        "--ephemeral",
                        "--approve-for-me",
                        "--skip-git-repo-check",
                        "--cd",
                        "{workspace}",
                        "{prompt}",
                    ),
                    model=str(agent.get("model") or ""),
                )
            return AdapterSpec(**common, kind="cooperative", reason="codex CLI is unavailable")
        if name == "Cursor":
            executable = _executable(
                "cursor-agent", str(Path.home() / ".local/bin/cursor-agent")
            )
            api_key = _secret_configured("CURSOR_API_KEY")
            browser_login = bool(executable) and self._cached_cli_auth(
                "cursor:" + executable,
                (executable, "status"),
            )
            if executable and (api_key or browser_login):
                return AdapterSpec(
                    **common,
                    kind="command",
                    command=(
                        executable,
                        "--print",
                        "--output-format",
                        "json",
                        "--force",
                        "--trust",
                        "--approve-mcps",
                        "--workspace",
                        "{workspace}",
                        "{prompt}",
                    ),
                    auth_env="CURSOR_API_KEY" if api_key else "",
                    model=str(agent.get("model") or ""),
                )
            reason = (
                "Cursor CLI is unavailable"
                if not executable
                else "Cursor CLI requires CURSOR_API_KEY or a completed cursor-agent login for unattended execution"
            )
            return AdapterSpec(**common, kind="cooperative", reason=reason)
        if name == "OpenCode":
            executable = _executable("opencode", str(Path.home() / ".opencode/bin/opencode"))
            if executable:
                return AdapterSpec(
                    **common,
                    kind="command",
                    command=(
                        executable,
                        "run",
                        "--pure",
                        "--format",
                        "json",
                        "--auto",
                        "--dir",
                        "{workspace}",
                        "{prompt}",
                    ),
                    model=str(agent.get("model") or ""),
                )
            return AdapterSpec(**common, kind="cooperative", reason="opencode CLI is unavailable")
        if name == "Kilo":
            executable = _executable("kilo") or _extension_executable(
                "kilocode.kilo-code", "kilo"
            )
            if executable:
                return AdapterSpec(
                    **common,
                    kind="command",
                    command=(
                        executable,
                        "run",
                        "--pure",
                        "--format",
                        "json",
                        "--auto",
                        "--model",
                        KILO_AUTOMATION_MODEL,
                        "--dir",
                        "{workspace}",
                        "{prompt}",
                    ),
                    model=str(agent.get("model") or ""),
                )
            return AdapterSpec(**common, kind="cooperative", reason="kilo CLI is unavailable")
        if name == "Kiro":
            executable = _executable(
                "kiro-cli", str(Path.home() / ".local/bin/kiro-cli")
            )
            api_key = _secret_configured("KIRO_API_KEY")
            browser_login = bool(executable) and self._cached_cli_auth(
                "kiro:" + executable,
                (executable, "whoami"),
            )
            if executable and (api_key or browser_login):
                return AdapterSpec(
                    **common,
                    kind="command",
                    command=(
                        executable,
                        "chat",
                        "--no-interactive",
                        "--trust-all-tools",
                        "--output-format",
                        "stream-json",
                        "{prompt}",
                    ),
                    auth_env="KIRO_API_KEY" if api_key else "",
                    model=str(agent.get("model") or ""),
                )
            reason = (
                "Kiro CLI is unavailable"
                if not executable
                else "Kiro CLI requires KIRO_API_KEY or a completed kiro-cli login for unattended execution"
            )
            return AdapterSpec(**common, kind="cooperative", reason=reason)
        if "claude" in lower or name == "Claude-FCC":
            executable = _executable("fcc-claude", "claude")
            if executable:
                return AdapterSpec(
                    **common,
                    kind="command",
                    command=(
                        executable,
                        "-p",
                        "{prompt}",
                        "--output-format",
                        "json",
                        "--json-schema",
                        CLAUDE_WORKER_JSON_SCHEMA,
                        "--dangerously-skip-permissions",
                        "--no-session-persistence",
                    ),
                    model=str(agent.get("model") or ""),
                )
            return AdapterSpec(**common, kind="cooperative", reason="Claude CLI is unavailable")
        if name.startswith("Friday"):
            executable = _executable(str(Path.home() / ".local/bin/friday"))
            if executable:
                return AdapterSpec(
                    **common,
                    kind="command",
                    command=(executable, "pro", "--ask", "{prompt}"),
                    model=str(agent.get("model") or "local"),
                )
            return AdapterSpec(**common, kind="cooperative", reason="FRIDAY launcher is unavailable")
        endpoint = str(agent.get("endpoint") or "")
        if endpoint:
            return AdapterSpec(
                **common,
                kind="http",
                endpoint=endpoint,
                model=str(agent.get("model") or ""),
                reason="registered endpoint",
            )
        return AdapterSpec(
            **common,
            kind="cooperative",
            reason="agent has no invokable local adapter; durable MCP worker path remains available",
        )

    def invoke(
        self,
        spec: AdapterSpec,
        *,
        prompt: str,
        payload: dict[str, Any],
        workspace: Path,
        heartbeat: Callable[[], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        task_token: str = "",
        caller_agent: str = "",
    ) -> AdapterResult:
        if not spec.available:
            return AdapterResult(
                agent=spec.agent,
                kind=spec.kind,
                returncode=127,
                stderr=spec.reason or "adapter unavailable",
            )
        if spec.kind == "http":
            return self._invoke_http(
                spec, prompt, payload, workspace, task_token=task_token,
                caller_agent=caller_agent,
            )
        if spec.kind == "ollama":
            return self._invoke_ollama(
                spec, prompt, payload, workspace, task_token=task_token,
                caller_agent=caller_agent,
            )
        if spec.kind == "mcp":
            return self._invoke_mcp(
                spec, prompt, payload, workspace, task_token=task_token,
                caller_agent=caller_agent,
            )
        return self._invoke_command(
            spec, prompt, workspace, heartbeat, cancel_check,
            task_token=task_token, caller_agent=caller_agent, payload=payload,
        )

    def _invoke_command(
        self,
        spec: AdapterSpec,
        prompt: str,
        workspace: Path,
        heartbeat: Callable[[], None] | None,
        cancel_check: Callable[[], bool] | None,
        *,
        task_token: str = "",
        caller_agent: str = "",
        payload: dict[str, Any] | None = None,
    ) -> AdapterResult:
        argv = _render_argv(spec.command, prompt=prompt, workspace=workspace, agent=spec.agent, model=spec.model)
        if spec.agent.lower().startswith("claude"):
            argv = _claude_role_argv(argv, str((payload or {}).get("role") or "worker"))
        started = time.monotonic()
        environment = _headless_environment((spec.auth_env,))
        environment["AGENT_MESH_AUTONOMOUS_AGENT"] = spec.agent
        environment["AGENT_MESH_AGENT_NAME"] = caller_agent or spec.agent
        if task_token:
            environment["AGENT_MESH_TASK_TOKEN"] = task_token
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(workspace),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return AdapterResult(
                agent=spec.agent,
                kind=spec.kind,
                stderr=redact_text(str(exc)),
                returncode=127,
                duration_seconds=time.monotonic() - started,
            )
        output, errors, timed_out, cancelled = _capture_process(
            process, timeout=max(0.0, spec.timeout - (time.monotonic() - started)),
            heartbeat_interval=spec.heartbeat_interval, heartbeat=heartbeat,
            cancel_check=cancel_check,
        )
        return AdapterResult(
            agent=spec.agent,
            kind=spec.kind,
            stdout=output,
            stderr=errors,
            returncode=process.returncode if process.returncode is not None else 124,
            timed_out=timed_out,
            cancelled=cancelled,
            duration_seconds=time.monotonic() - started,
        )

    def _invoke_http(
        self,
        spec: AdapterSpec,
        prompt: str,
        payload: dict[str, Any],
        workspace: Path,
        *,
        task_token: str = "",
        caller_agent: str = "",
    ) -> AdapterResult:
        started = time.monotonic()
        allowed, reason = _provider_endpoint_allowed(spec.endpoint)
        if not allowed:
            return AdapterResult(
                agent=spec.agent,
                kind=spec.kind,
                returncode=403,
                stderr=reason,
                duration_seconds=time.monotonic() - started,
            )
        body = json.dumps(
            sanitize(
                {
                    "prompt": prompt,
                    "agent": spec.agent,
                    "workspace": str(workspace),
                    "task": payload,
                }
            ),
            separators=(",", ":"),
        ).encode()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if caller_agent:
            headers["X-Agent-Mesh-Agent"] = caller_agent
        if task_token:
            headers["X-Agent-Mesh-Task-Lease"] = task_token
        if spec.auth_env and os.environ.get(spec.auth_env):
            headers["Authorization"] = "Bearer " + os.environ[spec.auth_env]
        request = urllib.request.Request(spec.endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=spec.timeout) as response:
                output = response.read(MAX_PROVIDER_OUTPUT + 1).decode(errors="replace")
            if len(output) > MAX_PROVIDER_OUTPUT:
                output = output[:MAX_PROVIDER_OUTPUT]
            return AdapterResult(
                agent=spec.agent,
                kind=spec.kind,
                stdout=output,
                duration_seconds=time.monotonic() - started,
            )
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            return AdapterResult(
                agent=spec.agent,
                kind=spec.kind,
                stderr=redact_text(str(exc)),
                returncode=1,
                duration_seconds=time.monotonic() - started,
            )

    def _invoke_mcp(
        self,
        spec: AdapterSpec,
        prompt: str,
        payload: dict[str, Any],
        workspace: Path,
        *,
        task_token: str = "",
        caller_agent: str = "",
    ) -> AdapterResult:
        started = time.monotonic()
        arguments = dict(payload)
        arguments["prompt"] = prompt
        arguments["workspace"] = str(workspace)
        if caller_agent:
            arguments["_caller_agent"] = caller_agent
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": spec.tool, "arguments": sanitize(arguments)},
            },
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}},
        ]
        wire = b"".join(_frame(item) for item in requests)
        try:
            environment = _headless_environment((spec.auth_env,))
            if caller_agent:
                environment["AGENT_MESH_AGENT_NAME"] = caller_agent
            if task_token:
                # Scoped lease material travels through the process
                # environment, never through the provider prompt or MCP
                # arguments/logs.
                environment["AGENT_MESH_TASK_TOKEN"] = task_token
            process = subprocess.Popen(
                [_executable(spec.command[0]), *spec.command[1:]],
                cwd=str(workspace),
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            stdout, stderr, timed_out, cancelled = _capture_process(
                process, timeout=spec.timeout,
                heartbeat_interval=spec.heartbeat_interval, input_data=wire,
            )
            return AdapterResult(
                agent=spec.agent,
                kind=spec.kind,
                stdout=stdout,
                stderr=stderr,
                returncode=process.returncode or 0,
                timed_out=timed_out,
                cancelled=cancelled,
                duration_seconds=time.monotonic() - started,
            )
        except OSError as exc:
            return AdapterResult(
                agent=spec.agent,
                kind=spec.kind,
                returncode=127,
                stderr=redact_text(str(exc)),
                duration_seconds=time.monotonic() - started,
            )

    def _invoke_ollama(
        self,
        spec: AdapterSpec,
        prompt: str,
        payload: dict[str, Any],
        workspace: Path,
        *,
        task_token: str = "",
        caller_agent: str = "",
    ) -> AdapterResult:
        started = time.monotonic()
        allowed, reason = _provider_endpoint_allowed(spec.endpoint)
        if not allowed:
            return AdapterResult(
                agent=spec.agent,
                kind=spec.kind,
                returncode=403,
                stderr=reason,
                duration_seconds=time.monotonic() - started,
            )
        body = json.dumps(
            {
                "model": spec.model,
                "stream": False,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a worker in a durable agent mesh. Return only the "
                            "requested JSON result; do not claim actions you did not perform."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "options": {"temperature": 0.1},
            },
            separators=(",", ":"),
        ).encode()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if caller_agent:
            headers["X-Agent-Mesh-Agent"] = caller_agent
        if task_token:
            headers["X-Agent-Mesh-Task-Lease"] = task_token
        request = urllib.request.Request(spec.endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=spec.timeout) as response:
                document = json.loads(response.read(MAX_PROVIDER_OUTPUT + 1).decode(errors="replace"))
            content = ""
            if isinstance(document, dict):
                message = document.get("message")
                if isinstance(message, dict):
                    content = str(message.get("content") or "")
                if not content:
                    content = str(document.get("response") or document.get("output") or "")
            if not content:
                content = json.dumps(document, separators=(",", ":"))
            return AdapterResult(
                agent=spec.agent,
                kind=spec.kind,
                stdout=_bounded(content),
                duration_seconds=time.monotonic() - started,
            )
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
            return AdapterResult(
                agent=spec.agent,
                kind=spec.kind,
                stderr=redact_text(str(exc)),
                returncode=1,
                duration_seconds=time.monotonic() - started,
            )


def _positive_number(value: Any, default: float) -> float:
    try:
        return max(float(value), 0.1)
    except (TypeError, ValueError):
        return default


def _ollama_model_available(endpoint: str, model: str) -> bool:
    """Probe Ollama's local tag catalog without exposing credentials."""
    if not endpoint or not model:
        return False
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        tags_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, "/api/tags", "", "")
        )
        with urllib.request.urlopen(tags_url, timeout=2.0) as response:
            document = json.loads(response.read(512 * 1024).decode(errors="replace"))
        names = {
            str(item.get("name") or item.get("model") or "")
            for item in (document.get("models") or [])
            if isinstance(item, dict)
        }
        # Ollama interprets an omitted tag as latest; another quantization or
        # size sharing the same base name cannot satisfy an explicit tag.
        expected = model if ":" in model.rsplit("/", 1)[-1] else model + ":latest"
        return expected in names or (expected == model + ":latest" and model in names)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError):
        return False


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return max(int(default), 1)


def _public_endpoint(value: Any) -> str:
    """Expose endpoint location without query-string credentials."""
    endpoint = redact_text(value or "")
    if not endpoint:
        return ""
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme and parsed.netloc:
            host = parsed.hostname or ""
            if ":" in host and not host.startswith("["):
                host = "[" + host + "]"
            if parsed.port:
                host += ":" + str(parsed.port)
            return urllib.parse.urlunsplit(
                (parsed.scheme, host, parsed.path, "", "")
            )
    except ValueError:
        pass
    return endpoint.split("?", 1)[0].split("#", 1)[0]


def _render_argv(
    command: tuple[str, ...], *, prompt: str, workspace: Path, agent: str, model: str
) -> list[str]:
    replacements = {
        "{prompt}": prompt,
        "{workspace}": str(workspace),
        "{agent}": agent,
        "{model}": model,
    }
    argv = []
    has_prompt = False
    for token in command:
        rendered = str(token)
        for marker, value in replacements.items():
            if marker in rendered:
                rendered = rendered.replace(marker, value)
                if marker == "{prompt}":
                    has_prompt = True
        argv.append(rendered)
    if argv:
        argv[0] = _executable(argv[0]) or argv[0]
    if not has_prompt:
        argv.append(prompt)
    return argv


def _claude_role_argv(argv: list[str], role: str) -> list[str]:
    """Use a schema matching the autonomous role being invoked.

    Claude's structured-output option validates the model response before the
    mesh parser sees it.  Reusing the worker schema for planning or auditing
    makes otherwise healthy Claude sessions return an unusable response.
    """
    schemas = {
        "planner": CLAUDE_PLANNER_JSON_SCHEMA,
        "planner_repair": CLAUDE_PLANNER_JSON_SCHEMA,
        "auditor": CLAUDE_AUDIT_JSON_SCHEMA,
    }
    schema = schemas.get(role)
    if not schema:
        return argv
    try:
        index = argv.index("--json-schema")
    except ValueError:
        return argv
    if index + 1 >= len(argv):
        return argv
    rendered = list(argv)
    rendered[index + 1] = schema
    return rendered


def _text_chunk(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _bounded(value: str, maximum: int = MAX_PROVIDER_OUTPUT) -> str:
    value = str(value or "")
    return value if len(value) <= maximum else value[:maximum]


def _capture_process(
    process: subprocess.Popen,
    *,
    timeout: float,
    heartbeat_interval: float,
    heartbeat: Callable[[], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    input_data: bytes | None = None,
) -> tuple[str, str, bool, bool]:
    """Drain bounded byte buffers, retaining each output byte only once.

    communicate() retains all output internally and TimeoutExpired exposes a
    cumulative snapshot. Reading nonblocking pipes avoids both unbounded
    capture and repeated snapshots across heartbeat intervals.
    """
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    pending_input = memoryview(input_data or b"")
    timed_out = cancelled = False
    deadline = time.monotonic() + timeout
    interval = max(0.05, heartbeat_interval)
    next_heartbeat = time.monotonic() + interval
    terminate_deadline: float | None = None
    drain_deadline: float | None = None

    def close_stream(stream: Any) -> None:
        selector.unregister(stream)
        stream.close()

    try:
        for name in ("stdout", "stderr"):
            stream = getattr(process, name)
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
        if process.stdin is not None:
            if pending_input:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            running = process.poll() is None
            if running and terminate_deadline is None:
                if cancel_check:
                    try:
                        cancelled = bool(cancel_check())
                    except Exception:
                        pass
                timed_out = not cancelled and now >= deadline
                if cancelled or timed_out:
                    _terminate_process_group(process)
                    terminate_deadline = now + 1.0
            if terminate_deadline is not None and now >= terminate_deadline and running:
                _terminate_process_group(process, force=True)
                break
            if not running:
                # Descendants may retain inherited pipes after the CLI exits.
                # Drain queued bytes, then clean up that isolated process group.
                if drain_deadline is None:
                    drain_deadline = now + 0.25
                if now >= drain_deadline:
                    _terminate_process_group(process, force=True, include_descendants=True)
                    break
            if running and now >= next_heartbeat:
                if heartbeat:
                    try:
                        heartbeat()
                    except Exception:
                        pass
                next_heartbeat = now + interval
            wait = min(0.1, max(0.0, next_heartbeat - now)) if running else 0.05
            for key, _ in selector.select(wait):
                stream, name = key.fileobj, key.data
                try:
                    if name == "stdin":
                        written = os.write(stream.fileno(), pending_input[:65536])
                        pending_input = pending_input[written:]
                        if not pending_input:
                            close_stream(stream)
                    else:
                        chunk = os.read(stream.fileno(), 65536)
                        if not chunk:
                            close_stream(stream)
                        else:
                            remaining = MAX_PROVIDER_OUTPUT - len(buffers[name])
                            if remaining > 0:
                                buffers[name].extend(chunk[:remaining])
                except BlockingIOError:
                    continue
                except OSError:
                    close_stream(stream)
    finally:
        if process.poll() is None or selector.get_map():
            _terminate_process_group(process, force=True, include_descendants=True)
        for key in list(selector.get_map().values()):
            close_stream(key.fileobj)
        selector.close()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            _terminate_process_group(process, force=True)
            process.wait(timeout=1.0)
    return (
        buffers["stdout"].decode(errors="replace"),
        buffers["stderr"].decode(errors="replace"),
        timed_out,
        cancelled,
    )


def _terminate_process_group(
    process: subprocess.Popen, force: bool = False, *, include_descendants: bool = False
) -> None:
    if process.poll() is not None and not include_descendants:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            process.kill() if force else process.terminate()
        except OSError:
            return


def _frame(value: dict[str, Any]) -> bytes:
    body = json.dumps(value, separators=(",", ":")).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def _json_candidates(text: str) -> list[Any]:
    cleaned = _ANSI.sub("", str(text or ""))
    # Some existing local clients render their response inside a terminal
    # panel.  The JSON is still valid, but each line is prefixed/suffixed by
    # the panel's vertical border (for example ``│ { │``).  Normalize only
    # those line borders so ordinary provider output and JSON strings that
    # contain ``|`` remain untouched.
    framed_lines: list[str] = []
    framed_payload: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if stripped.startswith("│"):
            stripped = stripped[1:]
            if stripped.endswith("│"):
                stripped = stripped[:-1]
            framed_payload.append(stripped.strip())
            line = stripped.strip()
        framed_lines.append(line)
    cleaned = "\n".join(framed_lines).strip()
    if not cleaned:
        return []
    candidates: list[Any] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        try:
            key = json.dumps(value, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return
        if key not in seen:
            seen.add(key)
            candidates.append(value)

    # Fixed-width terminal panels can also wrap a single JSON line in the
    # middle of a string.  Try the frame payload reassembled without and with
    # a separator; the former preserves mid-token wraps and the latter covers
    # clients that wrap only at whitespace.  These variants are only built
    # from bordered lines, so surrounding HUD prose cannot corrupt a normal
    # unframed provider response.
    variants = [cleaned]
    if framed_payload:
        variants.extend(
            [
                " ".join(framed_payload).strip(),
                "".join(framed_payload).strip(),
            ]
        )
    for variant in variants:
        try:
            add(json.loads(variant))
        except (TypeError, ValueError):
            pass
        for line in variant.splitlines():
            line = line.strip()
            if not line or line.startswith("```"):
                continue
            try:
                add(json.loads(line))
            except (TypeError, ValueError):
                continue
        decoder = json.JSONDecoder()
        for index, char in enumerate(variant):
            if char not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(variant[index:])
            except (TypeError, ValueError):
                continue
            add(value)
    return candidates


def _walk(value: Any, depth: int = 0):
    yield value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                nested = json.loads(stripped)
            except (TypeError, ValueError):
                nested = None
            if isinstance(nested, (dict, list)):
                yield from _walk(nested, depth + 1)
        elif depth < 2:
            # JSON event streams often carry the model response in a text
            # field surrounded by prose or a Markdown fence.  Recover an
            # embedded object without treating the surrounding text as an
            # executable instruction.
            for nested in _json_candidates(stripped):
                if isinstance(nested, (dict, list)):
                    yield from _walk(nested, depth + 1)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk(child, depth)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, depth)


def _text_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_text_from_value(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in (
            "summary",
            "response",
            "output",
            "text",
            "result",
            "message",
            "content",
            "part",
        ):
            if key in value:
                text = _text_from_value(value[key])
                if text:
                    return text
    return ""


def _is_lifecycle_text(text: str) -> bool:
    """Reject provider lifecycle chatter as a completed worker result."""
    normalized = re.sub(r"\s+", " ", _ANSI.sub("", str(text or ""))).strip().lower()
    if not normalized:
        return True
    return normalized in {
        "provider execution started",
        "provider execution finished",
        "reading additional input from stdin...",
        "reading additional input from stdin",
        "turn started",
        "turn completed",
        "thread started",
        "session started",
        "item started",
        "assistant turn started",
    } or normalized.startswith("provider execution started ")


def parse_plan(text: str) -> dict[str, Any] | None:
    for candidate in _json_candidates(text):
        for value in _walk(candidate):
            if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
                continue
            tasks = [item for item in value["tasks"] if isinstance(item, dict)]
            if tasks:
                return {"tasks": sanitize(tasks)}
    return None


def parse_audit(text: str) -> dict[str, Any] | None:
    for candidate in _json_candidates(text):
        for value in _walk(candidate):
            if not isinstance(value, dict):
                continue
            valid = value.get("valid", value.get("approved"))
            if isinstance(valid, bool):
                issues = value.get("issues") or value.get("findings") or []
                return {
                    "valid": valid,
                    "issues": sanitize(issues if isinstance(issues, list) else [issues]),
                    "revision_instructions": redact_text(
                        value.get("revision_instructions")
                        or value.get("instructions")
                        or value.get("reason")
                        or ""
                    ),
                    "tests_to_run": sanitize(value.get("tests_to_run") or []),
                }
    return None


def parse_worker_result(result: AdapterResult) -> dict[str, Any] | None:
    if not result.ok:
        return None
    candidates = _json_candidates(result.stdout)
    for candidate in candidates:
        for value in _walk(candidate):
            if not isinstance(value, dict):
                continue
            nested = value.get("result")
            if (
                isinstance(nested, dict)
                and str(nested.get("summary") or "").strip()
                and not _is_lifecycle_text(nested.get("summary"))
            ):
                return sanitize(nested)
            if (
                str(value.get("summary") or "").strip()
                and not _is_lifecycle_text(value.get("summary"))
            ):
                return sanitize(value)
    text_values = [_text_from_value(candidate) for candidate in candidates]
    text = max(
        (value for value in text_values if not _is_lifecycle_text(value)),
        key=len,
        default="",
    )
    if not text:
        if candidates:
            return None
        text = _ANSI.sub("", result.stdout).strip()
    if not text or _is_lifecycle_text(text):
        return None
    return {
        "summary": text[:50000],
        "files_changed": [],
        "files_created": [],
        "commands_executed": [],
        "tests": [],
        "warnings": ["Provider returned unstructured text; review the actual workspace and output."],
        "errors": [],
        "handoff_notes": [],
    }
