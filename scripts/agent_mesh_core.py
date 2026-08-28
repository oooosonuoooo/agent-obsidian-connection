#!/usr/bin/env python3
"""Durable coordination primitives for the Agent Mesh service.

The original project stored messages, but did not provide a worker-consumable
task queue or a state machine.  This module keeps the legacy SQLite schema
compatible and adds the deterministic control plane used by the HTTP and MCP
adapters.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


TASK_TERMINAL = frozenset({"completed", "failed", "blocked", "cancelled"})
TASK_ACTIVE = frozenset({"sent", "acknowledged", "running", "verifying"})
TASK_STATES = frozenset(
    {
        "pending",
        "waiting_dependency",
        "waiting_agent",
        "retrying",
        "sent",
        "acknowledged",
        "running",
        "verifying",
        "completed",
        "failed",
        "blocked",
        "cancelled",
    }
)
MESSAGE_TYPES = frozenset(
    {
        "TASK_REQUEST",
        "TASK_ACK",
        "TASK_PROGRESS",
        "TASK_RESULT",
        "TASK_ERROR",
        "TASK_CANCEL",
        "CLARIFICATION_REQUEST",
        "CLARIFICATION_RESPONSE",
        "AGENT_HEARTBEAT",
        "DIRECT_MESSAGE",
    }
)
RUN_TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED", "PARTIALLY_FAILED"})


class MeshError(Exception):
    """An error safe to return from the REST API."""

    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


class _ClosingConnection(sqlite3.Connection):
    """Close connections used by ``with store.connect()`` at context exit."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


@dataclass(frozen=True)
class Settings:
    root: Path
    db: Path
    vault: Path
    host: str
    port: int
    token: str | None
    ack_timeout: float
    execution_timeout: float
    max_retries: int
    retry_backoff: float
    heartbeat_timeout: float
    max_parallel: int
    max_delegation_depth: int
    reaper_interval: float
    max_body_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(
            os.environ.get("AI_SECOND_BRAIN_ROOT", str(Path.home() / "AI-Second-Brain"))
        ).expanduser().resolve()
        db = Path(
            os.environ.get("AGENT_MESH_DB_PATH", str(root / ".agent_mesh" / "agent_mesh.sqlite"))
        ).expanduser().resolve()
        vault = Path(
            os.environ.get("OBSIDIAN_VAULT_PATH", str(root / "AI-Second-Brain-Vault"))
        ).expanduser().resolve()

        def number(name: str, default: float, minimum: float) -> float:
            try:
                value = float(os.environ.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(value, minimum)

        def integer(name: str, default: int, minimum: int) -> int:
            try:
                value = int(os.environ.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(value, minimum)

        return cls(
            root=root,
            db=db,
            vault=vault,
            # The service is intentionally local-only.  There is no opt-out
            # through an environment variable.
            host="127.0.0.1",
            port=integer("AGENT_MESH_PORT", 17860, 1),
            token=os.environ.get("AGENT_MESH_TOKEN"),
            ack_timeout=number("AGENT_ACK_TIMEOUT", 30.0, 0.1),
            execution_timeout=number("AGENT_EXECUTION_TIMEOUT", 1800.0, 1.0),
            max_retries=integer("AGENT_MAX_RETRIES", 2, 0),
            retry_backoff=number("AGENT_RETRY_BACKOFF", 2.0, 0.0),
            heartbeat_timeout=number("AGENT_HEARTBEAT_TIMEOUT", 120.0, 1.0),
            max_parallel=integer("MAX_PARALLEL_AGENT_TASKS", 8, 1),
            max_delegation_depth=integer("MAX_DELEGATION_DEPTH", 3, 0),
            reaper_interval=number("AGENT_MESH_REAPER_INTERVAL", 1.0, 0.05),
            max_body_bytes=integer("AGENT_MESH_MAX_BODY_BYTES", 2 * 1024 * 1024, 4096),
        )


def load_env(root: Path | None = None) -> None:
    """Load local references for standalone service starts without printing values."""
    root = root or Path(
        os.environ.get("AI_SECOND_BRAIN_ROOT", str(Path.home() / "AI-Second-Brain"))
    ).expanduser()
    paths = (root / ".env.local", Path.home() / "airllm" / ".env")
    for path in paths:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            item = line.strip()
            if not item or item.startswith("#") or "=" not in item:
                continue
            if item.startswith("export "):
                item = item[7:]
            key, value = item.split("=", 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip("'\"")
    if "AGENT_MESH_TOKEN" not in os.environ and os.environ.get("FRIDAY_WEB_TOKEN"):
        os.environ["AGENT_MESH_TOKEN"] = os.environ["FRIDAY_WEB_TOKEN"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def after(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 0))).isoformat(
        timespec="seconds"
    )


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


_SECRET_TEXT = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"password|passwd|secret|credential)\b[\"']?\s*[:=]\s*)([\"']?)([^,\s}\"']+)(\2)"
)
_AUTH_TEXT = re.compile(r"(?i)(\bAuthorization\s*:\s*Bearer\s+)[^\s,}\"']+")
_SECRET_KEY = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"password|passwd|secret|credential)"
)


def redact_text(value: Any) -> str:
    text = str(value)
    text = _AUTH_TEXT.sub(r"\1[REDACTED]", text)
    return _SECRET_TEXT.sub(r"\1\2[REDACTED]\4", text)


def sanitize(value: Any, key: str | None = None) -> Any:
    if key and _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def json_text(value: Any, default: Any = None) -> str:
    if value is None:
        value = {} if default is None else default
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return redact_text(value)
        value = parsed
    return json.dumps(sanitize(value), sort_keys=True, separators=(",", ":"))


