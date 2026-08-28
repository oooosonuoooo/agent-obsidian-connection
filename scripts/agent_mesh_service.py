#!/usr/bin/env python3
"""Local HTTP service for the durable Agent Mesh control plane."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from agent_mesh_core import (
    MeshError,
    MeshStore,
    Settings,
    load_env,
    redact_text,
    sanitize,
    utc_now,
)


class MeshHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, settings: Settings):
        self.settings = settings
        self.store = MeshStore(settings)
        super().__init__(address, MeshRequestHandler)


class MeshRequestHandler(BaseHTTPRequestHandler):
    server: MeshHTTPServer

    def log_message(self, fmt: str, *args) -> None:
        # Request bodies can contain private project data.  Do not log them.
        return

    @property
    def store(self) -> MeshStore:
        return self.server.store

    def respond(self, value, status: int = 200) -> None:
        raw = json.dumps(sanitize(value), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def error(self, detail: str, status: int = 400) -> None:
        self.respond({"error": redact_text(detail)}, status)

    def authenticated(self) -> bool:
        path = urlparse(self.path).path
        if path in {"/health", "/mcp/"}:
            return True
        token = self.server.settings.token
        if not token:
            self.error("AGENT_MESH_TOKEN is not configured", 503)
            return False
        if self.headers.get("Authorization", "") != "Bearer " + token:
            self.error("Unauthorized", 401)
            return False
        return True

    def request_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise MeshError("invalid Content-Length") from exc
        if length < 0 or length > self.server.settings.max_body_bytes:
            raise MeshError("request body is too large", 413)
        if length == 0:
            return {}
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MeshError("request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise MeshError("request body must be a JSON object")
        return value

    @staticmethod
    def segments(path: str) -> list[str]:
        return [unquote(part) for part in path.split("/") if part]

    def do_GET(self) -> None:
        if not self.authenticated():
            return
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            parts = self.segments(path)

            if path == "/health":
                health = self.store.health()
                health["vault"] = str(self.server.settings.vault)
                return self.respond(health)

            if path == "/agents":
                return self.respond(self.store.list_agents())
            if len(parts) == 2 and parts[0] == "agents":
                return self.respond(self.store.get_agent(parts[1]))
            if len(parts) == 3 and parts[0] == "agents" and parts[2] == "capabilities":
                agent = self.store.get_agent(parts[1])
                return self.respond(
                    {
                        "agent_id": agent["name"],
                        "name": agent["name"],
                        "capabilities": agent.get("capabilities", {}),
                        "tools": agent.get("metadata", {}).get("tools", []),
                        "provider": agent.get("provider"),
                        "model": agent.get("model"),
                        "health": agent.get("health"),
                    }
                )
            if len(parts) == 3 and parts[0] == "agents" and parts[2] in {"inbox", "messages"}:
                return self.respond(
                    self.store.get_messages(parts[1], (query.get("status") or [None])[0])
                )
            if len(parts) == 3 and parts[0] == "agents" and parts[2] == "tasks":
                tasks = [
                    task
                    for task in self.store.list_tasks()
                    if task.get("assigned_agent") == parts[1]
                ]
                return self.respond(tasks)

            if path == "/messages":
                agent = (query.get("to_agent") or [None])[0]
                if agent:
                    return self.respond(
                        self.store.get_messages(agent, (query.get("status") or [None])[0])
                    )
                return self.respond(self._table_rows("messages", 200))
            if len(parts) == 2 and parts[0] == "messages":
                return self.respond(
                    self.store.get_messages(parts[1], (query.get("status") or [None])[0])
                )

            if path == "/tasks":
                return self.respond(self.store.list_tasks((query.get("run_id") or [None])[0]))
            if path == "/tasks/stalled":
                return self.respond(self._stalled_tasks())
            if len(parts) == 2 and parts[0] == "tasks":
                return self.respond(self.store.get_task(parts[1]))

            if path == "/orchestration/runs":
                return self.respond(self.store.list_runs())
            if len(parts) == 3 and parts[0] == "orchestration" and parts[1] == "runs":
                return self.respond(self.store.get_run(parts[2]))
            if (
                len(parts) == 4
                and parts[0] == "orchestration"
                and parts[1] == "runs"
                and parts[3] == "events"
            ):
                return self.respond(self._events(parts[2]))

            if path == "/skills":
                return self.respond(self._table_rows("skills", 1000))
            if path == "/mcp/servers":
                return self.respond(self._table_rows("mcp_servers", 1000))
            if path == "/handoffs":
                return self.respond(self._table_rows("handoffs", 1000))
            if path == "/memory/search":
                needle = (query.get("q") or [""])[0]
                return self.respond(self._memory_search(needle))
            if path == "/capabilities":
                return self.respond(self._capability_inventory())
            if path == "/mcp/":
                return self.respond(
                    {
                        "status": "metadata-only",
                        "bridges": [
                            "agent_mesh_mcp_stdio.py",
                            "obsidian_vault_mcp_stdio.py",
                        ],
                        "protocol": "task-request-v1",
                    }
                )
            return self.error("not found", 404)
        except MeshError as exc:
            self.error(exc.detail, exc.status)
        except Exception as exc:
            self.error("internal server error", 500)
            print("Agent Mesh GET error:", redact_text(str(exc)))

    def do_POST(self) -> None:
        if not self.authenticated():
            return
        try:
            path = urlparse(self.path).path
            parts = self.segments(path)
            data = self.request_body()

            if path == "/agents/register":
                return self.respond(self.store.register_agent(data))
            if path == "/agents/heartbeat":
                name = data.get("agent") or data.get("agent_name") or data.get("name")
                return self.respond(self.store.heartbeat_agent(name, data))
            if len(parts) == 3 and parts[0] == "agents" and parts[2] == "heartbeat":
                return self.respond(self.store.heartbeat_agent(parts[1], data))

            if path == "/messages":
                return self.respond(self.store.create_message(data), 201)
            if len(parts) == 3 and parts[0] == "messages" and parts[2] == "ack":
                task_ref = data.get("task_id")
                if task_ref is None:
                    raise MeshError("task_id is required")
                return self.respond(self.store.acknowledge_task(task_ref, data))

            if path == "/handoff":
                return self.respond(self._create_handoff(data), 201)
            if path == "/memory":
                return self.respond(self._create_memory(data), 201)
            if path == "/skills/register":
                return self.respond(self._register_skill(data), 201)
            if path == "/mcp/servers/register":
                return self.respond(self._register_mcp(data), 201)

            if path == "/orchestration/runs":
                return self.respond(self.store.create_run(data), 201)
            if len(parts) == 4 and parts[:2] == ["orchestration", "runs"]:
                run_id = parts[2]
                if parts[3] == "cancel":
                    return self.respond(self.store.cancel_run(run_id, data.get("actor", "orchestrator")))
                if parts[3] == "finalize":
                    return self.respond(self.store.finalize_run(run_id, data))
                if parts[3] in {"advance", "dispatch"}:
                    self.store.dispatch_runnable(run_id)
                    return self.respond(self.store.reconcile_run(run_id))

            if path in {"/tasks/poll", "/agents/tasks/poll"}:
                agent = data.get("agent") or data.get("agent_id") or data.get("agent_name")
                return self.respond(self.store.poll_tasks(agent, data.get("limit", 1)))
            if path == "/tasks":
                return self.respond(self.store.create_legacy_task(data), 201)

            if len(parts) >= 3 and parts[0] == "tasks":
                reference = parts[1]
                action = parts[2]
                if action == "claim":
                    return self.respond(self.store.claim_legacy_task(reference, data))
                if action == "release":
                    return self.respond(self.store.release_task(reference))
                if action == "heartbeat":
                    return self.respond(self.store.heartbeat_task(reference, data))
                if action == "ack":
                    return self.respond(self.store.acknowledge_task(reference, data))
                if action in {"progress", "started"}:
                    return self.respond(self.store.task_progress(reference, data))
                if action in {"result", "complete"}:
                    return self.respond(self.store.submit_result(reference, data))
                if action in {"error", "fail"}:
                    return self.respond(self.store.fail_task(reference, data))
                if action == "verify":
                    return self.respond(self.store.verify_task(reference, data))
                if action == "cancel":
                    return self.respond(
                        self.store.cancel_task(reference, data.get("actor", "orchestrator"))
                    )
                if action in {"dispatch", "send"}:
                    task = self.store.get_task(reference)
                    self.store.dispatch_runnable(task.get("run_id") or None)
                    return self.respond(self.store.get_task(reference))
                return self.error("not found", 404)

            return self.error("not found", 404)
        except MeshError as exc:
            self.error(exc.detail, exc.status)
        except (TypeError, ValueError) as exc:
            self.error(redact_text(str(exc)), 400)
        except Exception as exc:
            self.error("internal server error", 500)
            print("Agent Mesh POST error:", redact_text(str(exc)))

    def _table_rows(self, table: str, limit: int) -> list[dict]:
        allowed = {
            "messages": "SELECT * FROM messages ORDER BY id DESC LIMIT ?",
            "skills": "SELECT * FROM skills ORDER BY name, owner_agent LIMIT ?",
            "mcp_servers": "SELECT * FROM mcp_servers ORDER BY name LIMIT ?",
            "handoffs": "SELECT * FROM handoffs ORDER BY id DESC LIMIT ?",
        }
        with self.store.connect() as database:
            rows = [dict(row) for row in database.execute(allowed[table], (limit,))]
        if table == "messages":
            return [self.store.decorate_message(row) for row in rows]
        return rows

    def _stalled_tasks(self) -> list[dict]:
        now = utc_now()
        with self.store.connect() as database:
            rows = [
                dict(row)
                for row in database.execute(
                    """
                    SELECT * FROM tasks
                    WHERE status NOT IN ('completed','failed','blocked','cancelled')
                      AND (
                        (lease_owner IS NOT NULL AND lease_expires_at < ?)
                        OR (status='sent' AND sent_at IS NOT NULL)
                      )
                    ORDER BY updated_at DESC
                    """,
                    (now,),
                )
            ]
        return [self.store.decorate_task(row) for row in rows]

    def _events(self, run_id: str) -> list[dict]:
        with self.store.connect() as database:
            return [
                dict(row)
                for row in database.execute(
                    "SELECT * FROM orchestration_events WHERE run_id=? ORDER BY id",
                    (run_id,),
                )
            ]

    def _memory_search(self, needle: str) -> list[dict]:
        query = "%" + redact_text(needle) + "%"
        with self.store.connect() as database:
            return [
                dict(row)
                for row in database.execute(
                    """
                    SELECT * FROM memory
                    WHERE title LIKE ? OR body LIKE ? OR source LIKE ?
                    ORDER BY updated_at DESC
                    """,
                    (query, query, query),
                )
            ]

    def _capability_inventory(self) -> list[dict]:
        return [
            {
                "agent_id": agent["name"],
                "name": agent["name"],
                "provider": agent.get("provider"),
                "model": agent.get("model"),
                "status": agent.get("status"),
                "health": agent.get("health"),
                "capabilities": agent.get("capabilities", {}),
                "active_task_count": agent.get("active_task_count", 0),
                "max_concurrent_tasks": agent.get("max_concurrent_tasks", 1),
            }
            for agent in self.store.list_agents()
        ]

    def _create_handoff(self, data: dict) -> dict:
        with self.store.transaction() as database:
            task_id = self.store._resolve_task_id(
                database, data.get("task_id"), required=False
            )
            now = utc_now()
            database.execute(
                """
                INSERT INTO handoffs
                (task_id, from_agent, to_agent, request, response, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    redact_text(data.get("from_agent") or "unknown"),
                    redact_text(data.get("to_agent") or "any-capable-agent"),
                    redact_text(data.get("request") or ""),
                    redact_text(data.get("response") or ""),
                    redact_text(data.get("status") or "requested"),
                    now,
                    now,
                ),
            )
            row = dict(
                database.execute(
                    "SELECT * FROM handoffs WHERE id=?",
                    (database.execute("SELECT last_insert_rowid()").fetchone()[0],),
                ).fetchone()
            )
        return row

    def _create_memory(self, data: dict) -> dict:
        with self.store.transaction() as database:
            now = utc_now()
            database.execute(
                """
                INSERT INTO memory
                (title, category, body, source, confidence, sensitivity, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    redact_text(data.get("title") or ""),
                    redact_text(data.get("category") or ""),
                    redact_text(data.get("body") or ""),
                    redact_text(data.get("source") or ""),
                    data.get("confidence"),
                    redact_text(data.get("sensitivity") or ""),
                    now,
                    now,
                ),
            )
            row = dict(
                database.execute(
                    "SELECT * FROM memory WHERE id=?",
                    (database.execute("SELECT last_insert_rowid()").fetchone()[0],),
                ).fetchone()
            )
        self.store.sync_memory(row)
        return row

    def _register_skill(self, data: dict) -> dict:
        with self.store.transaction() as database:
            now = utc_now()
            database.execute(
                """
                INSERT INTO skills
                (name, owner_agent, skill_type, input_format, output_format,
                 invocation_method, limitations, status, last_verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name, owner_agent) DO UPDATE SET
                    skill_type=excluded.skill_type,
                    input_format=excluded.input_format,
                    output_format=excluded.output_format,
                    invocation_method=excluded.invocation_method,
                    limitations=excluded.limitations,
                    status=excluded.status,
                    last_verified_at=excluded.last_verified_at
                """,
                (
                    redact_text(data.get("name") or ""),
                    redact_text(data.get("owner_agent") or ""),
                    redact_text(data.get("skill_type") or ""),
                    redact_text(data.get("input_format") or ""),
                    redact_text(data.get("output_format") or ""),
                    redact_text(data.get("invocation_method") or ""),
                    redact_text(data.get("limitations") or ""),
                    redact_text(data.get("status") or "active"),
                    data.get("last_verified_at") or now,
                ),
            )
            row = dict(
                database.execute(
                    """
                    SELECT * FROM skills
                    WHERE name=? AND COALESCE(owner_agent,'')=COALESCE(?, '')
                    """,
                    (data.get("name"), data.get("owner_agent")),
                ).fetchone()
            )
        return row

    def _register_mcp(self, data: dict) -> dict:
        with self.store.transaction() as database:
            now = utc_now()
            tools = data.get("tools") if data.get("tools") is not None else data.get("tools_json")
            database.execute(
                """
                INSERT INTO mcp_servers
                (name, owner_agent, endpoint, transport, auth_ref, tools_json,
                 safety_limits, status, last_verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    owner_agent=excluded.owner_agent,
                    endpoint=excluded.endpoint,
                    transport=excluded.transport,
                    auth_ref=excluded.auth_ref,
                    tools_json=excluded.tools_json,
                    safety_limits=excluded.safety_limits,
                    status=excluded.status,
                    last_verified_at=excluded.last_verified_at
                """,
                (
                    redact_text(data.get("name") or ""),
                    redact_text(data.get("owner_agent") or ""),
                    redact_text(data.get("endpoint") or ""),
                    redact_text(data.get("transport") or ""),
                    redact_text(data.get("auth_ref") or ""),
                    json.dumps(sanitize(tools if tools is not None else []), sort_keys=True),
                    json.dumps(sanitize(data.get("safety_limits") or {}), sort_keys=True),
                    redact_text(data.get("status") or "active"),
                    data.get("last_verified_at") or now,
                ),
            )
            row = dict(
                database.execute(
                    "SELECT * FROM mcp_servers WHERE name=?",
                    (data.get("name"),),
                ).fetchone()
            )
        return row


def reaper_loop(store: MeshStore, stop: threading.Event) -> None:
    while not stop.wait(store.settings.reaper_interval):
        try:
            store.reap_timeouts()
        except Exception as exc:
            print("Agent Mesh reaper error:", redact_text(str(exc)))


def main() -> None:
    load_env()
    settings = Settings.from_env()
    server = MeshHTTPServer((settings.host, settings.port), settings)
    stop = threading.Event()
    threading.Thread(target=reaper_loop, args=(server.store, stop), daemon=True).start()
    print(
        f"Agent Mesh running on http://{settings.host}:{settings.port} "
        "(SQLite durable task protocol)"
    )
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
