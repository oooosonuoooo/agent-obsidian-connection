#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
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
    if body:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


TOOLS = [
    {
        "name": "agent_mesh_health",
        "description": "Check Agent Mesh health.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "agent_mesh_list_agents",
        "description": "List registered Agent Mesh agents.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "agent_mesh_send_message",
        "description": "Send a message through Agent Mesh.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_agent": {"type": "string"},
                "to_agent": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["to_agent", "subject", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "agent_mesh_create_handoff",
        "description": "Create an Agent Mesh handoff request.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_agent": {"type": "string"},
                "to_agent": {"type": "string"},
                "request": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["from_agent", "to_agent", "request"],
            "additionalProperties": False,
        },
    },
]


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
        "serverInfo": {"name": "agent-mesh-stdio", "version": "1.0.1"},
    }


def call_tool(name: str, arguments: dict):
    if name == "agent_mesh_health":
        return http("GET", "/health")
    if name == "agent_mesh_list_agents":
        return http("GET", "/agents")
    if name == "agent_mesh_send_message":
        payload = dict(arguments)
        payload.setdefault("from_agent", "mcp-client")
        return http("POST", "/messages", payload)
    if name == "agent_mesh_create_handoff":
        return http("POST", "/handoff", arguments)
    raise ValueError(f"unknown tool: {name}")


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