def json_value(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def nonempty_text(value: Any, field: str, maximum: int = 20000) -> str:
    text = str(value or "").strip()
    if not text:
        raise MeshError(f"{field} is required")
    if len(text) > maximum:
        raise MeshError(f"{field} is too long")
    return redact_text(text)


class MeshStore:
    """SQLite-backed state and transition engine."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.db.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(
            str(self.settings.db), timeout=30, factory=_ClosingConnection
        )
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA busy_timeout=5000")
        database.execute("PRAGMA foreign_keys=ON")
        return database

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        database = self.connect()
        try:
            database.execute("BEGIN IMMEDIATE")
            yield database
            database.commit()
        except Exception:
            database.rollback()
            raise
        finally:
            database.close()

    @staticmethod
    def _columns(database: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in database.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _add_missing_columns(
        database: sqlite3.Connection, table: str, columns: dict[str, str]
    ) -> None:
        existing = MeshStore._columns(database, table)
        for name, definition in columns.items():
            if name not in existing:
                database.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def init_db(self) -> None:
        with self.transaction() as database:
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    provider TEXT,
                    type TEXT,
                    capabilities_json TEXT NOT NULL DEFAULT '{}',
                    limitations TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
                    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
                    endpoint TEXT,
                    model TEXT,
                    max_concurrent_tasks INTEGER NOT NULL DEFAULT 1,
                    health TEXT NOT NULL DEFAULT 'unknown',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    heartbeat_interval_seconds INTEGER NOT NULL DEFAULT 30
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_agent TEXT,
                    to_agent TEXT NOT NULL,
                    task_id INTEGER,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    read_at TEXT,
                    message_key TEXT,
                    correlation_id TEXT,
                    conversation_id TEXT,
                    message_type TEXT NOT NULL DEFAULT 'DIRECT_MESSAGE',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    attempt INTEGER NOT NULL DEFAULT 1,
                    max_attempts INTEGER NOT NULL DEFAULT 1,
                    available_at TEXT,
                    sent_at TEXT,
                    delivered_at TEXT,
                    acknowledged_at TEXT,
                    completed_at TEXT,
                    error TEXT,
                    idempotency_key TEXT,
                    reply_to TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    owner_agent TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority TEXT NOT NULL DEFAULT 'medium',
                    project TEXT,
                    context_path TEXT,
                    result_path TEXT,
                    last_heartbeat_at TEXT,
                    last_active_agent TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    resume_packet_path TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    task_key TEXT,
                    run_id TEXT,
                    parent_task_id INTEGER,
                    created_by TEXT,
                    lead_agent TEXT,
                    description TEXT,
                    assigned_agent TEXT,
                    required_capabilities_json TEXT NOT NULL DEFAULT '[]',
                    dependencies_json TEXT NOT NULL DEFAULT '[]',
                    artifact_paths_json TEXT NOT NULL DEFAULT '[]',
                    candidate_agents_json TEXT NOT NULL DEFAULT '[]',
                    task_type TEXT NOT NULL DEFAULT 'work',
                    input_json TEXT NOT NULL DEFAULT '{}',
                    acceptance_criteria TEXT,
                    interfaces_json TEXT NOT NULL DEFAULT '{}',
                    constraints TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 2,
                    ack_timeout_seconds REAL NOT NULL DEFAULT 30,
                    execution_timeout_seconds REAL NOT NULL DEFAULT 1800,
                    retry_backoff_seconds REAL NOT NULL DEFAULT 2,
                    idempotency_key TEXT,
                    correlation_id TEXT,
                    conversation_id TEXT,
                    assigned_provider TEXT,
                    assigned_model TEXT,
                    sent_at TEXT,
                    ack_at TEXT,
                    started_at TEXT,
                    result_received_at TEXT,
                    verified_at TEXT,
                    completed_at TEXT,
                    failed_at TEXT,
                    next_attempt_at TEXT,
                    waiting_reason TEXT,
                    verification_status TEXT,
                    result_json TEXT,
                    error_json TEXT,
                    failed_agents_json TEXT NOT NULL DEFAULT '[]',
                    reassign_on_retry INTEGER NOT NULL DEFAULT 1,
                    delegation_depth INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    category TEXT,
                    body TEXT NOT NULL,
                    source TEXT,
                    confidence REAL,
                    sensitivity TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS handoffs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    from_agent TEXT NOT NULL,
                    to_agent TEXT NOT NULL,
                    request TEXT NOT NULL,
                    response TEXT,
                    status TEXT NOT NULL DEFAULT 'requested',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    owner_agent TEXT,
                    endpoint TEXT,
                    transport TEXT,
                    auth_ref TEXT,
                    tools_json TEXT NOT NULL DEFAULT '[]',
                    safety_limits TEXT,
                    status TEXT NOT NULL DEFAULT 'unverified',
                    last_verified_at TEXT
                );
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    owner_agent TEXT,
                    skill_type TEXT,
                    input_format TEXT,
                    output_format TEXT,
                    invocation_method TEXT,
                    limitations TEXT,
                    status TEXT NOT NULL DEFAULT 'unverified',
                    last_verified_at TEXT,
                    UNIQUE(name, owner_agent)
                );
                CREATE TABLE IF NOT EXISTS orchestration_runs (
                    id TEXT PRIMARY KEY,
                    request TEXT NOT NULL,
                    lead_agent TEXT NOT NULL,
                    state TEXT NOT NULL,
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    max_delegation_depth INTEGER NOT NULL DEFAULT 3,
                    failure_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    final_result_json TEXT,
                    finalized_by TEXT,
                    finalized_at TEXT
                );
                CREATE TABLE IF NOT EXISTS task_dependencies (
                    task_id INTEGER NOT NULL,
                    depends_on_task_id INTEGER NOT NULL,
                    PRIMARY KEY (task_id, depends_on_task_id)
                );
                CREATE TABLE IF NOT EXISTS task_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    task_key TEXT,
                    attempt INTEGER NOT NULL,
                    agent_id TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    idempotency_key TEXT,
                    submitted_at TEXT NOT NULL,
                    verified_at TEXT
                );
                CREATE TABLE IF NOT EXISTS orchestration_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    task_id INTEGER,
                    message_id INTEGER,
                    event_type TEXT NOT NULL,
                    actor TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_locks (
                    path TEXT PRIMARY KEY,
                    task_id INTEGER NOT NULL,
                    owner_agent TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    acquired_at TEXT NOT NULL,
                    released_at TEXT
                );
                """
            )

            # Existing installations have the original seven tables.  These
            # migrations are deliberately additive and do not rewrite data.
            self._add_missing_columns(
                database,
                "agents",
                {
                    "endpoint": "TEXT",
                    "model": "TEXT",
                    "max_concurrent_tasks": "INTEGER NOT NULL DEFAULT 1",
                    "health": "TEXT NOT NULL DEFAULT 'unknown'",
                    "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
                    "heartbeat_interval_seconds": "INTEGER NOT NULL DEFAULT 30",
                },
            )
            self._add_missing_columns(
                database,
                "messages",
                {
                    "message_key": "TEXT",
                    "correlation_id": "TEXT",
                    "conversation_id": "TEXT",
                    "message_type": "TEXT NOT NULL DEFAULT 'DIRECT_MESSAGE'",
                    "payload_json": "TEXT NOT NULL DEFAULT '{}'",
                    "attempt": "INTEGER NOT NULL DEFAULT 1",
                    "max_attempts": "INTEGER NOT NULL DEFAULT 1",
                    "available_at": "TEXT",
                    "sent_at": "TEXT",
                    "delivered_at": "TEXT",
                    "acknowledged_at": "TEXT",
                    "completed_at": "TEXT",
                    "error": "TEXT",
                    "idempotency_key": "TEXT",
                    "reply_to": "TEXT",
                    "lease_owner": "TEXT",
                    "lease_expires_at": "TEXT",
                },
            )
            self._add_missing_columns(
                database,
                "tasks",
                {
                    "task_key": "TEXT",
                    "run_id": "TEXT",
                    "parent_task_id": "INTEGER",
                    "created_by": "TEXT",
                    "lead_agent": "TEXT",
                    "description": "TEXT",
                    "assigned_agent": "TEXT",
                    "required_capabilities_json": "TEXT NOT NULL DEFAULT '[]'",
                    "dependencies_json": "TEXT NOT NULL DEFAULT '[]'",
                    "artifact_paths_json": "TEXT NOT NULL DEFAULT '[]'",
                    "candidate_agents_json": "TEXT NOT NULL DEFAULT '[]'",
                    "task_type": "TEXT NOT NULL DEFAULT 'work'",
                    "input_json": "TEXT NOT NULL DEFAULT '{}'",
                    "acceptance_criteria": "TEXT",
                    "interfaces_json": "TEXT NOT NULL DEFAULT '{}'",
                    "constraints": "TEXT",
                    "attempt": "INTEGER NOT NULL DEFAULT 0",
                    "max_retries": "INTEGER NOT NULL DEFAULT 2",
                    "ack_timeout_seconds": "REAL NOT NULL DEFAULT 30",
                    "execution_timeout_seconds": "REAL NOT NULL DEFAULT 1800",
                    "retry_backoff_seconds": "REAL NOT NULL DEFAULT 2",
                    "idempotency_key": "TEXT",
                    "correlation_id": "TEXT",
                    "conversation_id": "TEXT",
                    "assigned_provider": "TEXT",
                    "assigned_model": "TEXT",
                    "sent_at": "TEXT",
                    "ack_at": "TEXT",
                    "started_at": "TEXT",
                    "result_received_at": "TEXT",
                    "verified_at": "TEXT",
                    "completed_at": "TEXT",
                    "failed_at": "TEXT",
                    "next_attempt_at": "TEXT",
                    "waiting_reason": "TEXT",
                    "verification_status": "TEXT",
                    "result_json": "TEXT",
                    "error_json": "TEXT",
                    "failed_agents_json": "TEXT NOT NULL DEFAULT '[]'",
                    "reassign_on_retry": "INTEGER NOT NULL DEFAULT 1",
                    "delegation_depth": "INTEGER NOT NULL DEFAULT 0",
                },
            )
            self._add_missing_columns(
                database,
                "orchestration_runs",
                {
                    "final_result_json": "TEXT",
                    "finalized_by": "TEXT",
                    "finalized_at": "TEXT",
                },
            )
            database.execute(
                "UPDATE messages SET message_key = lower(hex(randomblob(16))) "
                "WHERE message_key IS NULL OR message_key = ''"
            )
            database.execute(
                "UPDATE messages SET message_type = 'DIRECT_MESSAGE' "
                "WHERE message_type IS NULL OR message_type = ''"
            )
            database.execute(
                "UPDATE messages SET payload_json = '{}' "
                "WHERE payload_json IS NULL OR payload_json = ''"
            )
            database.execute(
                "UPDATE tasks SET task_key = 'legacy-task-' || id "
                "WHERE task_key IS NULL OR task_key = ''"
            )
            database.execute(
                "UPDATE tasks SET required_capabilities_json = '[]' "
                "WHERE required_capabilities_json IS NULL OR required_capabilities_json = ''"
            )
            database.execute(
                "UPDATE tasks SET dependencies_json = '[]' "
                "WHERE dependencies_json IS NULL OR dependencies_json = ''"
            )
            database.execute(
                "UPDATE tasks SET artifact_paths_json = '[]' "
                "WHERE artifact_paths_json IS NULL OR artifact_paths_json = ''"
            )
            database.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_message_key "
                "ON messages(message_key)"
            )
            database.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_task_key ON tasks(task_key)"
            )
            database.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_result_idempotency "
                "ON task_results(task_id, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            )
            database.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_delivery "
                "ON messages(to_agent, message_type, status, available_at)"
            )
            database.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_run_status "
                "ON tasks(run_id, status, next_attempt_at)"
            )
            database.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_assigned_status "
                "ON tasks(assigned_agent, status)"
            )
            database.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_run_created "
                "ON orchestration_events(run_id, created_at)"
            )

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def _vault_candidate(self, *relative: str) -> Path:
        for item in relative:
            path = self.settings.vault / item
            if path.parent.exists() or path.exists():
                return path
        return self.settings.vault / relative[0]

    def _write_note(self, path: Path, content: str, append: bool = False) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if append and path.exists():
                path.write_text(
                    path.read_text(encoding="utf-8", errors="ignore") + "\n" + content,
                    encoding="utf-8",
                )
            else:
                path.write_text(content, encoding="utf-8")
        except OSError:
            # Vault synchronization is auxiliary; it must not make a durable
            # task transition fail.
            return

    def sync_agent(self, agent: dict[str, Any]) -> None:
        path = self._vault_candidate("01_Agents/" + str(agent["name"]) + ".md")
        capabilities = json_value(agent.get("capabilities_json"), {})
        content = "\n".join(
            [
                "---",
                "type: agent",
                f"status: {agent.get('status') or 'active'}",
                f"health: {agent.get('health') or 'unknown'}",
                f"registered_at: {agent.get('registered_at') or utc_now()}",
                f"last_seen_at: {agent.get('last_seen_at') or utc_now()}",
                "---",
                f"# Agent Profile: {agent['name']}",
                "",
                "## Metadata",
                f"- Provider: {redact_text(agent.get('provider') or 'Unknown')}",
                f"- Model: {redact_text(agent.get('model') or 'Unknown')}",
                f"- Type: {redact_text(agent.get('type') or 'Unknown')}",
                f"- Endpoint: {redact_text(agent.get('endpoint') or 'Not advertised')}",
                "",
                "## Capabilities",
                json.dumps(sanitize(capabilities), indent=2, sort_keys=True),
                "",
                "## Limitations",
                redact_text(agent.get("limitations") or "None specified."),
                "",
            ]
        )
        self._write_note(path, content)
        self.sync_control_panel()

    def sync_control_panel(self) -> None:
        candidates = (
            self.settings.vault / "01_System" / "Agent_Control_Panel.md",
            self.settings.vault / "00_System" / "Agent_Control_Panel.md",
        )
        path = next((item for item in candidates if item.exists()), candidates[0])
        if not path.exists():
            return
        try:
            with self.connect() as database:
                agents = [dict(row) for row in database.execute("SELECT * FROM agents ORDER BY name")]
            lines = [
                "| Agent Name | Provider / Model | Type | Status | Health | Last Seen |",
                "|---|---|---|---|---|---|",
            ]
            for agent in agents:
                lines.append(
                    "| [[{}]] | {} / {} | {} | {} | {} | {} |".format(
                        agent["name"],
                        redact_text(agent.get("provider") or "Unknown"),
                        redact_text(agent.get("model") or "Unknown"),
                        redact_text(agent.get("type") or "Unknown"),
                        redact_text(agent.get("status") or "active"),
                        self.agent_health(agent),
                        agent.get("last_seen_at") or "unknown",
                    )
                )
            content = path.read_text(encoding="utf-8", errors="ignore")
            pattern = re.compile(r"(## Connected Agents Registry\s+).*?(?=\n+##|$)", re.DOTALL)
            replacement = lambda match: match.group(1) + "\n".join(lines) + "\n"
            if pattern.search(content):
                self._write_note(path, pattern.sub(replacement, content))
        except OSError:
            return

    def sync_task(self, task: dict[str, Any]) -> None:
        task_dir = "04_Tasks" if (self.settings.vault / "04_Tasks").exists() else "08_Tasks"
        path = self.settings.vault / task_dir / f"task_{task['id']}.md"
        content = "\n".join(
            [
                "---",
                "type: task",
                f"id: {task['id']}",
                f"task_key: {task.get('task_key') or 'unknown'}",
                f"run_id: {task.get('run_id') or 'none'}",
                f"status: {task.get('status') or 'pending'}",
                f"assigned_agent: {task.get('assigned_agent') or 'none'}",
                f"attempt: {task.get('attempt') or 0}",
                f"updated_at: {task.get('updated_at') or utc_now()}",
                "---",
                f"# Task {task['id']}: {redact_text(task.get('title') or '')}",
                "",
                f"- Project: {redact_text(task.get('project') or 'General')}",
                f"- Lead agent: {redact_text(task.get('lead_agent') or task.get('owner_agent') or 'none')}",
                f"- Assigned agent: {redact_text(task.get('assigned_agent') or 'none')}",
                f"- Status: {task.get('status') or 'pending'}",
                f"- Waiting reason: {redact_text(task.get('waiting_reason') or 'none')}",
                f"- Context path: {redact_text(task.get('context_path') or 'none')}",
                f"- Result path: {redact_text(task.get('result_path') or 'none')}",
                "",
                "## Description",
                redact_text(task.get("description") or "No description supplied."),
                "",
                "## Acceptance criteria",
                redact_text(task.get("acceptance_criteria") or "Lead agent verification required."),
                "",
            ]
        )
        self._write_note(path, content)

    def sync_message(self, message: dict[str, Any]) -> None:
        directory = "08_Inbox" if (self.settings.vault / "08_Inbox").exists() else "07_Inbox"
        path = self.settings.vault / directory / "messages_log.md"
        payload = json_value(message.get("payload_json"), {})
        content = "\n".join(
            [
                f"### Message {message['id']}: {redact_text(message.get('subject') or '')}",
                f"- From: {redact_text(message.get('from_agent') or 'unknown')}",
                f"- To: {redact_text(message.get('to_agent') or 'unknown')}",
                f"- Type: {message.get('message_type') or 'DIRECT_MESSAGE'}",
                f"- Correlation ID: {redact_text(message.get('correlation_id') or 'none')}",
                f"- Status: {message.get('status') or 'queued'}",
                f"- Created At: {message.get('created_at') or utc_now()}",
                "",
                "Body:",
                redact_text(message.get("body") or ""),
                "",
                "Payload:",
                json.dumps(sanitize(payload), indent=2, sort_keys=True),
                "",
                "---",
            ]
        )
        self._write_note(path, content, append=True)

    def sync_memory(self, memory: dict[str, Any]) -> None:
        path = self.settings.vault / "03_Memory" / "Memory_Inbox.md"
        content = "\n".join(
            [
                f"### Memory: {redact_text(memory.get('title') or 'Untitled')}",
                f"- Category: {redact_text(memory.get('category') or 'General')}",
                f"- Source: {redact_text(memory.get('source') or 'Unknown')}",
                f"- Confidence: {redact_text(memory.get('confidence') or 'unknown')}",
                f"- Sensitivity: {redact_text(memory.get('sensitivity') or 'unknown')}",
                f"- Created At: {memory.get('created_at') or utc_now()}",
                "",
                redact_text(memory.get("body") or ""),
                "",
                "---",
            ]
        )
        self._write_note(path, content, append=True)

    def agent_health(self, agent: dict[str, Any], at: datetime | None = None) -> str:
        status = str(agent.get("status") or "").lower()
        declared = str(agent.get("health") or "").lower()
        if status in {"offline", "disabled", "unavailable"}:
            return "offline"
        seen = parse_time(agent.get("last_seen_at"))
        at = at or datetime.now(timezone.utc)
        if seen is None or (at - seen).total_seconds() > self.settings.heartbeat_timeout:
            return "offline"
        if status == "busy" or declared == "busy":
            return "busy"
        if declared == "degraded" or status == "degraded":
            return "degraded"
        return "online"

    def register_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        name = nonempty_text(data.get("name"), "name", 200)
        stamp = utc_now()
        capabilities = data.get("capabilities")
        if capabilities is None:
            capabilities = data.get("capabilities_json", {})
        with self.transaction() as database:
            database.execute(
                """
                INSERT INTO agents
                (name, provider, type, capabilities_json, limitations, status,
                 registered_at, last_seen_at, endpoint, model,
                 max_concurrent_tasks, health, metadata_json, heartbeat_interval_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    provider=excluded.provider,
                    type=excluded.type,
                    capabilities_json=excluded.capabilities_json,
                    limitations=excluded.limitations,
                    status=excluded.status,
                    last_seen_at=excluded.last_seen_at,
                    endpoint=excluded.endpoint,
                    model=excluded.model,
                    max_concurrent_tasks=excluded.max_concurrent_tasks,
                    health=excluded.health,
                    metadata_json=excluded.metadata_json,
                    heartbeat_interval_seconds=excluded.heartbeat_interval_seconds
                """,
                (
                    name,
                    redact_text(data.get("provider") or ""),
                    redact_text(data.get("type") or ""),
                    json_text(capabilities, {}),
                    redact_text(data.get("limitations") or ""),
                    redact_text(data.get("status") or "active"),
                    stamp,
                    stamp,
                    redact_text(data.get("endpoint") or ""),
                    redact_text(data.get("model") or ""),
                    max(int(data.get("max_concurrent_tasks", 1)), 1),
                    redact_text(data.get("health") or "online"),
                    json_text(data.get("metadata") or data.get("metadata_json"), {}),
                    max(int(data.get("heartbeat_interval_seconds", 30)), 1),
                ),
            )
            row = self._dict(database.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone())
        assert row is not None
        self.sync_agent(row)
        return self.decorate_agent(row)

    def heartbeat_agent(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        name = nonempty_text(name, "agent", 200)
        stamp = utc_now()
        with self.transaction() as database:
            row = database.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone()
            if row is None:
                raise MeshError("agent is not registered", 404)
            status = redact_text(data.get("status") or "active")
            health = redact_text(data.get("health") or ("busy" if status == "busy" else "online"))
            database.execute(
                "UPDATE agents SET status=?, health=?, last_seen_at=? WHERE name=?",
                (status, health, stamp, name),
            )
            row = self._dict(database.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone())
        assert row is not None
        self.sync_agent(row)
        return self.decorate_agent(row)

    def get_agent(self, name: str) -> dict[str, Any]:
        with self.connect() as database:
            row = self._dict(database.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone())
        if row is None:
            raise MeshError("agent not found", 404)
        return self.decorate_agent(row)

    def list_agents(self) -> list[dict[str, Any]]:
        with self.connect() as database:
            agents = [self._dict(row) for row in database.execute("SELECT * FROM agents ORDER BY name")]
            loads = {
                row["assigned_agent"]: row["count"]
                for row in database.execute(
                    """
                    SELECT assigned_agent, COUNT(*) AS count
                    FROM tasks
                    WHERE assigned_agent IS NOT NULL
                      AND status IN ('sent','acknowledged','running','verifying')
                    GROUP BY assigned_agent
                    """
                )
            }
        result = []
        for agent in agents:
            assert agent is not None
            agent["health"] = self.agent_health(agent)
            agent["active_task_count"] = int(loads.get(agent["name"], 0))
            result.append(agent)
        return result

    def decorate_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        result = dict(agent)
        result["health"] = self.agent_health(result)
        result["capabilities"] = json_value(result.get("capabilities_json"), {})
        result["metadata"] = json_value(result.get("metadata_json"), {})
        with self.connect() as database:
            result["active_task_count"] = database.execute(
                """
                SELECT COUNT(*) FROM tasks
                WHERE assigned_agent=? AND status IN ('sent','acknowledged','running','verifying')
                """,
                (result["name"],),
            ).fetchone()[0]
        return result

    def _resolve_task_id(
        self, database: sqlite3.Connection, reference: Any, required: bool = True
    ) -> int | None:
        if reference is None or reference == "":
            if required:
                raise MeshError("task_id is required")
            return None
        text = str(reference)
        if text.isdigit():
            row = database.execute("SELECT id FROM tasks WHERE id=?", (int(text),)).fetchone()
        else:
            row = database.execute("SELECT id FROM tasks WHERE task_key=?", (text,)).fetchone()
        if row is None:
            raise MeshError("task not found", 404)
        return int(row["id"])

    @staticmethod
    def _task_json_fields(task: dict[str, Any]) -> dict[str, Any]:
        result = dict(task)
        mappings = {
            "required_capabilities_json": "required_capabilities",
            "dependencies_json": "dependencies",
            "artifact_paths_json": "artifact_paths",
            "candidate_agents_json": "candidate_agents",
            "input_json": "input",
            "interfaces_json": "interfaces",
            "result_json": "result",
            "error_json": "error",
            "failed_agents_json": "failed_agents",
        }
        for source, target in mappings.items():
            result[target] = json_value(result.get(source), [] if target.endswith(("ies", "s")) else {})
        return result

    def decorate_task(self, task: dict[str, Any]) -> dict[str, Any]:
        return self._task_json_fields(task)

    def decorate_message(self, message: dict[str, Any]) -> dict[str, Any]:
        result = dict(message)
        result["payload"] = json_value(result.get("payload_json"), {})
        return result

    def _event(
        self,
        database: sqlite3.Connection,
        event_type: str,
        actor: str | None = None,
        run_id: str | None = None,
        task_id: int | None = None,
        message_id: int | None = None,
        payload: Any = None,
    ) -> None:
        database.execute(
            """
            INSERT INTO orchestration_events
            (run_id, task_id, message_id, event_type, actor, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task_id,
                message_id,
                redact_text(event_type),
                redact_text(actor or ""),
                json_text(payload, {}),
                utc_now(),
            ),
        )

    def _insert_message(
        self,
        database: sqlite3.Connection,
        *,
        to_agent: str,
        subject: str,
        body: str,
        from_agent: str | None = None,
        task_id: int | None = None,
        message_type: str = "DIRECT_MESSAGE",
        payload: Any = None,
        correlation_id: str | None = None,
        conversation_id: str | None = None,
        attempt: int = 1,
        max_attempts: int = 1,
        idempotency_key: str | None = None,
        reply_to: str | None = None,
        status: str = "queued",
    ) -> dict[str, Any]:
        to_agent = nonempty_text(to_agent, "to_agent", 200)
        subject = nonempty_text(subject, "subject", 500)
        body = redact_text(body)
        if idempotency_key:
            existing = database.execute(
                """
                SELECT * FROM messages
                WHERE idempotency_key=? AND to_agent=? AND message_type=?
                ORDER BY id DESC LIMIT 1
                """,
                (idempotency_key, to_agent, message_type),
            ).fetchone()
            if existing is not None:
                return dict(existing)
        stamp = utc_now()
        key = str(uuid.uuid4())
        database.execute(
            """
            INSERT INTO messages
            (from_agent, to_agent, task_id, subject, body, status, created_at,
             message_key, correlation_id, conversation_id, message_type, payload_json,
             attempt, max_attempts, available_at, sent_at, idempotency_key, reply_to)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                redact_text(from_agent or ""),
                to_agent,
                task_id,
                subject,
                body,
                status,
                stamp,
                key,
                redact_text(correlation_id or ""),
                redact_text(conversation_id or ""),
                redact_text(message_type or "DIRECT_MESSAGE"),
                json_text(payload, {}),
                max(int(attempt), 1),
                max(int(max_attempts), 1),
                stamp,
                stamp if status in {"sent", "delivered"} else None,
                redact_text(idempotency_key or ""),
                redact_text(reply_to or ""),
            ),
        )
        row = database.execute("SELECT * FROM messages WHERE id=?", (database.execute("SELECT last_insert_rowid()").fetchone()[0],)).fetchone()
        assert row is not None
        return dict(row)

    def create_message(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as database:
            task_id = self._resolve_task_id(database, data.get("task_id"), required=False)
            body = data.get("body")
            if body is None:
                body = json.dumps(sanitize(data.get("payload") or {}), sort_keys=True)
            row = self._insert_message(
                database,
                to_agent=data.get("to_agent"),
                subject=data.get("subject"),
                body=body,
                from_agent=data.get("from_agent"),
                task_id=task_id,
                message_type=data.get("message_type") or data.get("type") or "DIRECT_MESSAGE",
                payload=data.get("payload") or {},
                correlation_id=data.get("correlation_id"),
                conversation_id=data.get("conversation_id"),
                idempotency_key=data.get("idempotency_key"),
            )
            self._event(
                database,
                "message.created",
                actor=data.get("from_agent"),
                task_id=task_id,
                message_id=row["id"],
                payload={"message_key": row["message_key"], "type": row["message_type"]},
            )
        self.sync_message(row)
        return self.decorate_message(row)

    def get_messages(self, agent: str, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM messages WHERE to_agent=?"
        values: list[Any] = [agent]
        if status:
            query += " AND status=?"
            values.append(status)
        query += " ORDER BY created_at DESC, id DESC"
        with self.connect() as database:
            return [self.decorate_message(dict(row)) for row in database.execute(query, values)]

    def _normalize_list(self, value: Any, field: str, maximum: int = 100) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                value = [value]
        if not isinstance(value, list):
            raise MeshError(f"{field} must be a list")
        if len(value) > maximum:
            raise MeshError(f"{field} has too many entries")
        return [nonempty_text(item, field, 1000) for item in value]

    def _insert_task(
        self,
        database: sqlite3.Connection,
        *,
        title: str,
        owner_agent: str | None = None,
        run_id: str | None = None,
        task_key: str | None = None,
        parent_task_id: int | None = None,
        created_by: str | None = None,
        lead_agent: str | None = None,
        description: str | None = None,
        assigned_agent: str | None = None,
        required_capabilities: Any = None,
        dependencies: Any = None,
        artifact_paths: Any = None,
        candidate_agents: Any = None,
        task_type: str = "work",
        input_data: Any = None,
        acceptance_criteria: Any = None,
        interfaces: Any = None,
        constraints: Any = None,
        priority: str = "medium",
        max_retries: int | None = None,
        ack_timeout: float | None = None,
        execution_timeout: float | None = None,
        retry_backoff: float | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
        conversation_id: str | None = None,
        reassign_on_retry: bool = True,
        delegation_depth: int = 0,
        project: str | None = None,
        context_path: str | None = None,
        result_path: str | None = None,
        status: str = "pending",
    ) -> dict[str, Any]:
        key = task_key or str(uuid.uuid4())
        now = utc_now()
        database.execute(
            """
            INSERT INTO tasks
            (title, owner_agent, status, priority, project, context_path, result_path,
             created_at, updated_at, task_key, run_id, parent_task_id, created_by,
             lead_agent, description, assigned_agent, required_capabilities_json,
             dependencies_json, artifact_paths_json, candidate_agents_json, task_type,
             input_json, acceptance_criteria, interfaces_json, constraints, attempt,
             max_retries, ack_timeout_seconds, execution_timeout_seconds,
             retry_backoff_seconds, idempotency_key, correlation_id, conversation_id,
             reassign_on_retry, delegation_depth)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nonempty_text(title, "title", 500),
                redact_text(owner_agent or ""),
                status,
                redact_text(priority or "medium"),
                redact_text(project or ""),
                redact_text(context_path or ""),
                redact_text(result_path or ""),
                now,
                now,
                redact_text(key),
                redact_text(run_id or ""),
                parent_task_id,
                redact_text(created_by or ""),
                redact_text(lead_agent or ""),
                redact_text(description or ""),
                redact_text(assigned_agent or ""),
                json_text(required_capabilities or [], []),
                json_text(dependencies or [], []),
                json_text(artifact_paths or [], []),
                json_text(candidate_agents or [], []),
                redact_text(task_type or "work"),
                json_text(input_data or {}, {}),
                redact_text(acceptance_criteria or ""),
                json_text(interfaces or {}, {}),
                redact_text(constraints or ""),
                max(self.settings.max_retries if max_retries is None else int(max_retries), 0),
                max(self.settings.ack_timeout if ack_timeout is None else float(ack_timeout), 0.1),
                max(
                    self.settings.execution_timeout
                    if execution_timeout is None
                    else float(execution_timeout),
                    1.0,
                ),
                max(
                    self.settings.retry_backoff
                    if retry_backoff is None
                    else float(retry_backoff),
                    0.0,
                ),
                redact_text(idempotency_key or ""),
                redact_text(correlation_id or key),
                redact_text(conversation_id or run_id or ""),
                1 if reassign_on_retry else 0,
                max(int(delegation_depth), 0),
            ),
        )
        task_id = int(database.execute("SELECT last_insert_rowid()").fetchone()[0])
        row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        assert row is not None
        return dict(row)

    def create_legacy_task(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as database:
            row = self._insert_task(
                database,
                title=data.get("title"),
                owner_agent=data.get("owner_agent"),
                created_by=data.get("created_by"),
                lead_agent=data.get("lead_agent") or data.get("owner_agent"),
                description=data.get("description"),
                assigned_agent=data.get("assigned_agent") or data.get("assigned_to"),
                required_capabilities=data.get("required_capabilities"),
                artifact_paths=data.get("artifact_paths"),
                priority=data.get("priority", "medium"),
                project=data.get("project"),
                context_path=data.get("context_path"),
                result_path=data.get("result_path"),
                max_retries=data.get("max_retries"),
                status=data.get("status", "pending"),
            )
            self._event(database, "task.created", actor=data.get("created_by") or data.get("owner_agent"), task_id=row["id"])
        self.sync_task(row)
        return self.decorate_task(row)

    def _task_row(self, database: sqlite3.Connection, reference: Any) -> dict[str, Any]:
        task_id = self._resolve_task_id(database, reference)
        row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise MeshError("task not found", 404)
        return dict(row)

    def get_task(self, reference: Any) -> dict[str, Any]:
        with self.connect() as database:
            task = self._task_row(database, reference)
        return self.decorate_task(task)

    def list_tasks(self, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks"
        values: list[Any] = []
        if run_id:
            query += " WHERE run_id=?"
            values.append(run_id)
        query += " ORDER BY updated_at DESC, id DESC"
        with self.connect() as database:
            return [self.decorate_task(dict(row)) for row in database.execute(query, values)]

    def _capability_tokens(self, value: Any) -> set[str]:
        parsed = json_value(value, {})
        tokens: set[str] = set()

        def visit(item: Any, key: str | None = None) -> None:
            if key:
                tokens.add(str(key).lower())
            if isinstance(item, dict):
                for child_key, child in item.items():
                    if child is True or child is not False:
                        visit(child, str(child_key))
            elif isinstance(item, list):
                for child in item:
                    visit(child)
            elif isinstance(item, str):
                tokens.add(item.lower())

        visit(parsed)
        return tokens

    def _select_agent(
        self,
        database: sqlite3.Connection,
        task: dict[str, Any],
        exclude: set[str] | None = None,
    ) -> dict[str, Any] | None:
        exclude = {str(item) for item in (exclude or set())}
        required = [str(item).lower() for item in json_value(task.get("required_capabilities_json"), [])]
        candidates = json_value(task.get("candidate_agents_json"), [])
        explicit = str(task.get("assigned_agent") or "")
        if explicit and int(task.get("attempt") or 0) == 0:
            candidates = [explicit]
        elif explicit and not int(task.get("reassign_on_retry") or 0):
            candidates = [explicit]
        if candidates:
            allowed = {str(item) for item in candidates}
        else:
            allowed = None
        rows = [dict(row) for row in database.execute("SELECT * FROM agents ORDER BY name")]
        ranked: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
        at = datetime.now(timezone.utc)
        for agent in rows:
            name = str(agent["name"])
            if name in exclude or (allowed is not None and name not in allowed):
                continue
            if self.agent_health(agent, at) == "offline":
                continue
            capabilities = self._capability_tokens(agent.get("capabilities_json"))
            matches = sum(1 for capability in required if capability in capabilities)
            if required and matches != len(required):
                continue
            load = database.execute(
                """
                SELECT COUNT(*) FROM tasks
                WHERE assigned_agent=? AND status IN ('sent','acknowledged','running','verifying')
                """,
                (name,),
            ).fetchone()[0]
            limit = max(int(agent.get("max_concurrent_tasks") or 1), 1)
            if load >= limit:
                continue
            # More capability matches, more free capacity, and lower load win.
            score = (matches, limit - int(load), -int(load), name)
            ranked.append((score, agent))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]

    def _dependencies(self, database: sqlite3.Connection, task_id: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in database.execute(
                """
                SELECT t.* FROM task_dependencies d
                JOIN tasks t ON t.id=d.depends_on_task_id
                WHERE d.task_id=?
                ORDER BY t.id
                """,
                (task_id,),
            )
        ]

    def _artifacts_available(
        self, database: sqlite3.Connection, task: dict[str, Any]
    ) -> tuple[bool, str | None]:
        paths = json_value(task.get("artifact_paths_json"), [])
        for path in paths:
            lock = database.execute(
                "SELECT * FROM artifact_locks WHERE path=? AND status='active'",
                (str(path),),
            ).fetchone()
            if lock is not None and int(lock["task_id"]) != int(task["id"]):
                owner = lock["owner_agent"] or "another agent"
                return False, f"artifact is owned by task {lock['task_id']} ({owner})"
        return True, None

    def _acquire_artifacts(self, database: sqlite3.Connection, task: dict[str, Any], agent: str) -> None:
        for item in json_value(task.get("artifact_paths_json"), []):
            path = nonempty_text(item, "artifact path", 2000)
            existing = database.execute(
                "SELECT * FROM artifact_locks WHERE path=?", (path,)
            ).fetchone()
            if existing is None:
                database.execute(
                    """
                    INSERT INTO artifact_locks
                    (path, task_id, owner_agent, status, acquired_at)
                    VALUES (?, ?, ?, 'active', ?)
                    """,
                    (path, task["id"], agent, utc_now()),
                )
            elif existing["status"] != "active":
                database.execute(
                    """
                    UPDATE artifact_locks
                    SET task_id=?, owner_agent=?, status='active', acquired_at=?, released_at=NULL
                    WHERE path=?
                    """,
                    (task["id"], agent, utc_now(), path),
                )
            elif int(existing["task_id"]) == int(task["id"]):
                database.execute(
                    "UPDATE artifact_locks SET owner_agent=?, status='active', released_at=NULL WHERE path=?",
                    (agent, path),
                )
            else:
                raise MeshError("artifact lock changed while dispatching", 409)

    def _release_artifacts(self, database: sqlite3.Connection, task_id: int) -> None:
        database.execute(
            """
            UPDATE artifact_locks
            SET status='released', released_at=?
            WHERE task_id=? AND status='active'
            """,
            (utc_now(), task_id),
        )

    def _worker_payload(self, database: sqlite3.Connection, task: dict[str, Any]) -> dict[str, Any]:
        dependencies = []
        for dependency in self._dependencies(database, int(task["id"])):
            dependencies.append(
                {
                    "task_id": dependency.get("task_key") or dependency["id"],
                    "title": dependency["title"],
                    "status": dependency["status"],
                    "result": json_value(dependency.get("result_json"), {}),
                }
            )
        project_goal = ""
        if task.get("run_id"):
            run = database.execute(
                "SELECT request FROM orchestration_runs WHERE id=?",
                (task["run_id"],),
            ).fetchone()
            if run is not None:
                project_goal = run["request"] or ""
        return {
            "task_id": task.get("task_key") or task["id"],
            "database_task_id": task["id"],
            "run_id": task.get("run_id") or None,
            "project_goal": project_goal,
            "conversation_id": task.get("conversation_id") or task.get("run_id") or None,
            "correlation_id": task.get("correlation_id") or task.get("task_key"),
            "parent_task_id": task.get("parent_task_id"),
            "title": task["title"],
            "description": task.get("description") or "",
            "project": task.get("project") or "",
            "task_type": task.get("task_type") or "work",
            "attempt": task.get("attempt") or 0,
            "required_capabilities": json_value(task.get("required_capabilities_json"), []),
            "dependencies": dependencies,
            "interfaces": json_value(task.get("interfaces_json"), {}),
            "acceptance_criteria": task.get("acceptance_criteria") or "",
            "relevant_files": json_value(task.get("artifact_paths_json"), []),
            "constraints": task.get("constraints") or "",
            "input": json_value(task.get("input_json"), {}),
            "expected_result": {
                "summary": "string",
                "files_changed": "list",
                "files_created": "list",
                "commands_executed": "list",
                "tests": "list",
                "warnings": "list",
                "errors": "list",
                "handoff_notes": "list",
            },
        }

    def _queue_retry_locked(
        self,
        database: sqlite3.Connection,
        task: dict[str, Any],
        reason: Any,
        *,
        reassign: bool | None = None,
        event_type: str = "task.retrying",
    ) -> str:
        stamp = utc_now()
        current_attempt = int(task.get("attempt") or 0)
        max_total = int(task.get("max_retries") or 0) + 1
        error = reason if isinstance(reason, dict) else {"message": str(reason)}
        failed_agents = json_value(task.get("failed_agents_json"), [])
        assigned = task.get("assigned_agent")
        should_reassign = bool(task.get("reassign_on_retry")) if reassign is None else reassign
        if should_reassign and assigned and assigned not in failed_agents:
            failed_agents.append(assigned)
        database.execute(
            """
            UPDATE messages SET status='expired', error=?, completed_at=?
            WHERE task_id=? AND message_type='TASK_REQUEST'
              AND attempt=? AND status IN ('queued','sent','delivered','acknowledged')
            """,
            (redact_text(json.dumps(sanitize(error), sort_keys=True)), stamp, task["id"], current_attempt),
        )
        if current_attempt >= max_total:
            database.execute(
                """
                UPDATE tasks
                SET status='failed', error_json=?, failed_agents_json=?, failed_at=?,
                    verification_status='rejected', updated_at=?, waiting_reason=?,
                    lease_owner=NULL, lease_expires_at=NULL
                WHERE id=?
                """,
                (
                    json_text(error, {}),
                    json_text(failed_agents, []),
                    stamp,
                    stamp,
                    redact_text(str(error.get("message") or "retry limit exhausted")),
                    task["id"],
                ),
            )
            database.execute(
                """
                UPDATE task_results SET status='rejected'
                WHERE task_id=? AND attempt=? AND status IN ('submitted', 'verified')
                """,
                (task["id"], current_attempt),
            )
            self._release_artifacts(database, int(task["id"]))
            final_status = "failed"
        else:
            next_time = after(
                float(task.get("retry_backoff_seconds") or 0)
                * (2 ** max(current_attempt - 1, 0))
            )
            database.execute(
                """
                UPDATE tasks
                SET status='retrying', error_json=?, failed_agents_json=?,
                    next_attempt_at=?, updated_at=?, waiting_reason=?,
                    assigned_agent=CASE WHEN ? THEN NULL ELSE assigned_agent END,
                    ack_at=NULL, started_at=NULL, sent_at=NULL, result_received_at=NULL,
                    lease_owner=NULL, lease_expires_at=NULL,
                    last_heartbeat_at=NULL, last_active_agent=NULL,
                    verification_status='revision_required'
                WHERE id=?
                """,
                (
                    json_text(error, {}),
                    json_text(failed_agents, []),
                    next_time,
                    stamp,
                    redact_text(str(error.get("message") or "retry scheduled")),
                    1 if should_reassign else 0,
                    task["id"],
                ),
            )
            database.execute(
                """
                UPDATE task_results SET status='superseded'
                WHERE task_id=? AND attempt=? AND status IN ('submitted', 'verified')
                """,
                (task["id"], current_attempt),
            )
            final_status = "retrying"
        run_id = task.get("run_id") or None
        self._event(
            database,
            event_type,
            actor="orchestrator",
            run_id=run_id,
            task_id=int(task["id"]),
            payload={"status": final_status, "reason": error, "attempt": current_attempt},
        )
        return final_status

    def dispatch_runnable(self, run_id: str | None = None) -> list[dict[str, Any]]:
        dispatched: list[dict[str, Any]] = []
        with self.connect() as database:
            query = """
                SELECT * FROM tasks
                WHERE status IN ('pending','retrying','waiting_agent','waiting_dependency')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            """
            values: list[Any] = [utc_now()]
            if run_id:
                query += " AND run_id=?"
                values.append(run_id)
            query += " ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, id"
            task_ids = [row["id"] for row in database.execute(query, values)]

        for task_id in task_ids:
            try:
                with self.transaction() as database:
                    task_row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                    if task_row is None:
                        continue
                    task = dict(task_row)
                    if task["status"] not in {"pending", "retrying", "waiting_agent", "waiting_dependency"}:
                        continue
                    dependencies = self._dependencies(database, int(task["id"]))
                    if any(item["status"] in {"failed", "blocked", "cancelled"} for item in dependencies):
                        database.execute(
                            "UPDATE tasks SET status='blocked', waiting_reason=?, updated_at=? WHERE id=?",
                            ("dependency failed", utc_now(), task["id"]),
                        )
                        self._event(
                            database,
                            "task.blocked",
                            actor="orchestrator",
                            run_id=task.get("run_id") or None,
                            task_id=task["id"],
                            payload={"reason": "dependency failed"},
                        )
                        continue
                    if any(item["status"] != "completed" for item in dependencies):
                        database.execute(
                            "UPDATE tasks SET status='waiting_dependency', waiting_reason=?, updated_at=? WHERE id=?",
                            ("waiting for prerequisite tasks", utc_now(), task["id"]),
                        )
                        continue
                    available, reason = self._artifacts_available(database, task)
                    if not available:
                        database.execute(
                            "UPDATE tasks SET status='waiting_dependency', waiting_reason=?, updated_at=? WHERE id=?",
                            (reason, utc_now(), task["id"]),
                        )
                        continue
                    if int(task.get("attempt") or 0) >= int(task.get("max_retries") or 0) + 1:
                        self._queue_retry_locked(database, task, {"message": "retry limit exhausted"})
                        continue
                    failed_agents = set(json_value(task.get("failed_agents_json"), []))
                    agent = self._select_agent(database, task, failed_agents)
                    if agent is None:
                        database.execute(
                            "UPDATE tasks SET status='waiting_agent', waiting_reason=?, updated_at=? WHERE id=?",
                            ("no healthy capable agent is available", utc_now(), task["id"]),
                        )
                        self._event(
                            database,
                            "task.waiting_agent",
                            actor="orchestrator",
                            run_id=task.get("run_id") or None,
                            task_id=task["id"],
                            payload={"required_capabilities": json_value(task.get("required_capabilities_json"), [])},
                        )
                        continue
                    agent_name = str(agent["name"])
                    attempt = int(task.get("attempt") or 0) + 1
                    stamp = utc_now()
                    database.execute(
                        """
                        UPDATE tasks
                        SET assigned_agent=?, assigned_provider=?, assigned_model=?,
                            status='sent', attempt=?, sent_at=?, updated_at=?,
                            waiting_reason=NULL, next_attempt_at=NULL,
                            verification_status='pending'
                        WHERE id=?
                        """,
                        (
                            agent_name,
                            redact_text(agent.get("provider") or ""),
                            redact_text(agent.get("model") or ""),
                            attempt,
                            stamp,
                            stamp,
                            task["id"],
                        ),
                    )
                    fresh = dict(database.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
                    self._acquire_artifacts(database, fresh, agent_name)
                    payload = self._worker_payload(database, fresh)
                    lead = fresh.get("lead_agent") or fresh.get("owner_agent") or "orchestrator"
                    message = self._insert_message(
                        database,
                        from_agent=lead,
                        to_agent=agent_name,
                        task_id=int(fresh["id"]),
                        subject=f"TASK_REQUEST: {fresh['title']}",
                        body=f"Execute delegated task {fresh.get('task_key') or fresh['id']}.",
                        message_type="TASK_REQUEST",
                        payload=payload,
                        correlation_id=fresh.get("correlation_id") or fresh.get("task_key"),
                        conversation_id=fresh.get("conversation_id") or fresh.get("run_id"),
                        attempt=attempt,
                        max_attempts=int(fresh.get("max_retries") or 0) + 1,
                        idempotency_key=f"{fresh.get('idempotency_key') or fresh.get('task_key')}:{attempt}",
                        status="queued",
                    )
                    self._event(
                        database,
                        "task.assigned",
                        actor="orchestrator",
                        run_id=fresh.get("run_id") or None,
                        task_id=fresh["id"],
                        message_id=message["id"],
                        payload={
                            "agent_id": agent_name,
                            "provider": agent.get("provider") or "",
                            "model": agent.get("model") or "",
                            "attempt": attempt,
                        },
                    )
                    dispatched.append(
                        {
                            "task": self.decorate_task(fresh),
                            "message": self.decorate_message(message),
                        }
                    )
                    self.sync_task(fresh)
                    self.sync_message(message)
            except (sqlite3.IntegrityError, MeshError):
                # Another dispatcher may have acquired the artifact or task.
                # The next reconciliation pass will make the state visible.
                continue
        return dispatched

    def poll_tasks(self, agent: str, limit: int = 1) -> list[dict[str, Any]]:
        agent = nonempty_text(agent, "agent", 200)
        limit = min(max(int(limit), 1), self.settings.max_parallel)
        stamp = utc_now()
        result: list[dict[str, Any]] = []
        with self.transaction() as database:
            row = database.execute("SELECT * FROM agents WHERE name=?", (agent,)).fetchone()
            if row is None:
                raise MeshError("agent is not registered", 404)
            database.execute(
                "UPDATE agents SET last_seen_at=?, status='active', health='online' WHERE name=?",
                (stamp, agent),
            )
            rows = database.execute(
                """
                SELECT m.*, t.*
                FROM messages m JOIN tasks t ON t.id=m.task_id
                WHERE m.to_agent=? AND m.message_type='TASK_REQUEST'
                  AND m.status IN ('queued','delivered')
                  AND (m.available_at IS NULL OR m.available_at <= ?)
                  AND (m.lease_expires_at IS NULL OR m.lease_expires_at <= ?)
                  AND t.status='sent'
                ORDER BY CASE t.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                         m.created_at, m.id
                LIMIT ?
                """,
                (agent, stamp, stamp, limit),
            ).fetchall()
            for joined in rows:
                message_id = int(joined["id"])
                task_id = int(joined["task_id"])
                lease = after(float(joined["execution_timeout_seconds"] or self.settings.execution_timeout))
                database.execute(
                    """
                    UPDATE messages
                    SET status='delivered', delivered_at=?, lease_owner=?, lease_expires_at=?
                    WHERE id=? AND status IN ('queued','delivered')
                    """,
                    (stamp, agent, lease, message_id),
                )
                message = self._dict(database.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())
                task = self._dict(database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
                if message is None or task is None:
                    continue
                self._event(
                    database,
                    "task.delivered",
                    actor=agent,
                    run_id=task.get("run_id") or None,
                    task_id=task_id,
                    message_id=message_id,
                    payload={"agent_id": agent},
                )
                result.append(
                    {
                        "task": self.decorate_task(task),
                        "message": self.decorate_message(message),
                        "execution": json_value(message.get("payload_json"), {}),
                    }
                )
        return result

    def _validate_agent_for_task(self, task: dict[str, Any], agent: Any) -> str:
        name = nonempty_text(agent or task.get("assigned_agent"), "agent", 200)
        if task.get("assigned_agent") and task["assigned_agent"] != name:
            raise MeshError("agent is not assigned to this task", 409)
        return name

    def acknowledge_task(
        self, reference: Any, data: dict[str, Any]
    ) -> dict[str, Any]:
        with self.transaction() as database:
            task = self._task_row(database, reference)
            agent = self._validate_agent_for_task(task, data.get("agent") or data.get("agent_id"))
            accepted = bool(data.get("accepted", True))
            message_id = data.get("message_id")
            stamp = utc_now()
            if task["status"] in TASK_TERMINAL:
                return self.decorate_task(task)
            if accepted and task["status"] in {"acknowledged", "running", "verifying"}:
                return self.decorate_task(task)
            if task["status"] != "sent":
                raise MeshError(
                    f"task cannot be acknowledged from state {task['status']}",
                    409,
                )
            if message_id is not None:
                request_message = database.execute(
                    """
                    SELECT * FROM messages
                    WHERE id=? AND task_id=? AND to_agent=?
                      AND message_type='TASK_REQUEST' AND attempt=?
                    """,
                    (int(message_id), task["id"], agent, task["attempt"]),
                ).fetchone()
            else:
                request_message = database.execute(
                    """
                    SELECT * FROM messages
                    WHERE task_id=? AND to_agent=?
                      AND message_type='TASK_REQUEST' AND attempt=?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (task["id"], agent, task["attempt"]),
                ).fetchone()
            if request_message is None:
                raise MeshError("TASK_REQUEST message not found for this attempt", 409)
            if request_message["status"] not in {"queued", "delivered"}:
                raise MeshError("TASK_REQUEST is no longer awaiting acknowledgement", 409)
            if not accepted:
                database.execute(
                    "UPDATE messages SET status='rejected', error=?, completed_at=? WHERE id=?",
                    ("agent rejected task", stamp, request_message["id"]),
                )
                self._queue_retry_locked(
                    database,
                    task,
                    {"message": data.get("reason") or "agent rejected task"},
                    reassign=True,
                    event_type="task.rejected",
                )
                updated = dict(database.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
            else:
                database.execute(
                    """
                    UPDATE messages
                    SET status='acknowledged', acknowledged_at=?, lease_owner=?, lease_expires_at=?
                    WHERE id=?
                    """,
                    (
                        stamp,
                        agent,
                        after(float(task.get("execution_timeout_seconds") or self.settings.execution_timeout)),
                        request_message["id"],
                    ),
                )
                database.execute(
                    """
                    UPDATE tasks
                    SET status='acknowledged', ack_at=?, last_heartbeat_at=?,
                        last_active_agent=?, updated_at=?
                    WHERE id=?
                    """,
                    (stamp, stamp, agent, stamp, task["id"]),
                )
                database.execute(
                    "UPDATE agents SET last_seen_at=?, health='online', status='active' WHERE name=?",
                    (stamp, agent),
                )
                updated = dict(database.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
                lead = updated.get("lead_agent") or updated.get("owner_agent")
                if lead and lead != agent:
                    ack_message = self._insert_message(
                        database,
                        from_agent=agent,
                        to_agent=lead,
                        task_id=updated["id"],
                        subject=f"TASK_ACK: {updated['title']}",
                        body=f"Task {updated.get('task_key') or updated['id']} acknowledged.",
                        message_type="TASK_ACK",
                        payload={
                            "task_id": updated.get("task_key") or updated["id"],
                            "accepted": True,
                            "agent_id": agent,
                            "attempt": updated.get("attempt"),
                        },
                        correlation_id=updated.get("correlation_id"),
                        conversation_id=updated.get("conversation_id"),
                        idempotency_key=f"{updated.get('task_key')}:ack:{updated.get('attempt')}",
                    )
                    self._event(
                        database,
                        "task.acknowledged",
                        actor=agent,
                        run_id=updated.get("run_id") or None,
                        task_id=updated["id"],
                        message_id=ack_message["id"],
                        payload={"accepted": True},
                    )
                else:
                    self._event(
                        database,
                        "task.acknowledged",
                        actor=agent,
                        run_id=updated.get("run_id") or None,
                        task_id=updated["id"],
                        payload={"accepted": True},
                    )
        self.sync_task(updated)
        if not accepted:
            self.dispatch_runnable(task.get("run_id") or None)
        self.reconcile_run(task.get("run_id"))
        return self.decorate_task(updated)

    def task_progress(self, reference: Any, data: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as database:
            task = self._task_row(database, reference)
            previous_status = task["status"]
            agent = self._validate_agent_for_task(task, data.get("agent") or data.get("agent_id"))
            if task["status"] in TASK_TERMINAL:
                raise MeshError(f"task is already {task['status']}", 409)
            if task["status"] == "sent":
                raise MeshError("task must be acknowledged before progress is reported", 409)
            legacy_progress = not task.get("run_id") and task["status"] in {
                "pending",
                "retrying",
                "waiting_agent",
            }
            if task["status"] not in {"acknowledged", "running"} and not legacy_progress:
                raise MeshError(
                    f"task cannot report progress from state {task['status']}",
                    409,
                )
            stamp = utc_now()
            database.execute(
                """
                UPDATE tasks SET status='running', started_at=COALESCE(started_at, ?),
                    last_heartbeat_at=?, last_active_agent=?, updated_at=?
                WHERE id=? AND status NOT IN ('completed','failed','blocked','cancelled')
                """,
                (stamp, stamp, agent, stamp, task["id"]),
            )
            database.execute(
                "UPDATE agents SET last_seen_at=?, health='online', status='active' WHERE name=?",
                (stamp, agent),
            )
            updated = dict(database.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
            lead = updated.get("lead_agent") or updated.get("owner_agent")
            message = None
            if lead and lead != agent:
                message = self._insert_message(
                    database,
                    from_agent=agent,
                    to_agent=lead,
                    task_id=updated["id"],
                    subject=f"TASK_PROGRESS: {updated['title']}",
                    body=redact_text(data.get("summary") or data.get("message") or "Task in progress."),
                    message_type="TASK_PROGRESS",
                    payload={
                        "task_id": updated.get("task_key") or updated["id"],
                        "progress": data.get("progress"),
                        "summary": data.get("summary") or data.get("message"),
                    },
                    correlation_id=updated.get("correlation_id"),
                    conversation_id=updated.get("conversation_id"),
                )
            self._event(
                database,
                "task.progress",
                actor=agent,
                run_id=updated.get("run_id") or None,
                task_id=updated["id"],
                message_id=message["id"] if message else None,
                payload={"progress": data.get("progress"), "summary": data.get("summary")},
            )
            if previous_status != "running":
                self._event(
                    database,
                    "task.started",
                    actor=agent,
                    run_id=updated.get("run_id") or None,
                    task_id=updated["id"],
                )
        self.sync_task(updated)
        return self.decorate_task(updated)

    def heartbeat_task(self, reference: Any, data: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as database:
            task = self._task_row(database, reference)
            agent = self._validate_agent_for_task(task, data.get("agent") or data.get("agent_id"))
            if task["status"] in TASK_TERMINAL:
                raise MeshError(f"task is already {task['status']}", 409)
            legacy_heartbeat = not task.get("run_id")
            if task["status"] not in TASK_ACTIVE and not legacy_heartbeat:
                raise MeshError(
                    f"task cannot receive a heartbeat from state {task['status']}",
                    409,
                )
            stamp = utc_now()
            database.execute(
                """
                UPDATE tasks SET last_heartbeat_at=?, last_active_agent=?,
                    lease_expires_at=?, updated_at=?
                WHERE id=?
                """,
                (
                    stamp,
                    agent,
                    after(float(task.get("execution_timeout_seconds") or self.settings.execution_timeout)),
                    stamp,
                    task["id"],
                ),
            )
            database.execute(
                "UPDATE agents SET last_seen_at=?, health='online', status='active' WHERE name=?",
                (stamp, agent),
            )
            updated = dict(database.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
            self._event(
                database,
                "agent.heartbeat",
                actor=agent,
                run_id=updated.get("run_id") or None,
                task_id=updated["id"],
            )
        self.sync_task(updated)
        return self.decorate_task(updated)

    def _validate_result(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise MeshError("result must be an object")
        summary = str(result.get("summary") or "").strip()
        if not summary:
            raise MeshError("result.summary is required")
        result = dict(result)
        result["summary"] = summary
        for field in (
            "files_changed",
            "files_created",
            "commands_executed",
            "tests",
            "warnings",
            "errors",
            "handoff_notes",
        ):
            if field in result and not isinstance(result[field], list):
                raise MeshError(f"result.{field} must be a list")
        return sanitize(result)

    def submit_result(self, reference: Any, data: dict[str, Any]) -> dict[str, Any]:
        result = self._validate_result(data.get("result") or data.get("payload"))
        with self.transaction() as database:
            task = self._task_row(database, reference)
            agent = self._validate_agent_for_task(task, data.get("agent") or data.get("agent_id"))
            key = redact_text(
                data.get("idempotency_key")
                or f"{task.get('task_key')}:attempt:{task.get('attempt')}"
            )
            duplicate = database.execute(
                "SELECT * FROM task_results WHERE task_id=? AND idempotency_key=?",
                (task["id"], key),
            ).fetchone()
            if duplicate is not None:
                return self.decorate_task(task)
            if task["status"] in TASK_TERMINAL:
                raise MeshError(f"task is already {task['status']}", 409)
            if task.get("run_id") and task["status"] not in {"acknowledged", "running"}:
                raise MeshError(
                    f"task result requires an acknowledged or running task, not {task['status']}",
                    409,
                )
            stamp = utc_now()
            database.execute(
                """
                INSERT INTO task_results
                (task_id, task_key, attempt, agent_id, provider, model, status,
                 result_json, idempotency_key, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?, ?, ?)
                """,
                (
                    task["id"],
                    task.get("task_key"),
                    task.get("attempt") or 0,
                    agent,
                    task.get("assigned_provider") or "",
                    task.get("assigned_model") or "",
                    json_text(result, {}),
                    key,
                    stamp,
                ),
            )
            database.execute(
                """
                UPDATE tasks SET status='verifying', result_json=?,
                    result_received_at=?, verification_status='pending',
                    updated_at=?, lease_expires_at=NULL
                WHERE id=?
                """,
                (json_text(result, {}), stamp, stamp, task["id"]),
            )
            database.execute(
                """
                UPDATE messages SET status='result_received', completed_at=?
                WHERE task_id=? AND message_type='TASK_REQUEST' AND attempt=?
                """,
                (stamp, task["id"], task["attempt"]),
            )
            database.execute(
                "UPDATE agents SET last_seen_at=?, health='online', status='active' WHERE name=?",
                (stamp, agent),
            )
            updated = dict(database.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
            lead = updated.get("lead_agent") or updated.get("owner_agent")
            result_message = None
            if lead and lead != agent:
                result_message = self._insert_message(
                    database,
                    from_agent=agent,
                    to_agent=lead,
                    task_id=updated["id"],
                    subject=f"TASK_RESULT: {updated['title']}",
                    body=f"Result submitted for {updated.get('task_key') or updated['id']}; verification required.",
                    message_type="TASK_RESULT",
                    payload={
                        "task_id": updated.get("task_key") or updated["id"],
                        "agent_id": agent,
                        "provider": updated.get("assigned_provider") or "",
                        "model": updated.get("assigned_model") or "",
                        "result": result,
                    },
                    correlation_id=updated.get("correlation_id"),
                    conversation_id=updated.get("conversation_id"),
                    idempotency_key=f"{key}:result-message",
                )
            self._event(
                database,
                "task.result_submitted",
                actor=agent,
                run_id=updated.get("run_id") or None,
                task_id=updated["id"],
                message_id=result_message["id"] if result_message else None,
                payload={"idempotency_key": key},
            )
        self.sync_task(updated)
        if result_message:
            self.sync_message(result_message)
        self.reconcile_run(task.get("run_id"))
        return self.decorate_task(updated)

    def fail_task(self, reference: Any, data: dict[str, Any]) -> dict[str, Any]:
        error = data.get("error") or {"message": data.get("message") or "agent reported task failure"}
        if isinstance(error, str):
            error = {"message": error}
        error = sanitize(error)
        with self.transaction() as database:
            task = self._task_row(database, reference)
            agent = self._validate_agent_for_task(task, data.get("agent") or data.get("agent_id"))
            self._queue_retry_locked(
                database,
                task,
                error,
                reassign=bool(data.get("reassign", True)),
                event_type="task.failed",
            )
            updated = dict(database.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
            lead = updated.get("lead_agent") or updated.get("owner_agent")
            error_message = None
            if lead and lead != agent:
                error_message = self._insert_message(
                    database,
                    from_agent=agent,
                    to_agent=lead,
                    task_id=updated["id"],
                    subject=f"TASK_ERROR: {updated['title']}",
                    body=redact_text(str(error.get("message") or "Task failed.")),
                    message_type="TASK_ERROR",
                    payload={"task_id": updated.get("task_key") or updated["id"], "error": error},
                    correlation_id=updated.get("correlation_id"),
                    conversation_id=updated.get("conversation_id"),
                )
        self.sync_task(updated)
        if error_message:
            self.sync_message(error_message)
        self.dispatch_runnable(task.get("run_id") or None)
        self.reconcile_run(task.get("run_id"))
        return self.decorate_task(updated)

    def verify_task(self, reference: Any, data: dict[str, Any]) -> dict[str, Any]:
        valid = bool(data.get("valid"))
        revision = data.get("revision_instructions") or data.get("problem") or ""
        with self.transaction() as database:
            task = self._task_row(database, reference)
            actor = data.get("verified_by") or data.get("agent") or task.get("lead_agent") or "orchestrator"
            stamp = utc_now()
            if task["status"] == "completed" and valid:
                return self.decorate_task(task)
            if task["status"] in TASK_TERMINAL:
                raise MeshError(f"task is already {task['status']}", 409)
            if task["status"] != "verifying" or not task.get("result_json"):
                raise MeshError(
                    "task verification requires a submitted result",
                    409,
                )
            if valid:
                database.execute(
                    """
                    UPDATE tasks SET status='completed', verification_status='accepted',
                        verified_at=?, updated_at=?, completed_at=?
                    WHERE id=?
                    """,
                    (stamp, stamp, stamp, task["id"]),
                )
                database.execute(
                    """
                    UPDATE task_results SET status='verified', verified_at=?
                    WHERE id=(SELECT id FROM task_results WHERE task_id=? ORDER BY id DESC LIMIT 1)
                    """,
                    (stamp, task["id"]),
                )
                self._release_artifacts(database, int(task["id"]))
                self._event(
                    database,
                    "task.verified",
                    actor=actor,
                    run_id=task.get("run_id") or None,
                    task_id=task["id"],
                    payload={"valid": True},
                )
                self._event(
                    database,
                    "task.completed",
                    actor=actor,
                    run_id=task.get("run_id") or None,
                    task_id=task["id"],
                )
            else:
                reason = {"message": redact_text(str(revision or "verification failed"))}
                if data.get("expected") is not None:
                    reason["expected"] = sanitize(data["expected"])
                if data.get("actual") is not None:
                    reason["actual"] = sanitize(data["actual"])
                if bool(data.get("retry", True)) and int(task.get("attempt") or 0) < int(task.get("max_retries") or 0) + 1:
                    self._queue_retry_locked(
                        database,
                        task,
                        reason,
                        reassign=bool(data.get("reassign", False)),
                        event_type="task.revision_requested",
                    )
                else:
                    database.execute(
                        """
                        UPDATE tasks SET status='failed', verification_status='rejected',
                            error_json=?, failed_at=?, updated_at=?, waiting_reason=?
                        WHERE id=?
                        """,
                        (json_text(reason, {}), stamp, stamp, reason["message"], task["id"]),
                    )
                    self._release_artifacts(database, int(task["id"]))
                    self._event(
                        database,
                        "task.verification_failed",
                        actor=actor,
                        run_id=task.get("run_id") or None,
                        task_id=task["id"],
                        payload=reason,
                    )
            updated = dict(database.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
        self.sync_task(updated)
        self.dispatch_runnable(task.get("run_id") or None)
        self.reconcile_run(task.get("run_id"))
        return self.decorate_task(updated)

    def cancel_task(self, reference: Any, actor: str = "orchestrator") -> dict[str, Any]:
        cancel_message = None
        with self.transaction() as database:
            task = self._task_row(database, reference)
            if task["status"] not in TASK_TERMINAL:
                database.execute(
                    """
                    UPDATE tasks SET status='cancelled', waiting_reason='cancelled',
                        updated_at=?, failed_at=?
                    WHERE id=?
                    """,
                    (utc_now(), utc_now(), task["id"]),
                )
                database.execute(
                    """
                    UPDATE messages SET status='cancelled', completed_at=?
                    WHERE task_id=? AND status IN ('queued','sent','delivered','acknowledged')
                    """,
                    (utc_now(), task["id"]),
                )
                if task.get("assigned_agent"):
                    cancel_message = self._insert_message(
                        database,
                        from_agent=actor,
                        to_agent=task["assigned_agent"],
                        task_id=task["id"],
                        subject=f"TASK_CANCEL: {task['title']}",
                        body=f"Cancel task {task.get('task_key') or task['id']}.",
                        message_type="TASK_CANCEL",
                        payload={
                            "task_id": task.get("task_key") or task["id"],
                            "reason": "cancelled by orchestrator",
                        },
                        correlation_id=task.get("correlation_id"),
                        conversation_id=task.get("conversation_id"),
                        idempotency_key=f"{task.get('task_key')}:cancel",
                    )
                self._release_artifacts(database, int(task["id"]))
                self._event(
                    database,
                    "task.cancelled",
                    actor=actor,
                    run_id=task.get("run_id") or None,
                    task_id=task["id"],
                    message_id=cancel_message["id"] if cancel_message else None,
                )
            updated = dict(database.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
        self.sync_task(updated)
        if cancel_message:
            self.sync_message(cancel_message)
        self.reconcile_run(task.get("run_id"))
        return self.decorate_task(updated)

    def claim_legacy_task(self, reference: Any, data: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as database:
            task = self._task_row(database, reference)
            agent = nonempty_text(
                data.get("agent") or data.get("agent_name") or data.get("lease_owner"),
                "agent",
                200,
            )
            seconds = int(data.get("lease_seconds", int(data.get("lease_hours", 1)) * 3600))
            expiry = after(max(seconds, 1))
            database.execute(
                """
                UPDATE tasks SET lease_owner=?, lease_expires_at=?, last_active_agent=?,
                    last_heartbeat_at=?, updated_at=?,
                    assigned_agent=COALESCE(assigned_agent, ?)
                WHERE id=?
                """,
                (agent, expiry, agent, utc_now(), utc_now(), agent, task["id"]),
            )
            updated = dict(database.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
            self._event(
                database,
                "task.claimed",
                actor=agent,
                run_id=updated.get("run_id") or None,
                task_id=updated["id"],
            )
        self.sync_task(updated)
        return self.decorate_task(updated)

    def release_task(self, reference: Any) -> dict[str, Any]:
        with self.transaction() as database:
            task = self._task_row(database, reference)
            database.execute(
                "UPDATE tasks SET lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
                (utc_now(), task["id"]),
            )
            updated = dict(database.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
        self.sync_task(updated)
        return self.decorate_task(updated)

    def _validate_task_graph(self, plans: list[dict[str, Any]]) -> dict[str, list[str]]:
        keys = [str(plan.get("task_id") or plan.get("task_key") or f"task_{index + 1}") for index, plan in enumerate(plans)]
        if len(set(keys)) != len(keys):
            raise MeshError("task IDs must be unique")
        graph: dict[str, list[str]] = {}
        for key, plan in zip(keys, plans):
            dependencies = plan.get("dependencies") or []
            if isinstance(dependencies, str):
                dependencies = [dependencies]
            if not isinstance(dependencies, list):
                raise MeshError(f"dependencies for {key} must be a list")
            graph[key] = [str(item) for item in dependencies]
            for dependency in graph[key]:
                if dependency not in keys:
                    raise MeshError(f"unknown dependency {dependency} for {key}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise MeshError("task dependency graph contains a cycle")
            if key in visited:
                return
            visiting.add(key)
            for dependency in graph[key]:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in keys:
            visit(key)
        return graph

    def create_run(self, data: dict[str, Any]) -> dict[str, Any]:
        request = nonempty_text(data.get("request") or data.get("goal"), "request", 50000)
        lead = nonempty_text(data.get("lead_agent") or data.get("from_agent") or "orchestrator", "lead_agent", 200)
        plan = data.get("plan") if isinstance(data.get("plan"), dict) else data
        plans = plan.get("tasks") or []
        if not isinstance(plans, list):
            raise MeshError("tasks must be a list")
        run_id = redact_text(data.get("run_id") or str(uuid.uuid4()))
        max_depth = int(data.get("max_delegation_depth", self.settings.max_delegation_depth))
        if max_depth < 0 or max_depth > self.settings.max_delegation_depth:
            raise MeshError("max_delegation_depth exceeds configured limit")
        graph = self._validate_task_graph(plans) if plans else {}
        keys = list(graph.keys())
        stamp = utc_now()
        task_rows: list[dict[str, Any]] = []
        with self.transaction() as database:
            existing = database.execute("SELECT * FROM orchestration_runs WHERE id=?", (run_id,)).fetchone()
            if existing is not None:
                return self.get_run(run_id)
            database.execute(
                """
                INSERT INTO orchestration_runs
                (id, request, lead_agent, state, plan_json, metadata_json,
                 max_delegation_depth, created_at, updated_at)
                VALUES (?, ?, ?, 'RECEIVED', ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    request,
                    lead,
                    json_text(plan, {}),
                    json_text(data.get("metadata") or {}, {}),
                    max_depth,
                    stamp,
                    stamp,
                ),
            )
            self._event(
                database,
                "orchestration.started",
                actor=lead,
                run_id=run_id,
                payload={"lead_agent": lead},
            )
            database.execute(
                "UPDATE orchestration_runs SET state='PLANNING', updated_at=? WHERE id=?",
                (utc_now(), run_id),
            )
            self._event(database, "orchestration.planning", actor=lead, run_id=run_id)
            task_id_by_key: dict[str, int] = {}
            parent_refs: dict[str, Any] = {}
            depths: dict[str, int] = {}
            for index, task_plan in enumerate(plans):
                if not isinstance(task_plan, dict):
                    raise MeshError("each task plan must be an object")
                key = keys[index]
                depth = int(task_plan.get("delegation_depth", 0))
                if depth > max_depth:
                    raise MeshError(f"task {key} exceeds max_delegation_depth")
                if depth < 0:
                    raise MeshError(f"task {key} has an invalid delegation_depth")
                dependencies = graph[key]
                row = self._insert_task(
                    database,
                    title=task_plan.get("title") or key,
                    owner_agent=lead,
                    run_id=run_id,
                    task_key=key,
                    created_by=lead,
                    lead_agent=lead,
                    description=task_plan.get("description") or task_plan.get("task") or "",
                    assigned_agent=task_plan.get("assigned_to") or task_plan.get("assigned_agent"),
                    required_capabilities=task_plan.get("required_capabilities") or task_plan.get("capabilities"),
                    dependencies=dependencies,
                    artifact_paths=task_plan.get("artifact_paths") or task_plan.get("relevant_files"),
                    candidate_agents=task_plan.get("candidate_agents"),
                    task_type=task_plan.get("task_type") or "work",
                    input_data=task_plan.get("input") or {},
                    acceptance_criteria=task_plan.get("acceptance_criteria") or "",
                    interfaces=task_plan.get("interfaces") or {},
                    constraints=task_plan.get("constraints") or "",
                    priority=task_plan.get("priority") or "medium",
                    max_retries=task_plan.get("max_retries"),
                    ack_timeout=task_plan.get("ack_timeout"),
                    execution_timeout=task_plan.get("execution_timeout"),
                    retry_backoff=task_plan.get("retry_backoff"),
                    idempotency_key=task_plan.get("idempotency_key") or f"{run_id}:{key}",
                    correlation_id=task_plan.get("correlation_id") or f"{run_id}:{key}",
                    conversation_id=run_id,
                    reassign_on_retry=bool(task_plan.get("reassign_on_retry", True)),
                    delegation_depth=depth,
                    project=task_plan.get("project") or data.get("project"),
                    context_path=task_plan.get("context_path"),
                    result_path=task_plan.get("result_path"),
                )
                task_rows.append(row)
                task_id_by_key[key] = int(row["id"])
                parent_refs[key] = task_plan.get("parent_task_id")
                depths[key] = depth
            for row in task_rows:
                key = str(row["task_key"])
                parent_ref = parent_refs.get(key)
                if parent_ref in (None, ""):
                    continue
                parent_text = str(parent_ref)
                if parent_text in task_id_by_key:
                    parent_id = task_id_by_key[parent_text]
                    if depths[key] <= depths[parent_text]:
                        raise MeshError(
                            f"task {key} must have a greater delegation_depth than its parent"
                        )
                elif parent_text.isdigit():
                    parent_id = int(parent_text)
                    if database.execute("SELECT 1 FROM tasks WHERE id=?", (parent_id,)).fetchone() is None:
                        raise MeshError(f"parent task {parent_ref} for {key} was not found")
                else:
                    raise MeshError(f"parent task {parent_ref} for {key} was not found")
                if parent_id == int(row["id"]):
                    raise MeshError(f"task {key} cannot be its own parent")
                database.execute(
                    "UPDATE tasks SET parent_task_id=? WHERE id=?",
                    (parent_id, row["id"]),
                )
                row["parent_task_id"] = parent_id
            for row in task_rows:
                key = row["task_key"]
                for dependency in graph[key]:
                    database.execute(
                        "INSERT INTO task_dependencies(task_id, depends_on_task_id) VALUES (?, ?)",
                        (row["id"], task_id_by_key[dependency]),
                    )
                self._event(
                    database,
                    "task.created",
                    actor=lead,
                    run_id=run_id,
                    task_id=row["id"],
                    payload={
                        "task_key": key,
                        "task_type": row.get("task_type"),
                        "parent_task_id": row.get("parent_task_id"),
                    },
                )
            database.execute(
                "UPDATE orchestration_runs SET state='DELEGATING', updated_at=? WHERE id=?",
                (utc_now(), run_id),
            )
            self._event(database, "orchestration.delegating", actor=lead, run_id=run_id)
        for row in task_rows:
            self.sync_task(row)
        self.dispatch_runnable(run_id)
        self.reconcile_run(run_id)
        return self.get_run(run_id)

    def _run_row(self, database: sqlite3.Connection, run_id: str) -> dict[str, Any]:
        row = database.execute("SELECT * FROM orchestration_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise MeshError("orchestration run not found", 404)
        return dict(row)

    def reconcile_run(self, run_id: str | None) -> dict[str, Any] | None:
        if not run_id:
            return None
        with self.transaction() as database:
            run = self._run_row(database, str(run_id))
            tasks = [dict(row) for row in database.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY id", (run_id,))]
            previous = run["state"]
            if not tasks:
                state = "PLANNING"
            else:
                statuses = {task["status"] for task in tasks}
                if statuses and statuses <= {"completed"}:
                    state = "COMPLETED"
                elif "cancelled" in statuses and statuses <= TASK_TERMINAL:
                    state = "CANCELLED"
                elif "failed" in statuses:
                    state = "PARTIALLY_FAILED" if "completed" in statuses else "FAILED"
                elif "blocked" in statuses:
                    state = "BLOCKED"
                elif statuses & {"verifying"}:
                    state = "VERIFYING"
                elif statuses & {"sent", "acknowledged", "running"}:
                    state = "EXECUTING"
                elif statuses & {"pending", "retrying", "waiting_agent", "waiting_dependency"}:
                    state = "WAITING"
                else:
                    state = "DELEGATING"
            completed_at = utc_now() if state in RUN_TERMINAL else None
            failure_reason = None
            if state in {"FAILED", "PARTIALLY_FAILED", "BLOCKED"}:
                failure_reason = "one or more delegated tasks did not reach verified completion"
            database.execute(
                """
                UPDATE orchestration_runs SET state=?, updated_at=?,
                    completed_at=COALESCE(completed_at, ?), failure_reason=?
                WHERE id=?
                """,
                (state, utc_now(), completed_at, failure_reason, run_id),
            )
            if previous != state:
                self._event(
                    database,
                    "orchestration." + state.lower(),
                    actor="orchestrator",
                    run_id=run_id,
                    payload={"previous_state": previous, "state": state},
                )
                if state == "COMPLETED":
                    self._event(database, "orchestration.completed", actor="orchestrator", run_id=run_id)
        return self.get_run(str(run_id))

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as database:
            run = self._run_row(database, str(run_id))
            tasks = [
                self.decorate_task(dict(row))
                for row in database.execute(
                    "SELECT * FROM tasks WHERE run_id=? ORDER BY id", (str(run_id),)
                )
            ]
            events = [
                dict(row)
                for row in database.execute(
                    """
                    SELECT * FROM orchestration_events WHERE run_id=?
                    ORDER BY id DESC LIMIT 200
                    """,
                    (str(run_id),),
                )
            ]
        run["plan"] = json_value(run.get("plan_json"), {})
        run["metadata"] = json_value(run.get("metadata_json"), {})
        run["final_result"] = json_value(run.get("final_result_json"), {})
        run["tasks"] = tasks
        run["events"] = list(reversed(events))
        return run

    def list_runs(self) -> list[dict[str, Any]]:
        with self.connect() as database:
            ids = [row["id"] for row in database.execute("SELECT id FROM orchestration_runs ORDER BY updated_at DESC")]
        return [self.get_run(run_id) for run_id in ids]

    def finalize_run(self, run_id: str, data: dict[str, Any]) -> dict[str, Any]:
        supplied = data.get("result") or data.get("final_result")
        if supplied is None and data.get("summary"):
            supplied = {"summary": data["summary"]}
        result = self._validate_result(supplied)
        actor = data.get("finalized_by") or data.get("agent") or "orchestrator"
        with self.transaction() as database:
            run = self._run_row(database, run_id)
            tasks = [
                dict(row)
                for row in database.execute(
                    "SELECT * FROM tasks WHERE run_id=? ORDER BY id", (run_id,)
                )
            ]
            if not tasks:
                raise MeshError("cannot finalize a run without tasks", 409)
            if any(task["status"] != "completed" for task in tasks):
                raise MeshError("all tasks must be verified before finalization", 409)
            stamp = utc_now()
            database.execute(
                """
                UPDATE orchestration_runs
                SET state='COMPLETED', final_result_json=?, finalized_by=?,
                    finalized_at=?, completed_at=COALESCE(completed_at, ?), updated_at=?
                WHERE id=?
                """,
                (json_text(result, {}), redact_text(actor), stamp, stamp, stamp, run_id),
            )
            self._event(
                database,
                "orchestration.integrating",
                actor=actor,
                run_id=run_id,
                payload={"task_count": len(tasks)},
            )
            self._event(
                database,
                "orchestration.finalized",
                actor=actor,
                run_id=run_id,
                payload={"has_final_result": True},
            )
        return self.get_run(run_id)

    def cancel_run(self, run_id: str, actor: str = "orchestrator") -> dict[str, Any]:
        cancel_messages: list[dict[str, Any]] = []
        with self.transaction() as database:
            run = self._run_row(database, run_id)
            tasks = [dict(row) for row in database.execute("SELECT * FROM tasks WHERE run_id=?", (run_id,))]
            for task in tasks:
                if task["status"] not in TASK_TERMINAL:
                    database.execute(
                        "UPDATE tasks SET status='cancelled', waiting_reason='run cancelled', updated_at=? WHERE id=?",
                        (utc_now(), task["id"]),
                    )
                    database.execute(
                        "UPDATE messages SET status='cancelled', completed_at=? WHERE task_id=? AND status IN ('queued','sent','delivered','acknowledged')",
                        (utc_now(), task["id"]),
                    )
                    if task.get("assigned_agent"):
                        cancel_message = self._insert_message(
                            database,
                            from_agent=actor,
                            to_agent=task["assigned_agent"],
                            task_id=task["id"],
                            subject=f"TASK_CANCEL: {task['title']}",
                            body=f"Cancel task {task.get('task_key') or task['id']}.",
                            message_type="TASK_CANCEL",
                            payload={
                                "task_id": task.get("task_key") or task["id"],
                                "reason": "run cancelled by orchestrator",
                            },
                            correlation_id=task.get("correlation_id"),
                            conversation_id=task.get("conversation_id") or run_id,
                            idempotency_key=f"{task.get('task_key')}:cancel",
                        )
                        cancel_messages.append(cancel_message)
                    self._release_artifacts(database, int(task["id"]))
                    self._event(
                        database,
                        "task.cancelled",
                        actor=actor,
                        run_id=run_id,
                        task_id=task["id"],
                        message_id=cancel_message["id"] if task.get("assigned_agent") else None,
                    )
            database.execute(
                "UPDATE orchestration_runs SET state='CANCELLED', updated_at=?, completed_at=? WHERE id=?",
                (utc_now(), utc_now(), run_id),
            )
            self._event(database, "orchestration.cancelled", actor=actor, run_id=run_id)
            updated_tasks = [
                dict(row)
                for row in database.execute(
                    "SELECT * FROM tasks WHERE run_id=? ORDER BY id", (run_id,)
                )
            ]
        for task in updated_tasks:
            self.sync_task(task)
        for message in cancel_messages:
            self.sync_message(message)
        return self.get_run(run_id)

    def reap_timeouts(self) -> int:
        changed = 0
        now_dt = datetime.now(timezone.utc)
        with self.transaction() as database:
            rows = [
                dict(row)
                for row in database.execute(
                    "SELECT * FROM tasks WHERE status IN ('sent','acknowledged','running')"
                )
            ]
            for task in rows:
                reference = parse_time(task.get("sent_at") if task["status"] == "sent" else task.get("ack_at") or task.get("started_at"))
                if reference is None:
                    continue
                timeout = (
                    float(task.get("ack_timeout_seconds") or self.settings.ack_timeout)
                    if task["status"] == "sent"
                    else float(task.get("execution_timeout_seconds") or self.settings.execution_timeout)
                )
                if (now_dt - reference).total_seconds() < timeout:
                    continue
                reason = {
                    "message": "acknowledgement timeout" if task["status"] == "sent" else "execution timeout",
                    "status": task["status"],
                }
                self._queue_retry_locked(database, task, reason, event_type="task.timeout")
                changed += 1
        if changed:
            for run in self.list_runs():
                self.dispatch_runnable(run["id"])
                self.reconcile_run(run["id"])
        return changed

    def health(self) -> dict[str, Any]:
        with self.connect() as database:
            counts = {
                table: database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "agents",
                    "messages",
                    "tasks",
                    "memory",
                    "handoffs",
                    "mcp_servers",
                    "skills",
                    "orchestration_runs",
                    "orchestration_events",
                )
            }
            queue = {
                status: database.execute(
                    "SELECT COUNT(*) FROM messages WHERE message_type='TASK_REQUEST' AND status=?",
                    (status,),
                ).fetchone()[0]
                for status in ("queued", "delivered", "acknowledged", "result_received", "expired")
            }
        return {
            "status": "ok",
            "time": utc_now(),
            "host": self.settings.host,
            "port": self.settings.port,
            "counts": counts,
            "task_queue": queue,
            "protocol": "task-request-v1",
            "orchestration_states": sorted(
                {
                    "RECEIVED",
                    "ANALYZING",
                    "CONSULTING",
                    "PLANNING",
                    "DELEGATING",
                    "EXECUTING",
                    "COLLECTING",
                    "VERIFYING",
                    "INTEGRATING",
                    "FINALIZING",
                    "COMPLETED",
                    "WAITING",
                    "BLOCKED",
                    "PARTIALLY_FAILED",
                    "FAILED",
                    "CANCELLED",
                }
            ),
        }
