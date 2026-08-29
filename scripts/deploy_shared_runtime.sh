#!/usr/bin/env bash
set -euo pipefail

# Install the control-plane scripts once into the shared runtime directory.
# Existing Gemini, Antigravity, Codex, and OpenCode MCP entries point there;
# no per-agent bridge configuration is required.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(printenv AI_SECOND_BRAIN_ROOT 2>/dev/null || true)"
if [ -z "$ROOT" ]; then
  ROOT="$HOME/AI-Second-Brain"
fi
TARGET="$ROOT/.agent_mesh/scripts"
BACKUP="$ROOT/.agent_mesh/backups/agent-mesh-$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$TARGET" "$BACKUP"
for name in agent_mesh_core.py agent_mesh_service.py agent_mesh_mcp_stdio.py agent_mesh_adapters.py agent_mesh_autonomy.py configure_shared_agents.py sync_shared_catalog.py start_agent_mesh.sh agent_mesh.service; do
  if [ -e "$TARGET/$name" ]; then
    cp -a -- "$TARGET/$name" "$BACKUP/$name"
  fi
  cp -a -- "$SCRIPT_DIR/$name" "$TARGET/$name"
done

chmod 755 "$TARGET/agent_mesh_service.py" "$TARGET/agent_mesh_mcp_stdio.py" "$TARGET/agent_mesh_adapters.py" "$TARGET/agent_mesh_autonomy.py" "$TARGET/configure_shared_agents.py" "$TARGET/sync_shared_catalog.py" "$TARGET/start_agent_mesh.sh"
chmod 664 "$TARGET/agent_mesh.service"
AI_SECOND_BRAIN_ROOT="$ROOT" python3 "$TARGET/sync_shared_catalog.py"
python3 "$TARGET/configure_shared_agents.py"
printf 'Shared Agent Mesh runtime installed at %s\n' "$TARGET"
printf 'Previous runtime backed up at %s\n' "$BACKUP"
printf 'Restart the shared Agent Mesh service once to load the update.\n'
