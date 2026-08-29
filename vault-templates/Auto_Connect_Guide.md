# Auto-Connect Integration Guide

## For Future Agents

Every agent joining this Second Brain must:

1. **Discover the shared runtime** at `http://127.0.0.1:17860`
2. **Use the shared Agent Mesh MCP bridge** when MCP is available
3. **Advertise a profile/adapter only when the provider is not auto-discovered**

For every substantive user request, the receiving agent is the lead: call
`agent_mesh_start_autonomous_run` once, then wait with
`agent_mesh_wait_autonomous_run`. Known local CLIs are discovered automatically.
GUI-only agents remain truthful cooperative workers through the durable
poll/ACK/result protocol. Do not create a separate per-agent task bus.

All agents can discover the same published MCP tools and skills through
`agent_mesh_list_shared_capabilities`, `agent_mesh_list_shared_tools`, and
`agent_mesh_list_shared_skills`. Request a shared capability with
`required_tools` or `required_skills` in an autonomous task; the supervisor
routes execution to the authorized owner agent and preserves its local
credentials and permissions.

## Auto-Start Configuration

Add to shell profile (`~/.bashrc` or `~/.zshrc`):

```bash
# Auto-start Agent Mesh on login
[ -f "~/AI-Second-Brain/.agent_mesh/scripts/autostart.sh" ] && source "~/AI-Second-Brain/.agent_mesh/scripts/autostart.sh"
```

## Agent Mesh Endpoints

- Health: `GET http://127.0.0.1:17860/health`
- Register: `POST http://127.0.0.1:17860/agents/register`
- Messages: `GET/POST http://127.0.0.1:17860/messages`
- Tasks: `GET/POST http://127.0.0.1:17860/tasks`
- Skills: `GET/POST http://127.0.0.1:17860/skills`
- MCP Servers: `GET/POST http://127.0.0.1:17860/mcp/servers`
- Handoff: `POST http://127.0.0.1:17860/handoff`
- Heartbeat: `POST http://127.0.0.1:17860/tasks/{id}/heartbeat`
- Start objective: `POST http://127.0.0.1:17860/autonomous/runs`
- Read objective: `GET http://127.0.0.1:17860/autonomous/runs/{id}`
- Adapter inventory: `GET http://127.0.0.1:17860/autonomous/adapters`
- Shared capabilities: `GET http://127.0.0.1:17860/shared/capabilities`
- Shared tools: `GET http://127.0.0.1:17860/shared/tools`
- Shared skills: `GET http://127.0.0.1:17860/shared/skills`

## Obsidian Integration

All agents share the same vault at `~/AI-Second-Brain/AI-Second-Brain-Vault`

Use MCP endpoints to read/write/search vault content.

## Handshake Protocol

When agent starts:
1. Read this vault to discover system
2. Register in `01_Agents/`
3. Announce presence via Agent Mesh `/messages` to `any-capable-agent`
4. Update its status to `active`
