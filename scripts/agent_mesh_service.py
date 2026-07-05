#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Any

import re

ROOT = Path.home() / "AI-Second-Brain"
DB = ROOT / ".agent_mesh" / "agent_mesh.sqlite"
VAULT = ROOT / "AI-Second-Brain-Vault"
HOST = "127.0.0.1"
PORT = int(os.environ.get("AGENT_MESH_PORT", "17860"))


def load_env() -> None:
    for path in (ROOT / ".env.local", Path.home() / "airllm" / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
    if "AGENT_MESH_TOKEN" not in os.environ and os.environ.get("FRIDAY_WEB_TOKEN"):
        os.environ["AGENT_MESH_TOKEN"] = os.environ["FRIDAY_WEB_TOKEN"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init_db() -> None:
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, provider TEXT, type TEXT, capabilities_json TEXT, limitations TEXT, status TEXT, registered_at TEXT, last_seen_at TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_name ON agents(name);
            CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_agent TEXT, to_agent TEXT, task_id INTEGER, subject TEXT, body TEXT, status TEXT, created_at TEXT, read_at TEXT);
            CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, owner_agent TEXT, status TEXT, priority TEXT, project TEXT, context_path TEXT, result_path TEXT, last_heartbeat_at TEXT, last_active_agent TEXT, lease_owner TEXT, lease_expires_at TEXT, resume_packet_path TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS memory (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, category TEXT, body TEXT, source TEXT, confidence REAL, sensitivity TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS handoffs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, from_agent TEXT, to_agent TEXT, request TEXT, response TEXT, status TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS mcp_servers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, owner_agent TEXT, endpoint TEXT, transport TEXT, auth_ref TEXT, tools_json TEXT, safety_limits TEXT, status TEXT, last_verified_at TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_name ON mcp_servers(name);
            CREATE TABLE IF NOT EXISTS skills (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, owner_agent TEXT, skill_type TEXT, input_format TEXT, output_format TEXT, invocation_method TEXT, limitations TEXT, status TEXT, last_verified_at TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_name_owner ON skills(name, owner_agent);
            """
        )


def rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def jdump(value) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


# Obsidian Vault Synchronization Helpers

def sync_agent_to_obsidian(agent: dict[str, Any]) -> None:
    agent_name = agent["name"]
    file_path = VAULT / "01_Agents" / f"{agent_name}.md"
    template = f"""---
type: agent
status: {agent.get("status", "active")}
registered_at: {agent.get("registered_at", now())}
last_seen_at: {agent.get("last_seen_at", now())}
---
# Agent Profile: {agent_name}

## Metadata
- **Provider**: {agent.get("provider", "Unknown")}
- **Type**: {agent.get("type", "Unknown")}
- **Status**: {agent.get("status", "active")}
- **Last Seen**: {agent.get("last_seen_at", now())}

## Capabilities
```json
{json.dumps(json.loads(agent.get("capabilities_json", "{}")), indent=2)}
```

## Limitations
{agent.get("limitations") or "None specified."}
"""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(template, encoding="utf-8")
        update_agent_control_panel()
    except Exception as e:
        print(f"Error syncing agent to obsidian: {e}")

def update_agent_control_panel() -> None:
    panel_path = VAULT / "00_System" / "Agent_Control_Panel.md"
    if not panel_path.exists():
        return
    try:
        with conn() as c:
            cur = c.execute("SELECT * FROM agents ORDER BY name")
            agents = rows(cur)
        
        table_lines = [
            "| Agent Name | Provider / Model | Type | Status | Last Seen |",
            "|---|---|---|---|---|",
        ]
        for ag in agents:
            table_lines.append(
                f"| [[{ag['name']}]] | {ag.get('provider', 'Unknown')} | {ag.get('type', 'Unknown')} | {ag.get('status', 'active')} | {ag.get('last_seen_at')} |"
            )
        table_content = "\n".join(table_lines)
        content = panel_path.read_text(encoding="utf-8")
        pattern = r"(## Connected Agents Registry\n+).*?(?=\n+##|$)"
        if re.search(pattern, content, re.DOTALL):
            new_content = re.sub(pattern, f"\\1{table_content}\n", content, flags=re.DOTALL)
            panel_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        print(f"Error updating agent control panel: {e}")

def sync_task_to_obsidian(task: dict[str, Any]) -> None:
    task_id = task["id"]
    task_title = task["title"]
    file_path = VAULT / "04_Tasks" / f"task_{task_id}.md"
    template = f"""---
type: task
id: {task_id}
status: {task.get("status", "pending")}
priority: {task.get("priority", "medium")}
owner_agent: {task.get("owner_agent") or "none"}
lease_owner: {task.get("lease_owner") or "none"}
lease_expires: {task.get("lease_expires_at") or "none"}
last_heartbeat: {task.get("last_heartbeat_at") or "none"}
created_at: {task.get("created_at", now())}
updated_at: {task.get("updated_at", now())}
---
# Task {task_id}: {task_title}

## Description
- **Project**: {task.get("project") or "General"}
- **Status**: {task.get("status", "pending")}
- **Priority**: {task.get("priority", "medium")}
- **Owner Agent**: {task.get("owner_agent") or "None"}
- **Lease Owner**: {task.get("lease_owner") or "None"}
- **Lease Expires**: {task.get("lease_expires_at") or "None"}
- **Context Path**: {task.get("context_path") or "None"}
- **Result Path**: {task.get("result_path") or "None"}

## Goal
Verify or achieve the objective of this task.

## Resume Packet Path
{task.get("resume_packet_path") or "None"}
"""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(template, encoding="utf-8")
    except Exception as e:
        print(f"Error syncing task to obsidian: {e}")

def sync_memory_to_obsidian(memory: dict[str, Any]) -> None:
    inbox_path = VAULT / "03_Memory" / "Memory_Inbox.md"
    new_entry = f"""
### Memory: {memory["title"]}
- **Category**: {memory.get("category") or "General"}
- **Source**: {memory.get("source") or "Unknown"}
- **Confidence**: {memory.get("confidence") or "medium"}
- **Sensitivity**: {memory.get("sensitivity") or "medium"}
- **Created At**: {memory.get("created_at") or now()}

{memory["body"]}

---
"""
    try:
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        with open(inbox_path, "a", encoding="utf-8") as f:
            f.write(new_entry)
        update_memory_index(memory)
    except Exception as e:
        print(f"Error syncing memory to obsidian: {e}")

def update_memory_index(memory: dict[str, Any]) -> None:
    index_path = VAULT / "00_System" / "Memory_Index.md"
    if not index_path.exists():
        return
    try:
        line = f"- [{memory['title']}]({inbox_path}) | Category: {memory.get('category') or 'General'} | Source: {memory.get('source') or 'Unknown'} | {now()}\n"
        with open(index_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"Error updating memory index: {e}")

def sync_message_to_obsidian(msg: dict[str, Any]) -> None:
    log_path = VAULT / "08_Inbox" / "messages_log.md"
    new_entry = f"""
### Message {msg["id"]}: {msg["subject"]}
- **From**: {msg.get("from_agent") or "Unknown"}
- **To**: {msg["to_agent"]}
- **Task ID**: {msg.get("task_id") or "None"}
- **Status**: {msg.get("status") or "queued"}
- **Created At**: {msg.get("created_at") or now()}

**Body**:
{msg["body"]}

---
"""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(new_entry)
    except Exception as e:
        print(f"Error syncing message to obsidian: {e}")

def sync_skill_to_obsidian(skill: dict[str, Any]) -> None:
    registry_path = VAULT / "07_API_and_Tools" / "Skill_Registry.md"
    if not registry_path.exists():
        return
    new_entry = f"\n| {skill['name']} | {skill.get('owner_agent') or 'shared'} | {skill.get('skill_type') or 'Unknown'} | {skill.get('input_format') or 'JSON'} | {skill.get('output_format') or 'JSON'} | {skill.get('invocation_method') or 'REST'} | active |\n"
    try:
        with open(registry_path, "a", encoding="utf-8") as f:
            f.write(new_entry)
    except Exception as e:
        print(f"Error syncing skill to obsidian: {e}")

def sync_mcp_server_to_obsidian(mcp: dict[str, Any]) -> None:
    registry_path = VAULT / "07_API_and_Tools" / "MCP_Server_Registry.md"
    if not registry_path.exists():
        return
    new_entry = f"\n| {mcp['name']} | {mcp.get('owner_agent') or 'shared'} | {mcp.get('endpoint') or 'Unknown'} | {mcp.get('transport') or 'stdio'} | {mcp.get('auth_ref') or 'None'} | {mcp.get('tools_json') or '[]'} | active |\n"
    try:
        with open(registry_path, "a", encoding="utf-8") as f:
            f.write(new_entry)
    except Exception as e:
        print(f"Error syncing MCP server to obsidian: {e}")

# Secret Redactor helper
def redact_secrets(text: Any) -> str:
    if not isinstance(text, str):
        return str(text)
    result = text
    # Redact Authorization header values, tokens, keys
    patterns = [
        r'(Authorization:\s*Bearer\s+)[A-Za-z0-9_\-\.]+',
        r'(?i)(api_key\s*[:=]\s*["\']?)[A-Za-z0-9_\-\.]+(["\']?)',
        r'(?i)(token\s*[:=]\s*["\']?)[A-Za-z0-9_\-\.]+(["\']?)',
        r'(?i)(password\s*[:=]\s*["\']?)[A-Za-z0-9_\-\.]+(["\']?)',
    ]
    for pattern in patterns:
        result = re.sub(pattern, r'\1[REDACTED]\2', result)
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode())

    def authed(self) -> bool:
        if self.path.startswith("/health") or self.path.startswith("/mcp/"):
            return True
        token = os.environ.get("AGENT_MESH_TOKEN")
        got = self.headers.get("Authorization", "")
        if not token:
            self.send({"detail": "AGENT_MESH_TOKEN is not configured"}, 503)
            return False
        if got != f"Bearer {token}":
            self.send({"detail": "Unauthorized"}, 401)
            return False
        return True

    def do_GET(self):
        if not self.authed():
            return
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        with conn() as c:
            if path == "/health":
                counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("agents", "messages", "tasks", "memory", "handoffs", "mcp_servers", "skills")}
                return self.send({"status": "ok", "time": now(), "host": HOST, "port": PORT, "vault": str(VAULT), "counts": counts})
            if path == "/agents":
                return self.send(rows(c.execute("SELECT * FROM agents ORDER BY name")))
            if path.startswith("/messages/"):
                agent = path.rsplit("/", 1)[-1]
                return self.send(rows(c.execute("SELECT * FROM messages WHERE to_agent=? ORDER BY created_at DESC", (agent,))))
            if path == "/tasks":
                return self.send(rows(c.execute("SELECT * FROM tasks ORDER BY updated_at DESC")))
            if path == "/tasks/stalled":
                return self.send(rows(c.execute("SELECT * FROM tasks WHERE status NOT IN ('complete','completed','done') AND lease_owner IS NOT NULL AND lease_expires_at < ?", (now(),))))
            if path == "/skills":
                return self.send(rows(c.execute("SELECT * FROM skills ORDER BY name, owner_agent")))
            if path == "/mcp/servers":
                return self.send(rows(c.execute("SELECT * FROM mcp_servers ORDER BY name")))
            if path == "/handoffs":
                return self.send(rows(c.execute("SELECT * FROM handoffs ORDER BY created_at DESC")))
            if path == "/memory/search":
                q = f"%{(qs.get('q') or [''])[0]}%"
                return self.send(rows(c.execute("SELECT * FROM memory WHERE title LIKE ? OR body LIKE ? OR source LIKE ? ORDER BY updated_at DESC", (q, q, q))))
            if path == "/mcp/":
                return self.send({"status": "metadata-only", "bridges": ["agent_mesh_mcp_stdio.py", "obsidian_vault_mcp_stdio.py"]})
        self.send({"detail": "not found"}, 404)

    def do_POST(self):
        if not self.authed():
            return
        path = urlparse(self.path).path
        data = self.body()
        stamp = now()
        with conn() as c:
            if path == "/agents/register":
                c.execute("INSERT INTO agents (name,provider,type,capabilities_json,limitations,status,registered_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET provider=excluded.provider,type=excluded.type,capabilities_json=excluded.capabilities_json,limitations=excluded.limitations,status=excluded.status,last_seen_at=excluded.last_seen_at", (data["name"], data.get("provider"), data.get("type"), jdump(data.get("capabilities") or data.get("capabilities_json")), data.get("limitations"), data.get("status", "active"), stamp, stamp))
                row = dict(c.execute("SELECT * FROM agents WHERE name=?", (data["name"],)).fetchone())
                sync_agent_to_obsidian(row)
                return self.send(row)
            if path == "/messages":
                cur = c.execute("INSERT INTO messages (from_agent,to_agent,task_id,subject,body,status,created_at) VALUES (?,?,?,?,?,?,?)", (data.get("from_agent"), data["to_agent"], data.get("task_id"), data["subject"], data["body"], data.get("status", "queued"), stamp))
                row = dict(c.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone())
                sync_message_to_obsidian(row)
                return self.send(row)
            if path == "/handoff":
                cur = c.execute("INSERT INTO handoffs (task_id,from_agent,to_agent,request,response,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (data.get("task_id"), data["from_agent"], data["to_agent"], data["request"], data.get("response"), data.get("status", "requested"), stamp, stamp))
                return self.send(dict(c.execute("SELECT * FROM handoffs WHERE id=?", (cur.lastrowid,)).fetchone()))
            if path == "/tasks":
                cur = c.execute("INSERT INTO tasks (title,owner_agent,status,priority,project,context_path,result_path,last_heartbeat_at,last_active_agent,lease_owner,lease_expires_at,resume_packet_path,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (data["title"], data.get("owner_agent"), data.get("status", "pending"), data.get("priority", "medium"), data.get("project"), data.get("context_path"), data.get("result_path"), data.get("last_heartbeat_at"), data.get("last_active_agent"), data.get("lease_owner"), data.get("lease_expires_at"), data.get("resume_packet_path"), stamp, stamp))
                row = dict(c.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone())
                sync_task_to_obsidian(row)
                return self.send(row)
            if path == "/memory":
                cur = c.execute("INSERT INTO memory (title,category,body,source,confidence,sensitivity,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (data["title"], data.get("category"), data["body"], data.get("source"), data.get("confidence"), data.get("sensitivity"), stamp, stamp))
                row = dict(c.execute("SELECT * FROM memory WHERE id=?", (cur.lastrowid,)).fetchone())
                sync_memory_to_obsidian(row)
                return self.send(row)
            if path == "/skills/register":
                c.execute("INSERT INTO skills (name,owner_agent,skill_type,input_format,output_format,invocation_method,limitations,status,last_verified_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(name,owner_agent) DO UPDATE SET skill_type=excluded.skill_type,input_format=excluded.input_format,output_format=excluded.output_format,invocation_method=excluded.invocation_method,limitations=excluded.limitations,status=excluded.status,last_verified_at=excluded.last_verified_at", (data["name"], data.get("owner_agent"), data.get("skill_type"), data.get("input_format"), data.get("output_format"), data.get("invocation_method"), data.get("limitations"), data.get("status", "active"), data.get("last_verified_at", stamp)))
                row = dict(c.execute("SELECT * FROM skills WHERE name=? AND COALESCE(owner_agent,'')=COALESCE(?, '')", (data["name"], data.get("owner_agent"))).fetchone())
                sync_skill_to_obsidian(row)
                return self.send(row)
            if path == "/mcp/servers/register":
                c.execute("INSERT INTO mcp_servers (name,owner_agent,endpoint,transport,auth_ref,tools_json,safety_limits,status,last_verified_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET owner_agent=excluded.owner_agent,endpoint=excluded.endpoint,transport=excluded.transport,auth_ref=excluded.auth_ref,tools_json=excluded.tools_json,safety_limits=excluded.safety_limits,status=excluded.status,last_verified_at=excluded.last_verified_at", (data["name"], data.get("owner_agent"), data.get("endpoint"), data.get("transport"), data.get("auth_ref"), jdump(data.get("tools") or data.get("tools_json")), data.get("safety_limits"), data.get("status", "active"), data.get("last_verified_at", stamp)))
                row = dict(c.execute("SELECT * FROM mcp_servers WHERE name=?", (data["name"],)).fetchone())
                sync_mcp_server_to_obsidian(row)
                return self.send(row)

            # Claim task
            match_claim = re.match(r'^/tasks/(\d+)/claim$', path)
            if match_claim:
                task_id = int(match_claim.group(1))
                agent = data.get("agent") or data.get("agent_name") or data.get("lease_owner")
                lease_seconds = int(data.get("lease_seconds", int(data.get("lease_hours", 1)) * 3600))
                expiry = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
                c.execute("UPDATE tasks SET lease_owner=?, lease_expires_at=?, last_active_agent=?, last_heartbeat_at=?, updated_at=? WHERE id=?", (agent, expiry, agent, stamp, stamp, task_id))
                row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row:
                    row_dict = dict(row)
                    sync_task_to_obsidian(row_dict)
                    return self.send(row_dict)
                return self.send({"status": "claimed", "id": task_id})

            # Release task
            match_release = re.match(r'^/tasks/(\d+)/release$', path)
            if match_release:
                task_id = int(match_release.group(1))
                c.execute("UPDATE tasks SET lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?", (stamp, task_id))
                row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row:
                    row_dict = dict(row)
                    sync_task_to_obsidian(row_dict)
                    return self.send(row_dict)
                return self.send({"status": "released", "id": task_id})

            # Heartbeat task
            match_hb = re.match(r'^/tasks/(\d+)/heartbeat$', path)
            if match_hb:
                task_id = int(match_hb.group(1))
                agent = data.get("agent") or data.get("agent_name")
                c.execute("UPDATE tasks SET last_heartbeat_at=?, last_active_agent=?, updated_at=? WHERE id=?", (stamp, agent, stamp, task_id))
                row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row:
                    row_dict = dict(row)
                    sync_task_to_obsidian(row_dict)
                    return self.send(row_dict)
                return self.send({"status": "heartbeat_recorded", "id": task_id})

        self.send({"detail": "not found"}, 404)


if __name__ == "__main__":
    load_env()
    init_db()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
