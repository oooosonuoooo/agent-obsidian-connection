#!/bin/bash
# Auto-start Agent Mesh on shell login
# Update the path below to match where you installed agent-obsidian-connection

AGENT_MESH_SCRIPTS="${AI_SECOND_BRAIN_ROOT:-$HOME/AI-Second-Brain}/.agent_mesh/scripts"

if ! pgrep -f "agent_mesh_service.py" > /dev/null 2>&1; then
    echo "Starting Agent Mesh..."
    "$AGENT_MESH_SCRIPTS/start_agent_mesh.sh"
fi