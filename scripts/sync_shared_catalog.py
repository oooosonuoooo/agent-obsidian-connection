#!/usr/bin/env python3
"""Synchronize the canonical vault capability registries into Agent Mesh.

Only registry metadata is imported.  Credential values are never read: auth
fields remain references such as ``EXA_API_KEY`` and provider execution stays
with the declared owner agent.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Any, Iterable

from agent_mesh_core import MeshStore, Settings, load_env, public_endpoint, utc_now


AGENT_ALIASES = (
    ("gemini antigravity", "gemini-antigravity"),
    ("antigravity", "gemini-antigravity"),
    ("gemini cli", "Gemini"),
    ("gemini", "Gemini"),
    ("claude-fcc", "Claude-FCC"),
    ("claude code", "Claude-FCC"),
    ("claude", "Claude-FCC"),
    ("opencode", "OpenCode"),
    ("codex", "Codex"),
    ("kilo", "Kilo"),
    ("friday", "Friday"),
    ("localllm", "LocalLLM"),
    ("local llm", "LocalLLM"),
)


def _cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [item.strip() for item in value.split("|")]


def _is_separator(cells: Iterable[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", item.strip()) for item in cells)


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("`", "")
    text = re.sub(r"[*_]", "", text)
    return text.strip()


def _markdown_table(path: Path, required_headers: set[str]) -> list[dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        headers = [_clean(item).lower() for item in _cells(line)]
        if not required_headers.issubset(set(headers)):
            continue
        if index + 1 >= len(lines) or not lines[index + 1].lstrip().startswith("|"):
            continue
        result: list[dict[str, str]] = []
        for row_line in lines[index + 2 :]:
            if not row_line.lstrip().startswith("|"):
                break
            cells = _cells(row_line)
            if _is_separator(cells):
                continue
            if not any(cells):
                continue
            cells += [""] * (len(headers) - len(cells))
            result.append(
                {
                    headers[position]: _clean(cells[position])
                    for position in range(min(len(headers), len(cells)))
                }
            )
        return result
    return []


def _owner(source: str, known: set[str]) -> str:
    text = _clean(source).lower()
    if not text or text in {"none", "none yet", "registry only"}:
        return ""
    if "all agents" in text:
        return "shared"
    # Preserve the first listed source agent.  A source such as
    # ``Codex, Antigravity -> All`` means Codex owns the currently verified
    # capability and Antigravity is a target for later federation.
    for chunk in re.split(r",|;|→|->", text):
        chunk = chunk.strip()
        for alias, name in AGENT_ALIASES:
            if alias in chunk and (name in known or name in {"shared", "LocalLLM"}):
                return name
    return ""


def _status(value: str) -> str:
    text = _clean(value).lower()
    if "offline" in text or "❌" in value:
        return "offline"
    if "available" in text or "🔵" in value:
        return "available"
    if "active" in text or "✅" in value:
        return "active"
    return "unverified"


def _auth_ref(value: str) -> str:
    text = _clean(value)
    if not text or text.lower() in {"none", "no", "n/a"}:
        return ""
    # Keep only a variable/reference name, never a URL query or a value.
    matches = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text)
    return matches[-1] if matches else "configured"


def _http_endpoint(location: str) -> str:
    match = re.search(r"https?://[^\s`]+", location)
    return public_endpoint(match.group(0).rstrip(">),.;")) if match else ""


def _registry_paths(root: Path) -> tuple[Path, Path]:
    vault = root / "AI-Second-Brain-Vault"
    return (
        vault / "04_Ecosystem/Registries/MCP_Registry.md",
        vault / "04_Ecosystem/Registries/Skill_Registry_Index.md",
    )


def synchronize(store: MeshStore, root: Path, *, dry_run: bool = False) -> tuple[int, int]:
    mcp_path, skill_path = _registry_paths(root)
    mcp_rows = _markdown_table(
        mcp_path,
        {"name", "description", "source agent(s)", "live file location", "status"},
    )
    skill_rows = _markdown_table(
        skill_path,
        {"name", "description", "source agent(s)", "live file location", "status"},
    )
    known = {str(item.get("name")) for item in store.list_agents()}
    known.update({name for _, name in AGENT_ALIASES})
    existing_servers = {
        str(item.get("name")): item for item in store.list_shared_mcp_servers()
    }
    existing_skills = {
        (str(item.get("name")), str(item.get("owner_agent") or "")): item
        for item in store.list_shared_skills()
    }
    mcp_count = 0
    skill_count = 0
    for row in mcp_rows:
        name = row.get("name", "").strip()
        if not name:
            continue
        existing = existing_servers.get(name, {})
        owner = _owner(row.get("source agent(s)", ""), known) or str(
            existing.get("owner_agent") or ""
        )
        status = _status(row.get("status", ""))
        location = row.get("live file location", "")
        tools = existing.get("tools")
        if not isinstance(tools, list):
            tools = []
        if not any(
            (item.get("name") if isinstance(item, dict) else item) == name
            for item in tools
        ):
            tools = list(tools) + [name]
        payload = {
            "name": name,
            "owner_agent": owner,
            "endpoint": _http_endpoint(location) or existing.get("endpoint") or "",
            "transport": "http" if _http_endpoint(location) else "stdio",
            "auth_ref": _auth_ref(row.get("auth", "")),
            "tools": tools,
            "safety_limits": existing.get("safety_limits") or {},
            "status": status,
            "last_verified_at": utc_now(),
        }
        if not dry_run:
            store.register_shared_mcp_server(payload)
        mcp_count += 1
    for row in skill_rows:
        name = row.get("name", "").strip()
        if not name:
            continue
        owner = _owner(row.get("source agent(s)", ""), known)
        existing = existing_skills.get((name, owner), {})
        payload = {
            "name": name,
            "owner_agent": owner,
            "skill_type": row.get("description", "")[:500],
            "input_format": "",
            "output_format": "",
            "invocation_method": row.get("live file location", ""),
            "limitations": row.get("depends on", ""),
            "status": _status(row.get("status", "")),
            "last_verified_at": utc_now(),
        }
        if not dry_run:
            store.register_shared_skill(payload)
        skill_count += 1
    return mcp_count, skill_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = (args.root or Path(os.environ.get("AI_SECOND_BRAIN_ROOT", str(Path.home() / "AI-Second-Brain")))).expanduser().resolve()
    os.environ["AI_SECOND_BRAIN_ROOT"] = str(root)
    load_env(root)
    settings = Settings.from_env()
    store = MeshStore(settings)
    mcp_count, skill_count = synchronize(store, root, dry_run=args.dry_run)
    prefix = "would synchronize" if args.dry_run else "synchronized"
    print(f"{prefix} {mcp_count} MCP servers and {skill_count} skills from canonical registries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
