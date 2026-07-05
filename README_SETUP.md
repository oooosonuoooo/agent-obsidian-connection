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
2. Read `00_System/Operating_Rules.md`
3. Start the Agent Mesh service (if implemented)
4. Have agents onboard via `CONNECT_NEW_AI_AGENT.md`

## Agent Mesh Endpoints

If the Agent Mesh service is implemented:
- Health: `GET http://127.0.0.1:17860/health`
- Register agent: `POST http://127.0.0.1:17860/agents/register`
- Messages: `POST http://127.0.0.1:17860/messages`
- Tasks: `POST http://127.0.0.1:17860/tasks`

## MCP Endpoints

- Obsidian MCP: `https://127.0.0.1:27124/mcp/`
- Agent Mesh MCP: `http://127.0.0.1:17860/mcp/`