# Auto-Connect Integration Guide

## For Future Agents

Every agent joining this Second Brain must:

1. **Register itself** in `01_Agents/<agent-name>.md`
2. **Connect to Agent Mesh** at `http://127.0.0.1:17860`
3. **Connect to Obsidian MCP** at `https://127.0.0.1:27124/mcp/`

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

## Obsidian Integration

All agents share the same vault at `~/AI-Second-Brain/AI-Second-Brain-Vault`

Use MCP endpoints to read/write/search vault content.

## Handshake Protocol

When agent starts:
1. Read this vault to discover system
2. Register in `01_Agents/`
3. Announce presence via Agent Mesh `/messages` to `any-capable-agent`
4. Update its status to `active`