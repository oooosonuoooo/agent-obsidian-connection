#!/usr/bin/env python3
"""MCP stdio adapter for the Agent Mesh REST control plane.

The adapter is intentionally a thin transport layer.  It exposes the durable
task protocol to real agents; it does not fabricate worker responses.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BASE = os.environ.get("AGENT_MESH_BASE_URL", "http://127.0.0.1:17860").rstrip("/")


def token() -> str | None:
    if os.environ.get("AGENT_MESH_TOKEN"):
        return os.environ["AGENT_MESH_TOKEN"]
    for path in (Path.home() / "AI-Second-Brain/.env.local", Path.home() / "airllm/.env"):
        if not path.exists():
            continue
        for line in path.read_text(errors="ignore").splitlines():
            item = line.strip()
            if item.startswith("export "):
                item = item[7:]
            if item.startswith("AGENT_MESH_TOKEN=") or item.startswith("FRIDAY_WEB_TOKEN="):
                return item.split("=", 1)[1].strip().strip("'\"")
    return None


def read_message():
    first = sys.stdin.buffer.readline()
    if not first:
        return None
    stripped = first.lstrip()
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        return json.loads(first), "line"

    headers: dict[str, str] = {}
    line = first
    while line and line.strip():
        if b":" in line:
            key, value = line.decode(errors="replace").split(":", 1)
            headers[key.lower()] = value.strip()
        line = sys.stdin.buffer.readline()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    payload = sys.stdin.buffer.read(length)
    if not payload:
        return None
    return json.loads(payload), "content-length"


def send_message(message, mode: str = "content-length") -> None:
    body = json.dumps(message, separators=(",", ":")).encode()
    if mode == "line":
        sys.stdout.buffer.write(body + b"\n")
    else:
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()


def http(method: str, path: str, data=None):
    headers = {"Accept": "application/json"}
    auth_token = token()
    if auth_token:
        headers["Authorization"] = "Bearer " + auth_token
    body = json.dumps(data).encode() if data is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(raw)
            detail = parsed.get("error") or parsed.get("detail") or str(exc)
        except (ValueError, AttributeError):
            detail = str(exc)
        raise RuntimeError(str(detail)) from exc


def schema(properties=None, required=None):
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "agent_mesh_health",
        "description": "Check local Agent Mesh health and durable queue counts.",
        "inputSchema": schema(),
    },
    {
        "name": "agent_mesh_list_agents",
        "description": "Discover registered agents, capabilities, health, and workload.",
        "inputSchema": schema(),
    },
    {
        "name": "agent_mesh_get_agent",
        "description": "Get one registered agent and its capability metadata.",
        "inputSchema": schema({"agent": {"type": "string"}}, ["agent"]),
    },
    {
        "name": "agent_mesh_list_capabilities",
        "description": "List the capability registry used for agent selection.",
        "inputSchema": schema(),
    },
    {
        "name": "agent_mesh_send_message",
        "description": "Persist a direct or protocol message for another agent.",
        "inputSchema": schema(
            {
                "from_agent": {"type": "string"},
                "to_agent": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "task_id": {"type": "string"},
                "message_type": {"type": "string"},
                "payload": {"type": "object"},
                "correlation_id": {"type": "string"},
                "conversation_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            ["to_agent", "subject"],
        ),
    },
    {
        "name": "agent_mesh_create_handoff",
        "description": "Create a recorded handoff request.",
        "inputSchema": schema(
            {
                "from_agent": {"type": "string"},
                "to_agent": {"type": "string"},
                "request": {"type": "string"},
                "task_id": {"type": "string"},
            },
            ["from_agent", "to_agent", "request"],
        ),
    },
    {
        "name": "agent_mesh_create_orchestration_run",
        "description": "Create a lead-agent run from an explicit task plan and DAG.",
        "inputSchema": schema(
            {
                "request": {"type": "string"},
                "lead_agent": {"type": "string"},
                "tasks": {"type": "array", "items": {"type": "object"}},
                "plan": {"type": "object"},
                "metadata": {"type": "object"},
                "max_delegation_depth": {"type": "integer"},
                "run_id": {"type": "string"},
            },
            ["request"],
        ),
    },
    {
        "name": "agent_mesh_get_run",
        "description": "Read orchestration state, task results, and trace events.",
        "inputSchema": schema({"run_id": {"type": "string"}}, ["run_id"]),
    },
    {
        "name": "agent_mesh_advance_run",
        "description": "Reconcile a run and dispatch runnable durable tasks.",
        "inputSchema": schema({"run_id": {"type": "string"}}, ["run_id"]),
    },
    {
        "name": "agent_mesh_cancel_run",
        "description": "Cancel a run and propagate cancellation to pending work.",
        "inputSchema": schema(
            {"run_id": {"type": "string"}, "actor": {"type": "string"}},
            ["run_id"],
        ),
    },
    {
        "name": "agent_mesh_finalize_run",
        "description": "Store the lead agent's verified integrated result for a completed run.",
        "inputSchema": schema(
            {
                "run_id": {"type": "string"},
                "finalized_by": {"type": "string"},
                "result": {"type": "object"},
                "final_result": {"type": "object"},
                "summary": {"type": "string"},
            },
            ["run_id"],
        ),
    },
    {
        "name": "agent_mesh_poll_tasks",
        "description": "Poll and lease TASK_REQUEST messages addressed to this real worker agent.",
        "inputSchema": schema(
            {"agent": {"type": "string"}, "limit": {"type": "integer"}},
            ["agent"],
        ),
    },
    {
        "name": "agent_mesh_get_task",
        "description": "Read a task, context, dependencies, and current result.",
        "inputSchema": schema({"task_id": {"type": "string"}}, ["task_id"]),
    },
    {
        "name": "agent_mesh_get_tasks",
        "description": "List durable tasks, optionally scoped to an orchestration run.",
        "inputSchema": schema({"run_id": {"type": "string"}}),
    },
    {
        "name": "agent_mesh_ack_task",
        "description": "Acknowledge or reject a received TASK_REQUEST.",
        "inputSchema": schema(
            {
                "task_id": {"type": "string"},
                "agent": {"type": "string"},
                "accepted": {"type": "boolean"},
                "reason": {"type": "string"},
                "message_id": {"type": "integer"},
            },
            ["task_id", "agent"],
        ),
    },
    {
        "name": "agent_mesh_task_progress",
        "description": "Publish structured progress and refresh the task lease.",
        "inputSchema": schema(
            {
                "task_id": {"type": "string"},
                "agent": {"type": "string"},
                "progress": {"type": "number"},
                "summary": {"type": "string"},
                "message": {"type": "string"},
            },
            ["task_id", "agent"],
        ),
    },
    {
        "name": "agent_mesh_submit_task_result",
        "description": "Submit a structured worker result; lead-agent verification is still required.",
        "inputSchema": schema(
            {
                "task_id": {"type": "string"},
                "agent": {"type": "string"},
                "result": {"type": "object"},
                "idempotency_key": {"type": "string"},
            },
            ["task_id", "agent", "result"],
        ),
    },
    {
        "name": "agent_mesh_fail_task",
        "description": "Report a task error and allow bounded retry/reassignment.",
        "inputSchema": schema(
            {
                "task_id": {"type": "string"},
                "agent": {"type": "string"},
                "error": {"type": "object"},
                "message": {"type": "string"},
                "reassign": {"type": "boolean"},
            },
            ["task_id", "agent"],
        ),
    },
    {
        "name": "agent_mesh_verify_task",
        "description": "Lead-agent verification gate for a submitted result.",
        "inputSchema": schema(
            {
                "task_id": {"type": "string"},
                "valid": {"type": "boolean"},
                "verified_by": {"type": "string"},
                "revision_instructions": {"type": "string"},
                "expected": {},
                "actual": {},
                "retry": {"type": "boolean"},
                "reassign": {"type": "boolean"},
            },
            ["task_id", "valid"],
        ),
    },
    {
        "name": "agent_mesh_heartbeat",
        "description": "Publish worker health and keep the agent eligible for assignment.",
        "inputSchema": schema(
            {
                "agent": {"type": "string"},
                "status": {"type": "string"},
                "health": {"type": "string"},
            },
            ["agent"],
        ),
    },
    {
        "name": "agent_mesh_task_heartbeat",
        "description": "Refresh a running task heartbeat and execution lease.",
        "inputSchema": schema(
            {"task_id": {"type": "string"}, "agent": {"type": "string"}},
            ["task_id", "agent"],
        ),
    },
    {
        "name": "agent_mesh_cancel_task",
        "description": "Cancel one task and prevent further delivery.",
        "inputSchema": schema(
            {"task_id": {"type": "string"}, "actor": {"type": "string"}},
            ["task_id"],
        ),
    },
    {
        "name": "agent_mesh_get_inbox",
        "description": "Read messages addressed to an agent, including ACKs and results.",
        "inputSchema": schema(
            {"agent": {"type": "string"}, "status": {"type": "string"}},
            ["agent"],
        ),
    },
]


def encoded(value) -> str:
    return urllib.parse.quote(str(value), safe="")


def call_tool(name: str, arguments: dict):
    if name == "agent_mesh_health":
        return http("GET", "/health")
    if name == "agent_mesh_list_agents":
        return http("GET", "/agents")
    if name == "agent_mesh_get_agent":
        return http("GET", "/agents/" + encoded(arguments["agent"]))
    if name == "agent_mesh_list_capabilities":
        return http("GET", "/capabilities")
    if name == "agent_mesh_send_message":
        return http("POST", "/messages", arguments)
    if name == "agent_mesh_create_handoff":
        return http("POST", "/handoff", arguments)
    if name == "agent_mesh_create_orchestration_run":
        return http("POST", "/orchestration/runs", arguments)
    if name == "agent_mesh_get_run":
        return http("GET", "/orchestration/runs/" + encoded(arguments["run_id"]))
    if name == "agent_mesh_advance_run":
        return http(
            "POST",
            "/orchestration/runs/" + encoded(arguments["run_id"]) + "/advance",
            {},
        )
    if name == "agent_mesh_cancel_run":
        return http(
            "POST",
            "/orchestration/runs/" + encoded(arguments["run_id"]) + "/cancel",
            arguments,
        )
    if name == "agent_mesh_finalize_run":
        return http(
            "POST",
            "/orchestration/runs/" + encoded(arguments["run_id"]) + "/finalize",
            arguments,
        )
    if name == "agent_mesh_poll_tasks":
        return http("POST", "/tasks/poll", arguments)
    if name == "agent_mesh_get_task":
        return http("GET", "/tasks/" + encoded(arguments["task_id"]))
    if name == "agent_mesh_get_tasks":
        suffix = ""
        if arguments.get("run_id"):
            suffix = "?run_id=" + encoded(arguments["run_id"])
        return http("GET", "/tasks" + suffix)
    if name == "agent_mesh_ack_task":
        return http(
            "POST",
            "/tasks/" + encoded(arguments["task_id"]) + "/ack",
            arguments,
        )
    if name == "agent_mesh_task_progress":
        return http(
            "POST",
            "/tasks/" + encoded(arguments["task_id"]) + "/progress",
            arguments,
        )
    if name == "agent_mesh_submit_task_result":
        return http(
            "POST",
            "/tasks/" + encoded(arguments["task_id"]) + "/result",
            arguments,
        )
    if name == "agent_mesh_fail_task":
        return http(
            "POST",
            "/tasks/" + encoded(arguments["task_id"]) + "/error",
            arguments,
        )
    if name == "agent_mesh_verify_task":
        return http(
            "POST",
            "/tasks/" + encoded(arguments["task_id"]) + "/verify",
            arguments,
        )
    if name == "agent_mesh_heartbeat":
        return http(
            "POST",
            "/agents/" + encoded(arguments["agent"]) + "/heartbeat",
            arguments,
        )
    if name == "agent_mesh_task_heartbeat":
        return http(
            "POST",
            "/tasks/" + encoded(arguments["task_id"]) + "/heartbeat",
            arguments,
        )
    if name == "agent_mesh_cancel_task":
        return http(
            "POST",
            "/tasks/" + encoded(arguments["task_id"]) + "/cancel",
            arguments,
        )
    if name == "agent_mesh_get_inbox":
        suffix = ""
        if arguments.get("status"):
            suffix = "?status=" + encoded(arguments["status"])
        return http(
            "GET",
            "/agents/" + encoded(arguments["agent"]) + "/inbox" + suffix,
        )
    raise ValueError(f"unknown tool: {name}")


def initialize_result(request):
    params = request.get("params") or {}
    protocol = params.get("protocolVersion") or "2024-11-05"
    return {
        "protocolVersion": protocol,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
            "prompts": {"listChanged": False},
        },
        "serverInfo": {"name": "agent-mesh-stdio", "version": "2.0.0"},
    }


def handle_request(request):
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        return initialize_result(request), False
    if method in {"notifications/initialized", "notifications/cancelled", "notifications/progress"}:
        return None, False
    if method == "ping":
        return {}, False
    if method == "tools/list":
        return {"tools": TOOLS}, False
    if method == "tools/call":
        value = call_tool(params.get("name", ""), params.get("arguments") or {})
        return {"content": [{"type": "text", "text": json.dumps(value, indent=2)}]}, False
    if method == "resources/list":
        return {"resources": []}, False
    if method == "resources/templates/list":
        return {"resourceTemplates": []}, False
    if method == "prompts/list":
        return {"prompts": []}, False
    if method == "logging/setLevel":
        return {}, False
    if method == "shutdown":
        return {}, True
    raise ValueError(f"unsupported method: {method}")


def process(request, mode: str = "content-length"):
    request_id = request.get("id")
    try:
        result, should_exit = handle_request(request)
        if request_id is not None and result is not None:
            send_message({"jsonrpc": "2.0", "id": request_id, "result": result}, mode)
        return should_exit
    except Exception as exc:
        if request_id is not None:
            send_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": str(exc)},
                },
                mode,
            )
        return False


while True:
    incoming = read_message()
    if incoming is None:
        break
    message, frame_mode = incoming
    if isinstance(message, list):
        for item in message:
            if process(item, frame_mode):
                raise SystemExit(0)
    elif process(message, frame_mode):
        break
