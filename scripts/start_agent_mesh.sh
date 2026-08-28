#!/usr/bin/env bash
set -euo pipefail

ROOT="$(printenv AI_SECOND_BRAIN_ROOT 2>/dev/null || true)"
if [ -z "$ROOT" ]; then
  ROOT="$HOME/AI-Second-Brain"
fi
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SERVICE_SCRIPT="$SCRIPT_DIR/agent_mesh_service.py"
if [ -f "$ROOT/.agent_mesh/scripts/agent_mesh_service.py" ]; then
  SERVICE_SCRIPT="$ROOT/.agent_mesh/scripts/agent_mesh_service.py"
fi
LOG_DIR="$ROOT/.agent_mesh/logs"
mkdir -p "$LOG_DIR"

PORT="$(printenv AGENT_MESH_PORT 2>/dev/null || true)"
if [ -z "$PORT" ]; then
  PORT=17860
fi
if ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
  printf 'Agent Mesh already listening on 127.0.0.1:%s\n' "$PORT"
  exit 0
fi

nohup /usr/bin/env python3 "$SERVICE_SCRIPT" >"$LOG_DIR/agent_mesh.log" 2>&1 < /dev/null &
printf '%s\n' "$!" > "$ROOT/.agent_mesh/agent_mesh.pid"
printf 'Agent Mesh started on 127.0.0.1:%s\n' "$PORT"
