#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


VAULT = Path(
    os.environ.get(
        "OBSIDIAN_VAULT_PATH",
        str(Path.home() / "AI-Second-Brain/AI-Second-Brain-Vault"),
    )
).resolve()


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


def safe_note_path(relative_path: str) -> Path:
    path = (VAULT / relative_path).resolve()
    if not str(path).startswith(str(VAULT) + os.sep) or path.suffix != ".md":
        raise ValueError("invalid vault markdown path")
    return path


def notes(limit: int = 200):
    return sorted(
        str(path.relative_to(VAULT))
        for path in VAULT.rglob("*.md")
        if ".obsidian" not in path.parts
    )[:limit]


TOOLS = [
    {
        "name": "obsidian_vault_status",
        "description": "Report local Obsidian vault status.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "obsidian_list_notes",
        "description": "List Markdown notes in the local Obsidian vault.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000}},
            "additionalProperties": False,
        },
    },
    {
        "name": "obsidian_read_note",
        "description": "Read a Markdown note from the local Obsidian vault.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "obsidian_write_note",
        "description": "Write or append a Markdown note inside the local Obsidian vault.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["replace", "append"]},
            },
            "required": ["path", "content"],
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
        "serverInfo": {"name": "obsidian-vault-stdio", "version": "1.0.1"},
    }


def call_tool(name: str, arguments: dict):
    if name == "obsidian_vault_status":
        return {"vault": str(VAULT), "exists": VAULT.exists(), "notes": len(notes(100000))}
    if name == "obsidian_list_notes":
        return notes(int(arguments.get("limit", 200)))
    if name == "obsidian_read_note":
        path = safe_note_path(arguments["path"])
        return {"path": arguments["path"], "content": path.read_text(errors="ignore")}
    if name == "obsidian_write_note":
        path = safe_note_path(arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = arguments["content"]
        if arguments.get("mode") == "append" and path.exists():
            path.write_text(path.read_text(errors="ignore") + "\n" + content)
        else:
            path.write_text(content)
        return {"path": arguments["path"], "written": True}
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
