#!/usr/bin/env bash
set -euo pipefail

ROOT="${AI_SECOND_BRAIN_ROOT:-$HOME/AI-Second-Brain}"
mkdir -p "$ROOT/.agent_mesh/logs"

if ss -ltn "sport = :17860" | grep -q LISTEN; then
  printf 'Agent Mesh already listening on 127.0.0.1:17860\n'
  exit 0
fi

nohup "$ROOT/.agent_mesh/scripts/run_agent_mesh.sh" >"$ROOT/.agent_mesh/logs/agent_mesh.log" 2>&1 < /dev/null &
printf '%s\n' "$!" > "$ROOT/.agent_mesh/agent_mesh.pid"
printf 'Agent Mesh started on 127.0.0.1:17860\n'
