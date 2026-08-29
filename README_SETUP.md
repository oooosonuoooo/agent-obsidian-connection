# AI Second Brain Setup

## Overview

This system provides a local multi-agent coordination layer using:
- Obsidian vault for shared memory and context
- Agent Mesh SQLite database for task/message/handoff management
- MCP registry for shared tool access

## Paths

- Root: `~/AI-Second-Brain`
- Vault: `~/AI-Second-Brain/AI-Second-Brain-Vault`
- Agent Mesh DB: `~/AI-Second-Brain/.agent_mesh/agent_mesh.sqlite`
- Agent Mesh Config: `~/AI-Second-Brain/.agent_mesh/config.json`

## Getting Started

1. Open the Obsidian vault at `AI-Second-Brain-Vault`
2. Read the active `01_System/Operating_Rules.md`
3. Install the shared Agent Mesh runtime once with `bash scripts/deploy_shared_runtime.sh` (this also connects supported local clients to the shared API bridge)
4. Start the shared Agent Mesh service (if implemented)
5. Have agents onboard via `CONNECT_NEW_AI_AGENT.md`; existing clients use the shared bridge

For substantive work, the receiving agent is automatically the lead. It submits
one objective through the autonomous Agent Mesh tools; the shared supervisor
plans, consults specialists, routes tasks to real adapters, audits/revises
results, retries/reassigns failures, and returns one verified final report.
No per-agent task protocol is required. Installed Gemini, Codex, Kilo, OpenCode,
Claude/FCC, and Friday CLIs are discovered automatically. GUI-only agents are
supported as cooperative workers through the durable poll/ACK/result protocol.
Deployment also bootstraps the shared catalog from the canonical vault MCP and
skill registries using safe metadata only.

## Agent Mesh Endpoints

If the Agent Mesh service is implemented:
- Health: `GET http://127.0.0.1:17860/health`
- Register agent: `POST http://127.0.0.1:17860/agents/register`
- Messages: `POST http://127.0.0.1:17860/messages`
- Tasks: `POST http://127.0.0.1:17860/tasks`
- Orchestration: `POST http://127.0.0.1:17860/orchestration/runs`
- Worker queue: `POST http://127.0.0.1:17860/tasks/poll`
- Shared capability catalog: `GET http://127.0.0.1:17860/shared/capabilities`
- Shared tools: `GET http://127.0.0.1:17860/shared/tools`
- Shared skills: `GET http://127.0.0.1:17860/shared/skills`
- Autonomous adapters: `GET http://127.0.0.1:17860/autonomous/adapters`
- Start objective: `POST http://127.0.0.1:17860/autonomous/runs`
- Read/wait objective: `GET http://127.0.0.1:17860/autonomous/runs/{id}`

## MCP Endpoints

- Obsidian MCP: `https://127.0.0.1:27124/mcp/`
- Agent Mesh MCP: `http://127.0.0.1:17860/mcp/`

The Agent Mesh MCP bridge exposes `agent_mesh_start_autonomous_run`,
`agent_mesh_wait_autonomous_run`, `agent_mesh_get_autonomous_run`,
`agent_mesh_resume_autonomous_run`, `agent_mesh_cancel_autonomous_run`, and
`agent_mesh_list_adapters` for this workflow. Shared tool and skill discovery
is available through `agent_mesh_list_shared_capabilities`,
`agent_mesh_list_shared_tools`, and `agent_mesh_list_shared_skills`; request a
published capability with `required_tools` or `required_skills` in the
autonomous task plan so the authorized owner executes it.
