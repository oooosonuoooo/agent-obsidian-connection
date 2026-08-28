#!/usr/bin/env python3
"""Regression and protocol tests for the durable Agent Mesh control plane."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_mesh_core import MeshError, MeshStore, Settings, redact_text  # noqa: E402
from agent_mesh_service import MeshHTTPServer  # noqa: E402


def test_settings(base: Path, *, token: str | None = None) -> Settings:
    return Settings(
        root=base,
        db=base / "agent_mesh.sqlite",
        vault=base / "vault",
        host="127.0.0.1",
        port=0,
        token=token,
        ack_timeout=0.1,
        execution_timeout=10,
        max_retries=2,
        retry_backoff=0,
        heartbeat_timeout=10,
        max_parallel=8,
        max_delegation_depth=3,
        reaper_interval=0.1,
        max_body_bytes=1024 * 1024,
    )


class MeshTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="agent-mesh-test-")
        self.base = Path(self.tempdir.name)
        self.settings = test_settings(self.base)
        self.store = MeshStore(self.settings)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def register(self, name: str, capabilities: list[str], **extra) -> dict:
        data = {
            "name": name,
            "provider": "test-provider",
            "model": "test-model",
            "type": "worker",
            "capabilities": capabilities,
            "max_concurrent_tasks": 2,
        }
        data.update(extra)
        return self.store.register_agent(data)

    def finish_task(self, task_key: str, agent: str, summary: str) -> dict:
        inbox = self.store.poll_tasks(agent, 1)
        message = next(
            item["message"]
            for item in inbox
            if item["task"].get("task_key") == task_key
        )
        self.store.acknowledge_task(
            task_key,
            {"agent": agent, "message_id": message["id"], "accepted": True},
        )
        self.store.task_progress(
            task_key,
            {"agent": agent, "progress": 100, "summary": "work finished"},
        )
        submitted = self.store.submit_result(
            task_key,
            {
                "agent": agent,
                "idempotency_key": f"{task_key}:result:1",
                "result": {
                    "summary": summary,
                    "files_changed": [],
                    "files_created": [],
                    "commands_executed": [],
                    "tests": ["protocol regression"],
                    "warnings": [],
                    "errors": [],
                    "handoff_notes": [],
                },
            },
        )
        self.assertEqual(submitted["status"], "verifying")
        return self.store.verify_task(
            task_key,
            {"verified_by": "Lead", "valid": True},
        )

    def start_http(self, token: str = "unit-test-token"):
        settings = test_settings(self.base, token=token)
        server = MeshHTTPServer((settings.host, 0), settings)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        return server, f"http://127.0.0.1:{server.server_address[1]}"


class AgentMeshCoreTests(MeshTestCase):
    def test_discovery_consultation_dag_and_verified_final_result(self) -> None:
        self.register("Lead", ["orchestration"])
        self.register("Frontend", ["frontend", "consultation"])
        self.register("Backend", ["backend", "consultation"])

        run = self.store.create_run(
            {
                "run_id": "consultation-and-build",
                "request": "Design and build a coordinated service",
                "lead_agent": "Lead",
                "plan": {
                    "tasks": [
                        {
                            "task_id": "frontend-consult",
                            "task_type": "consultation",
                            "title": "Frontend recommendation",
                            "description": "Recommend a frontend structure.",
                            "assigned_agent": "Frontend",
                            "required_capabilities": ["consultation"],
                        },
                        {
                            "task_id": "backend-consult",
                            "task_type": "consultation",
                            "title": "Backend recommendation",
                            "description": "Recommend a backend structure.",
                            "assigned_agent": "Backend",
                            "required_capabilities": ["consultation"],
                        },
                        {
                            "task_id": "integrate",
                            "title": "Integrate recommendations",
                            "description": "Turn the approved recommendations into an integration plan.",
                            "assigned_agent": "Frontend",
                            "dependencies": ["frontend-consult", "backend-consult"],
                            "required_capabilities": ["frontend"],
                            "interfaces": {"output": "integration-plan"},
                            "acceptance_criteria": "Both recommendations are represented.",
                        },
                    ]
                },
            }
        )

        self.assertEqual({agent["name"] for agent in self.store.list_agents()}, {"Lead", "Frontend", "Backend"})
        statuses = {task["task_key"]: task["status"] for task in run["tasks"]}
        self.assertEqual(statuses["frontend-consult"], "sent")
        self.assertEqual(statuses["backend-consult"], "sent")
        self.assertEqual(statuses["integrate"], "waiting_dependency")

        self.finish_task("frontend-consult", "Frontend", "Use a component-oriented frontend.")
        self.assertEqual(self.store.get_task("integrate")["status"], "waiting_dependency")
        self.finish_task("backend-consult", "Backend", "Use a versioned service API.")
        self.assertEqual(self.store.get_task("integrate")["status"], "sent")
        self.finish_task("integrate", "Frontend", "Integration plan combines both consultations.")

        run = self.store.finalize_run(
            "consultation-and-build",
            {
                "finalized_by": "Lead",
                "result": {
                    "summary": "The lead agent integrated the verified specialist results.",
                    "tests": ["consultation", "dependency ordering", "result verification"],
                    "files_changed": [],
                    "files_created": [],
                    "commands_executed": [],
                    "warnings": [],
                    "errors": [],
                    "handoff_notes": [],
                },
            },
        )
        self.assertEqual(run["state"], "COMPLETED")
        self.assertEqual(run["final_result"]["summary"], "The lead agent integrated the verified specialist results.")
        self.assertTrue(any(event["event_type"] == "task.acknowledged" for event in run["events"]))
        self.assertTrue(any(event["event_type"] == "task.completed" for event in run["events"]))
        self.assertTrue(any(event["event_type"] == "orchestration.finalized" for event in run["events"]))

        with self.store.connect() as database:
            message_types = {
                row["message_type"]
                for row in database.execute(
                    "SELECT message_type FROM messages WHERE task_id IN "
                    "(SELECT id FROM tasks WHERE run_id=?)",
                    ("consultation-and-build",),
                )
            }
        self.assertTrue({"TASK_REQUEST", "TASK_ACK", "TASK_PROGRESS", "TASK_RESULT"} <= message_types)

    def test_timeout_retry_reassignment_and_persisted_recovery(self) -> None:
        self.register("WorkerA", ["python"])
        self.register("WorkerB", ["python"])
        run = self.store.create_run(
            {
                "run_id": "retry-run",
                "request": "Recover an unresponsive worker",
                "lead_agent": "Lead",
                "plan": {
                    "tasks": [
                        {
                            "task_id": "retryable",
                            "title": "Retryable work",
                            "assigned_agent": "WorkerA",
                            "candidate_agents": ["WorkerA", "WorkerB"],
                            "required_capabilities": ["python"],
                            "max_retries": 1,
                            "retry_backoff": 0,
                        }
                    ]
                },
            }
        )
        self.assertEqual(run["tasks"][0]["assigned_agent"], "WorkerA")
        with self.store.transaction() as database:
            database.execute(
                "UPDATE tasks SET sent_at='2000-01-01T00:00:00+00:00' WHERE task_key='retryable'"
            )
        self.assertEqual(self.store.reap_timeouts(), 1)
        retried = self.store.get_task("retryable")
        self.assertEqual(retried["attempt"], 2)
        self.assertEqual(retried["assigned_agent"], "WorkerB")
        self.assertEqual(retried["status"], "sent")
        self.assertIn("WorkerA", retried["failed_agents"])

        inbox = self.store.poll_tasks("WorkerB", 1)
        self.assertEqual(len(inbox), 1)
        self.store.fail_task(
            "retryable",
            {"agent": "WorkerB", "error": {"message": "worker failed again"}},
        )
        self.assertEqual(self.store.get_task("retryable")["status"], "failed")
        self.assertEqual(self.store.get_run("retry-run")["state"], "FAILED")

        recovered_store = MeshStore(self.settings)
        recovered = recovered_store.get_run("retry-run")
        self.assertEqual(recovered["tasks"][0]["status"], "failed")
        self.assertGreaterEqual(len(recovered["events"]), 5)

    def test_dependencies_block_after_failed_prerequisite(self) -> None:
        self.register("Worker", ["python"])
        run = self.store.create_run(
            {
                "run_id": "blocked-run",
                "request": "Test dependency failure handling",
                "lead_agent": "Lead",
                "plan": {
                    "tasks": [
                        {
                            "task_id": "root",
                            "title": "Root task",
                            "assigned_agent": "Worker",
                            "max_retries": 0,
                        },
                        {
                            "task_id": "dependent",
                            "title": "Dependent task",
                            "assigned_agent": "Worker",
                            "dependencies": ["root"],
                        },
                    ]
                },
            }
        )
        self.assertEqual(run["tasks"][1]["status"], "waiting_dependency")
        self.store.poll_tasks("Worker", 1)
        self.store.fail_task("root", {"agent": "Worker", "error": "root failed"})
        self.assertEqual(self.store.get_task("dependent")["status"], "blocked")
        self.assertEqual(self.store.get_run("blocked-run")["state"], "FAILED")

    def test_artifact_lock_serializes_shared_file_and_parallel_workers_are_independent(self) -> None:
        self.register("WorkerA", ["python"], max_concurrent_tasks=2)
        self.register("WorkerB", ["python"], max_concurrent_tasks=2)
        run = self.store.create_run(
            {
                "run_id": "parallel-run",
                "request": "Run independent work safely",
                "lead_agent": "Lead",
                "plan": {
                    "tasks": [
                        {
                            "task_id": "shared-a",
                            "title": "Shared artifact A",
                            "assigned_agent": "WorkerA",
                            "artifact_paths": ["src/shared.py"],
                        },
                        {
                            "task_id": "shared-b",
                            "title": "Shared artifact B",
                            "assigned_agent": "WorkerB",
                            "artifact_paths": ["src/shared.py"],
                        },
                        {
                            "task_id": "independent",
                            "title": "Independent work",
                            "required_capabilities": ["python"],
                        },
                    ]
                },
            }
        )
        first = self.store.get_task("shared-a")
        second = self.store.get_task("shared-b")
        independent = self.store.get_task("independent")
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "waiting_dependency")
        self.assertEqual(independent["status"], "sent")
        self.assertNotEqual(independent["assigned_agent"], first["assigned_agent"])

        self.finish_task("shared-a", "WorkerA", "Shared artifact A completed.")
        self.assertEqual(self.store.get_task("shared-b")["status"], "sent")
        self.finish_task("independent", independent["assigned_agent"], "Independent work completed.")
        self.finish_task("shared-b", "WorkerB", "Shared artifact B completed.")

    def test_duplicate_result_loop_protection_and_secret_redaction(self) -> None:
        self.register("Worker", ["python"])
        with self.assertRaises(MeshError):
            self.store.create_run(
                {
                    "run_id": "cycle",
                    "request": "reject a cycle",
                    "lead_agent": "Lead",
                    "plan": {
                        "tasks": [
                            {"task_id": "a", "dependencies": ["b"]},
                            {"task_id": "b", "dependencies": ["a"]},
                        ]
                    },
                }
            )
        self.assertNotIn("test-secret", redact_text('password="test-secret" api_key=test-secret'))

        run = self.store.create_run(
            {
                "run_id": "duplicate-result",
                "request": "protect duplicate side effects",
                "lead_agent": "Lead",
                "plan": {"tasks": [{"task_id": "once", "assigned_agent": "Worker"}]},
            }
        )
        inbox = self.store.poll_tasks("Worker", 1)
        message_id = inbox[0]["message"]["id"]
        self.store.acknowledge_task("once", {"agent": "Worker", "message_id": message_id})
        result = {"summary": "exactly once", "files_changed": []}
        self.store.submit_result(
            "once",
            {"agent": "Worker", "idempotency_key": "once:result", "result": result},
        )
        duplicate = self.store.submit_result(
            "once",
            {"agent": "Worker", "idempotency_key": "once:result", "result": result},
        )
        self.assertEqual(duplicate["status"], "verifying")
        with self.store.connect() as database:
            count = database.execute(
                "SELECT COUNT(*) FROM task_results WHERE task_id=(SELECT id FROM tasks WHERE task_key='once')"
            ).fetchone()[0]
        self.assertEqual(count, 1)


class MigrationTests(unittest.TestCase):
    def test_legacy_schema_is_migrated_without_losing_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-mesh-migration-test-") as temp:
            base = Path(temp)
            db_path = base / "agent_mesh.sqlite"
            database = sqlite3.connect(db_path)
            database.executescript(
                """
                CREATE TABLE agents (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, provider TEXT, type TEXT, capabilities_json TEXT, limitations TEXT, status TEXT, registered_at TEXT, last_seen_at TEXT);
                CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_agent TEXT, to_agent TEXT, task_id INTEGER, subject TEXT, body TEXT, status TEXT, created_at TEXT, read_at TEXT);
                CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, owner_agent TEXT, status TEXT, priority TEXT, project TEXT, context_path TEXT, result_path TEXT, last_heartbeat_at TEXT, last_active_agent TEXT, lease_owner TEXT, lease_expires_at TEXT, resume_packet_path TEXT, created_at TEXT, updated_at TEXT);
                CREATE TABLE memory (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, category TEXT, body TEXT, source TEXT, confidence REAL, sensitivity TEXT, created_at TEXT, updated_at TEXT);
                CREATE TABLE handoffs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, from_agent TEXT, to_agent TEXT, request TEXT, response TEXT, status TEXT, created_at TEXT, updated_at TEXT);
                CREATE TABLE mcp_servers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, owner_agent TEXT, endpoint TEXT, transport TEXT, auth_ref TEXT, tools_json TEXT, safety_limits TEXT, status TEXT, last_verified_at TEXT);
                CREATE UNIQUE INDEX idx_mcp_name ON mcp_servers(name);
                CREATE TABLE skills (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, owner_agent TEXT, skill_type TEXT, input_format TEXT, output_format TEXT, invocation_method TEXT, limitations TEXT, status TEXT, last_verified_at TEXT);
                CREATE UNIQUE INDEX idx_skill_name_owner ON skills(name, owner_agent);
                INSERT INTO agents(name, provider, status) VALUES ('Legacy', 'legacy-provider', 'active');
                INSERT INTO messages(from_agent, to_agent, subject, body, status, created_at) VALUES ('Legacy', 'Legacy', 'old message', 'preserve me', 'queued', datetime('now'));
                INSERT INTO tasks(title, owner_agent, status, created_at, updated_at) VALUES ('old task', 'Legacy', 'pending', datetime('now'), datetime('now'));
                """
            )
            database.commit()
            database.close()

            store = MeshStore(test_settings(base))
            self.assertEqual(store.get_task("legacy-task-1")["title"], "old task")
            self.assertEqual(store.get_messages("Legacy")[0]["body"], "preserve me")
            with store.connect() as database:
                task_columns = {row["name"] for row in database.execute("PRAGMA table_info(tasks)")}
                run_columns = {row["name"] for row in database.execute("PRAGMA table_info(orchestration_runs)")}
            self.assertIn("completed_at", task_columns)
            self.assertIn("final_result_json", run_columns)


class HTTPAndMCPTests(MeshTestCase):
    def http_request(self, base_url: str, method: str, path: str, payload=None, token: str | None = "unit-test-token"):
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = Request(base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except HTTPError as exc:
            raw = exc.read()
            exc.close()
            return exc.code, json.loads(raw.decode())

    def test_http_auth_routes_and_three_agent_e2e(self) -> None:
        server, base_url = self.start_http()
        status, health = self.http_request(base_url, "GET", "/health", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(health["protocol"], "task-request-v1")
        status, metadata = self.http_request(base_url, "GET", "/mcp/", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(metadata["status"], "metadata-only")
        status, _ = self.http_request(base_url, "GET", "/agents", token=None)
        self.assertEqual(status, 401)

        for name, capabilities in (
            ("Lead", ["orchestration"]),
            ("WorkerOne", ["python"]),
            ("WorkerTwo", ["testing"]),
        ):
            status, _ = self.http_request(
                base_url,
                "POST",
                "/agents/register",
                {"name": name, "capabilities": capabilities, "provider": "http-test"},
            )
            self.assertEqual(status, 200)

        status, run = self.http_request(
            base_url,
            "POST",
            "/orchestration/runs",
            {
                "run_id": "http-e2e",
                "request": "Exercise the real local HTTP protocol",
                "lead_agent": "Lead",
                "plan": {
                    "tasks": [
                        {"task_id": "one", "assigned_agent": "WorkerOne", "required_capabilities": ["python"]},
                        {"task_id": "two", "assigned_agent": "WorkerTwo", "required_capabilities": ["testing"]},
                    ]
                },
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual({task["status"] for task in run["tasks"]}, {"sent"})
        status, tasks = self.http_request(base_url, "GET", "/tasks?run_id=http-e2e")
        self.assertEqual(status, 200)
        self.assertEqual({task["task_key"] for task in tasks}, {"one", "two"})

        for task_key, agent in (("one", "WorkerOne"), ("two", "WorkerTwo")):
            status, inbox = self.http_request(
                base_url, "POST", "/tasks/poll", {"agent": agent, "limit": 1}
            )
            self.assertEqual(status, 200)
            self.assertEqual(len(inbox), 1)
            message_id = inbox[0]["message"]["id"]
            status, acknowledged = self.http_request(
                base_url,
                "POST",
                f"/tasks/{task_key}/ack",
                {"agent": agent, "message_id": message_id},
            )
            self.assertEqual(status, 200)
            self.assertEqual(acknowledged["status"], "acknowledged")
            status, progressed = self.http_request(
                base_url,
                "POST",
                f"/tasks/{task_key}/progress",
                {"agent": agent, "progress": 100, "summary": "finished"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(progressed["status"], "running")
            status, submitted = self.http_request(
                base_url,
                "POST",
                f"/tasks/{task_key}/result",
                {
                    "agent": agent,
                    "result": {"summary": f"{task_key} result", "tests": ["http"]},
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(submitted["status"], "verifying")
            status, verified = self.http_request(
                base_url,
                "POST",
                f"/tasks/{task_key}/verify",
                {"verified_by": "Lead", "valid": True},
            )
            self.assertEqual(status, 200)
            self.assertEqual(verified["status"], "completed")

        status, final = self.http_request(
            base_url,
            "POST",
            "/orchestration/runs/http-e2e/finalize",
            {"finalized_by": "Lead", "result": {"summary": "HTTP e2e complete"}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(final["state"], "COMPLETED")
        self.assertEqual(len(final["tasks"]), 2)
        self.assertIsNotNone(server.store.get_run("http-e2e")["final_result"])

    def test_legacy_routes_remain_usable(self) -> None:
        server, base_url = self.start_http()
        status, _ = self.http_request(
            base_url,
            "POST",
            "/agents/register",
            {"name": "LegacyAgent", "provider": "legacy-test", "type": "worker"},
        )
        self.assertEqual(status, 200)

        status, message = self.http_request(
            base_url,
            "POST",
            "/messages",
            {
                "from_agent": "LegacyAgent",
                "to_agent": "LegacyAgent",
                "subject": "legacy message",
                "body": "hello",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(message["status"], "queued")
        status, messages = self.http_request(base_url, "GET", "/messages/LegacyAgent")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["id"] == message["id"] for item in messages))

        status, task = self.http_request(
            base_url,
            "POST",
            "/tasks",
            {"title": "legacy task", "owner_agent": "LegacyAgent"},
        )
        self.assertEqual(status, 201)
        task_id = str(task["id"])
        status, claimed = self.http_request(
            base_url,
            "POST",
            f"/tasks/{task_id}/claim",
            {"agent": "LegacyAgent", "lease_seconds": 60},
        )
        self.assertEqual(status, 200)
        self.assertEqual(claimed["lease_owner"], "LegacyAgent")
        status, heartbeated = self.http_request(
            base_url,
            "POST",
            f"/tasks/{task_id}/heartbeat",
            {"agent": "LegacyAgent"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(heartbeated["last_active_agent"], "LegacyAgent")
        status, released = self.http_request(
            base_url, "POST", f"/tasks/{task_id}/release", {}
        )
        self.assertEqual(status, 200)
        self.assertIsNone(released["lease_owner"])

        status, memory = self.http_request(
            base_url,
            "POST",
            "/memory",
            {"title": "legacy memory", "body": "durable compatibility"},
        )
        self.assertEqual(status, 201)
        status, memories = self.http_request(
            base_url, "GET", "/memory/search?q=compatibility"
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(item["id"] == memory["id"] for item in memories))

        for path, payload, collection, key in (
            (
                "/handoff",
                {"from_agent": "LegacyAgent", "to_agent": "LegacyAgent", "request": "handoff"},
                "/handoffs",
                "request",
            ),
            (
                "/skills/register",
                {"name": "legacy-skill", "owner_agent": "LegacyAgent"},
                "/skills",
                "name",
            ),
            (
                "/mcp/servers/register",
                {"name": "legacy-mcp", "owner_agent": "LegacyAgent"},
                "/mcp/servers",
                "name",
            ),
        ):
            status, created = self.http_request(base_url, "POST", path, payload)
            self.assertEqual(status, 201)
            status, rows = self.http_request(base_url, "GET", collection)
            self.assertEqual(status, 200)
            self.assertTrue(any(row[key] == created[key] for row in rows))

    def test_mcp_initialize_and_tools_list_support_content_length(self) -> None:
        server, base_url = self.start_http()
        environment = os.environ.copy()
        environment["AGENT_MESH_BASE_URL"] = base_url
        environment["AGENT_MESH_TOKEN"] = "unit-test-token"
        process = subprocess.Popen(
            [sys.executable, str(SCRIPTS / "agent_mesh_mcp_stdio.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )

        def close_process() -> None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

        self.addCleanup(close_process)

        def send(request: dict) -> dict:
            body = json.dumps(request, separators=(",", ":")).encode()
            assert process.stdin is not None
            process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            process.stdin.flush()
            assert process.stdout is not None
            first = process.stdout.readline()
            self.assertTrue(first)
            headers = {}
            line = first
            while line.strip():
                key, value = line.decode().split(":", 1)
                headers[key.lower()] = value.strip()
                line = process.stdout.readline()
            length = int(headers["content-length"])
            return json.loads(process.stdout.read(length).decode())

        initialized = send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "agent-mesh-stdio")
        tools = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tool_names = {tool["name"] for tool in tools["result"]["tools"]}
        self.assertIn("agent_mesh_poll_tasks", tool_names)
        self.assertIn("agent_mesh_finalize_run", tool_names)
        send({"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}})
        process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
