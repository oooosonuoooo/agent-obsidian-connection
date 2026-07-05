#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(os.environ.get("AI_SECOND_BRAIN_ROOT", Path.home() / "AI-Second-Brain")).resolve()
VAULT = Path(os.environ.get("OBSIDIAN_VAULT_PATH", ROOT / "AI-Second-Brain-Vault")).resolve()
MESH = os.environ.get("AGENT_MESH_BASE_URL", "http://127.0.0.1:17860").rstrip("/")
FRIDAY = os.environ.get("FRIDAY_LOCAL_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
STATUS_NOTE = VAULT / "08_Inbox/friday_bridge_status.md"
AGENT_NOTE = VAULT / "01_Agents/Friday.md"


def load_env_value(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    for path in (ROOT / ".env.local", Path.home() / "airllm/.env"):
        if not path.exists():
            continue
        for line in path.read_text(errors="ignore").splitlines():
            item = line.strip()
            if item.startswith("export "):
                item = item[7:]
            if item.startswith(name + "="):
                return item.split("=", 1)[1].strip().strip("'\"")
    return None


def request_json(method: str, url: str, body=None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read().decode()
        if not payload:
            return {}
        return json.loads(payload)


def check_url(url: str, token: str | None = None) -> tuple[bool, str]:
    try:
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=8) as response:
            return 200 <= response.status < 500, str(response.status)
    except Exception as exc:
        return False, type(exc).__name__


def write_notes(friday_ok: bool, mesh_ok: bool, register_ok: bool) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    STATUS_NOTE.parent.mkdir(parents=True, exist_ok=True)
    AGENT_NOTE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_NOTE.write_text(
        "\n".join(
            [
                "# Friday Bridge Status",
                "",
                f"- Last heartbeat: {now}",
                f"- Friday web: {'ok' if friday_ok else 'failed'}",
                f"- Agent Mesh: {'ok' if mesh_ok else 'failed'}",
                f"- Friday registration: {'ok' if register_ok else 'failed'}",
                f"- Friday URL: `{FRIDAY}`",
                f"- Agent Mesh URL: `{MESH}`",
                f"- Obsidian vault: `{VAULT}`",
                "",
                "Secrets are referenced only through local environment variables and are not written here.",
                "",
            ]
        )
    )
    AGENT_NOTE.write_text(
        "\n".join(
            [
                "---",
                "type: agent",
                "status: active",
                f"registered_at: {now}",
                f"last_seen_at: {now}",
                "---",
                "# Agent Profile: Friday",
                "",
                "## Metadata",
                "- **Provider**: Local FRIDAY / airllm",
                "- **Type**: local assistant",
                "- **Status**: active",
                f"- **Last Seen**: {now}",
                "",
                "## Capabilities",
                "```json",
                json.dumps(
                    {
                        "agent_mesh": True,
                        "memory_core": True,
                        "obsidian_vault": True,
                        "web_ui": True,
                    },
                    indent=2,
                ),
                "```",
                "",
                "## Limitations",
                "Friday API routes require FRIDAY_WEB_TOKEN; token values are never copied to Obsidian.",
                "",
                "## Bridge Status",
                f"- Last bridge heartbeat: {now}",
                f"- Agent Mesh: `{MESH}`",
                f"- Obsidian vault: `{VAULT}`",
                f"- Friday web: `{FRIDAY}`",
                "- Other agents should use Agent Mesh handoffs/messages addressed to `Friday`.",
                "",
            ]
        )
    )


def heartbeat() -> None:
    mesh_token = load_env_value("AGENT_MESH_TOKEN")
    friday_token = load_env_value("FRIDAY_WEB_TOKEN")
    friday_ok, _ = check_url(FRIDAY + "/", friday_token)
    mesh_ok, _ = check_url(MESH + "/health")
    register_ok = False
    if mesh_token:
        try:
            request_json(
                "POST",
                MESH + "/agents/register",
                {
                    "name": "Friday",
                    "provider": "Local FRIDAY / airllm",
                    "type": "local assistant",
                    "capabilities_json": json.dumps(
                        {
                            "agent_mesh": True,
                            "obsidian_vault": True,
                            "web_ui": True,
                            "local_private": True,
                        }
                    ),
                    "limitations": "Requires local Friday web service and local token reference.",
                    "status": "active" if friday_ok else "degraded",
                },
                mesh_token,
            )
            register_ok = True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            register_ok = False
    write_notes(friday_ok, mesh_ok, register_ok)


def main() -> None:
    interval = int(os.environ.get("FRIDAY_SECOND_BRAIN_BRIDGE_INTERVAL", "60"))
    while True:
        heartbeat()
        time.sleep(max(interval, 15))


if __name__ == "__main__":
    main()
