#!/usr/bin/env python3
"""Install the shared Agent Mesh API bridge into local AI client configs.

This is intentionally idempotent and additive.  It updates only the two
shared bridge entries, keeps all unrelated provider settings intact, and
creates a recoverable backup before changing an existing client file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def root_path() -> Path:
    return Path(
        os.environ.get("AI_SECOND_BRAIN_ROOT", str(Path.home() / "AI-Second-Brain"))
    ).expanduser()


def bridges(root: Path) -> dict[str, str]:
    scripts = root / ".agent_mesh" / "scripts"
    return {
        "agent-mesh": str(scripts / "agent_mesh_mcp_stdio.py"),
        "obsidian-vault": str(scripts / "obsidian_vault_mcp_stdio.py"),
    }


def _strip_jsonc(text: str) -> str:
    """Remove JSONC comments outside strings and trailing commas."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index)
            if newline < 0:
                break
            output.append("\n")
            index = newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                break
            index = end + 2
            continue
        output.append(char)
        index += 1
    return re.sub(r",\s*([}\]])", r"\1", "".join(output))


def _load_document(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return {}, None
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        try:
            value = json.loads(_strip_jsonc(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None, "unparseable JSON/JSONC"
    if not isinstance(value, dict):
        return None, "root is not an object"
    return value, None


def _server(root_key: str, name: str, path: str) -> dict[str, Any]:
    if root_key == "mcp":
        return {
            "type": "local",
            "enabled": True,
            "command": ["/usr/bin/python3", path],
        }
    return {
        "command": "/usr/bin/python3",
        "args": [path],
    }


def _is_current(root_key: str, value: Any, path: str) -> bool:
    if not isinstance(value, dict):
        return False
    command = value.get("command")
    args = value.get("args")
    if root_key == "mcp":
        return isinstance(command, list) and path in command
    return isinstance(args, list) and path in args


def _atomic_write(path: Path, value: dict[str, Any], mode: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + path.name + ".", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _backup(path: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / path.name)


def _update_json(
    path: Path,
    root_key: str,
    bridge_paths: dict[str, str],
    backup_dir: Path,
    dry_run: bool,
) -> str:
    document, error = _load_document(path)
    if document is None:
        return "skipped: " + str(error)
    servers = document.get(root_key)
    if servers is None:
        servers = {}
        document[root_key] = servers
    if not isinstance(servers, dict):
        return "skipped: " + root_key + " is not an object"
    changed = False
    for name, bridge_path in bridge_paths.items():
        current = servers.get(name)
        if _is_current(root_key, current, bridge_path):
            continue
        servers[name] = _server(root_key, name, bridge_path)
        changed = True
    if not changed:
        return "already connected"
    if dry_run:
        return "would update"
    if path.exists():
        _backup(path, backup_dir)
        mode = path.stat().st_mode & 0o777
    else:
        mode = 0o600
    _atomic_write(path, document, mode)
    return "updated"


def _toml_has_server(text: str, name: str) -> bool:
    return re.search(r"(?m)^\[mcp_servers\." + re.escape(name) + r"\]\s*$", text) is not None


def _update_codex(
    path: Path,
    bridge_paths: dict[str, str],
    backup_dir: Path,
    dry_run: bool,
) -> str:
    if not path.exists():
        return "not present"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "skipped: unreadable"
    missing = [name for name in bridge_paths if not _toml_has_server(text, name)]
    if not missing:
        return "already connected"
    blocks: list[str] = []
    for name in missing:
        blocks.append(
            "[mcp_servers." + name + "]\n"
            + 'command = "/usr/bin/python3"\n'
            + 'args = [ "' + bridge_paths[name] + '" ]\n'
        )
    updated = text.rstrip() + "\n\n" + "\n".join(blocks)
    if dry_run:
        return "would update"
    _backup(path, backup_dir)
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + path.name + ".", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(updated + "\n")
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return "updated"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = root_path()
    bridge_paths = bridges(root)
    backup_dir = (
        root
        / ".agent_mesh"
        / "backups"
        / ("client-config-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    )
    json_configs = (
        (Path.home() / ".gemini/settings.json", "mcpServers"),
        (Path.home() / ".gemini/config/mcp_config.json", "mcpServers"),
        (Path.home() / ".config/opencode/opencode.json", "mcp"),
        (Path.home() / ".config/kilo/kilo.jsonc", "mcp"),
        (Path.home() / ".cursor/mcp.json", "mcpServers"),
        (Path.home() / ".codeium/windsurf/mcp_config.json", "mcpServers"),
        (Path.home() / ".kiro/settings/mcp.json", "mcpServers"),
    )
    for path, root_key in json_configs:
        status = _update_json(path, root_key, bridge_paths, backup_dir, args.dry_run)
        print(path, status)
    codex_status = _update_codex(
        Path.home() / ".codex/config.toml", bridge_paths, backup_dir, args.dry_run
    )
    print(Path.home() / ".codex/config.toml", codex_status)
    if not backup_dir.exists() and not args.dry_run:
        try:
            backup_dir.rmdir()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
