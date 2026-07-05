# Agent Obsidian Connection

> **⚠️ Security Warning:** Never commit real API keys, tokens, passwords, or secrets to this repository. Use the provided `.env.example` as a template and keep your real `.env.local` file local and out of Git.

---

## Project Overview

**Agent Obsidian Connection** is a local-first, privacy-focused **multi-agent coordination layer** that connects AI agents (Friday, Claude, Gemini, Codex, NVIDIA NIM, and others) through a shared memory and task management system.

The system consists of two core components:

1. **Agent Mesh** — A lightweight local HTTP service (Python, SQLite) that provides:
   - Agent registration and discovery
   - Message passing between agents
   - Task lifecycle management (create, claim, heartbeat, release)
   - Handoff coordination between agents
   - Skill and MCP server registry
   - Obsidian vault synchronization

2. **Obsidian Vault Integration** — A structured local [Obsidian](https://obsidian.md/) vault used as shared long-term memory:
   - Agent profiles and status
   - Task notes and context
   - Memory and decisions
   - API/tool registries
   - Inbox for inter-agent communication

Both components expose **MCP (Model Context Protocol) stdio bridges** for AI agents that support MCP, as well as a **REST API** for direct HTTP integration.

---

## Features

- 🕸️ **Agent Mesh REST API** — lightweight local HTTP server on `127.0.0.1:17860`
- 🔗 **MCP stdio bridges** — drop-in MCP servers for `agent-mesh` and `obsidian-vault`
- 📓 **Obsidian vault sync** — automatic Markdown notes for agents, tasks, messages, and memory
- 🔒 **Token-authenticated** — all sensitive endpoints require a local bearer token
- 🌉 **Friday bridge** — dedicated heartbeat bridge for the Friday local LLM assistant
- 🔄 **Task leasing** — agents can claim, heartbeat, and release tasks to avoid conflicts
- 💾 **SQLite backend** — lightweight, no external database required
- 🛡️ **Zero-trust design** — all services bind to `127.0.0.1` only, no external exposure
- 🧠 **Shared memory** — any agent can read and write to the shared vault and memory store
- 🏥 **Health endpoint** — unauthenticated `/health` for easy status checks

---

## Requirements

| Requirement | Version / Notes |
|-------------|----------------|
| Python | 3.10+ |
| Obsidian | Latest (for vault UI) — optional |
| OS | Linux, macOS, or WSL2 |
| Disk | < 50 MB (SQLite + vault markdown files) |

No additional pip packages are required for the core Agent Mesh service — it uses only the Python standard library.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/oooosonuoooo/agent-obsidian-connection.git
cd agent-obsidian-connection
```

### 2. Set up the directory structure

```bash
# Create runtime directories
mkdir -p ~/.agent_mesh/logs ~/.agent_mesh/inbox ~/.agent_mesh/outbox
mkdir -p ~/.agent_mesh/locks ~/.agent_mesh/exports
```

### 3. Configure environment variables

```bash
cp .env.example .env.local
nano .env.local   # Fill in your AGENT_MESH_TOKEN and paths
```

### 4. Initialize the database

The database is created automatically when the service starts. No manual migration is needed.

### 5. Set up Obsidian vault

```bash
# Create the vault structure (or open an existing vault in Obsidian)
mkdir -p ~/AI-Second-Brain/AI-Second-Brain-Vault/{00_System,01_Agents,02_Projects,03_Memory,04_Tasks,05_Decisions,06_Codebase_Context,07_API_and_Tools,08_Inbox,09_Daily,99_Archive}
```

---

## Configuration

All configuration is done via environment variables. Copy `.env.example` to `.env.local`:

```bash
# Agent Mesh authentication token (required for all endpoints except /health)
# Generate: python3 -c "import secrets; print(secrets.token_hex(32))"
export AGENT_MESH_TOKEN=your_secret_token_here

# Port (default: 17860)
export AGENT_MESH_PORT=17860

# Obsidian vault path (defaults to ~/AI-Second-Brain/AI-Second-Brain-Vault)
# export OBSIDIAN_VAULT_PATH=/path/to/your/vault

# Friday bridge settings (if using Friday local LLM)
# export FRIDAY_LOCAL_BASE_URL=http://127.0.0.1:8765
# export FRIDAY_WEB_TOKEN=your_friday_web_token_here
```

> 🔑 **Generate secure tokens:** `python3 -c "import secrets; print(secrets.token_hex(32))"`

---

## How to Start the Agent Mesh Service

### Option 1: Direct Python execution

```bash
source .env.local
python3 scripts/agent_mesh_service.py
```

### Option 2: Using the start script

```bash
bash scripts/start_agent_mesh.sh
```

### Option 3: systemd user service (persistent after login)

```bash
# Copy the service file
mkdir -p ~/.config/systemd/user/
cp scripts/agent_mesh.service ~/.config/systemd/user/
# Edit paths if needed
nano ~/.config/systemd/user/agent_mesh.service

# Enable and start
systemctl --user daemon-reload
systemctl --user enable agent_mesh
systemctl --user start agent_mesh

# Check status
systemctl --user status agent_mesh
```

### Option 4: Auto-start on shell login

Add to `~/.bashrc` or `~/.zshrc`:

```bash
[ -f "/path/to/agent-obsidian-connection/scripts/autostart.sh" ] && \
  source "/path/to/agent-obsidian-connection/scripts/autostart.sh"
```

---

## How Agents Connect With Each Other

All agent-to-agent communication flows through the **Agent Mesh REST API** at `http://127.0.0.1:17860`.

### Agent Registration

Every new agent must register itself first:

```bash
curl -s -X POST http://127.0.0.1:17860/agents/register \
  -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "MyAgent",
    "provider": "Claude/Gemini/Local/etc",
    "type": "assistant",
    "capabilities": {"code": true, "memory": true},
    "status": "active"
  }'
```

### Sending Messages Between Agents

```bash
curl -s -X POST http://127.0.0.1:17860/messages \
  -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "from_agent": "AgentA",
    "to_agent": "AgentB",
    "subject": "Help needed",
    "body": "Please review the task context in 04_Tasks/task_5.md"
  }'
```

### Creating and Claiming Tasks

```bash
# Create a task
curl -s -X POST http://127.0.0.1:17860/tasks \
  -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"title": "Refactor auth module", "priority": "high", "project": "MyProject"}'

# Claim a task (ID=1) with a 2-hour lease
curl -s -X POST http://127.0.0.1:17860/tasks/1/claim \
  -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"agent": "Claude", "lease_hours": 2}'
```

### MCP Integration (for MCP-capable agents)

Add to your agent's MCP configuration:

```json
{
  "mcpServers": {
    "agent-mesh": {
      "type": "stdio",
      "command": "python3",
      "args": ["/path/to/agent-obsidian-connection/scripts/agent_mesh_mcp_stdio.py"]
    },
    "obsidian-vault": {
      "type": "stdio",
      "command": "python3",
      "args": ["/path/to/agent-obsidian-connection/scripts/obsidian_vault_mcp_stdio.py"]
    }
  }
}
```

Available MCP tools:
- `agent_mesh_health` — Check service health
- `agent_mesh_list_agents` — List registered agents
- `agent_mesh_send_message` — Send a message to an agent
- `agent_mesh_create_handoff` — Create a task handoff request
- `obsidian_vault_status` — Check vault status
- `obsidian_list_notes` — List all vault notes
- `obsidian_read_note` — Read a specific note
- `obsidian_write_note` — Write or append to a note

---

## How Agents Connect With Obsidian

The Obsidian vault at `~/AI-Second-Brain/AI-Second-Brain-Vault` serves as the **shared persistent memory** for all agents.

### Vault Structure

```
AI-Second-Brain-Vault/
├── 00_System/           # Rules, indexes, control panel
│   ├── Operating_Rules.md
│   ├── Agent_Control_Panel.md
│   ├── Auto_Connect_Guide.md
│   └── Memory_Index.md
├── 01_Agents/           # Agent profiles (auto-synced from Mesh)
├── 02_Projects/         # Project context and notes
├── 03_Memory/           # Durable memories
├── 04_Tasks/            # Task notes (auto-synced from Mesh)
├── 05_Decisions/        # Decision records
├── 06_Codebase_Context/ # Code context for LLM agents
├── 07_API_and_Tools/    # Skill registry, MCP server registry
├── 08_Inbox/            # Temporary messages and status notes
├── 09_Daily/            # Daily notes
└── 99_Archive/          # Archived content
```

### Auto-sync Behavior

When you register an agent, create a task, or send a message via the REST API or MCP tools, the Agent Mesh service **automatically creates or updates** the corresponding Markdown note in the Obsidian vault.

### MCP Obsidian Bridge

For MCP-capable agents, use the `obsidian-vault` stdio bridge:

```python
# The bridge exposes read/write access to the vault
# All paths are relative to the vault root
# Only .md files within the vault are accessible (security restriction)
```

### Direct File Access

Agents with filesystem access can also read and write vault notes directly:

```bash
# Read a note
cat ~/AI-Second-Brain/AI-Second-Brain-Vault/00_System/Operating_Rules.md

# Write an agent profile
cat > ~/AI-Second-Brain/AI-Second-Brain-Vault/01_Agents/MyAgent.md << 'EOF'
---
type: agent
status: active
---
# Agent Profile: MyAgent
...
EOF
```

---

## How to Verify the Setup

```bash
# 1. Check Agent Mesh is running
curl -s http://127.0.0.1:17860/health | python3 -m json.tool

# 2. List registered agents
curl -s http://127.0.0.1:17860/agents \
  -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" | python3 -m json.tool

# 3. Check vault exists
ls ~/AI-Second-Brain/AI-Second-Brain-Vault/00_System/

# 4. Test MCP bridge
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}' | \
  python3 scripts/agent_mesh_mcp_stdio.py 2>/dev/null | head -1
```

---

## How to Troubleshoot Connection Issues

### Agent Mesh not responding

```bash
# Check if it's running
pgrep -f agent_mesh_service.py
# Or check the port
ss -ltn | grep 17860

# View logs
tail -50 ~/.agent_mesh/logs/agent_mesh.log

# Restart
kill $(cat ~/.agent_mesh/agent_mesh.pid) 2>/dev/null
bash scripts/start_agent_mesh.sh
```

### Authentication failures (401 errors)

```bash
# Verify token is set
echo "Token length: ${#AGENT_MESH_TOKEN}"
# Test authentication
curl -s -w "\n%{http_code}" http://127.0.0.1:17860/agents \
  -H "Authorization: Bearer ${AGENT_MESH_TOKEN}"
```

### MCP bridge connection issues

```bash
# Ensure Python 3.10+ is used
python3 --version

# Check bridge is executable
chmod +x scripts/agent_mesh_mcp_stdio.py
chmod +x scripts/obsidian_vault_mcp_stdio.py

# Verify AGENT_MESH_TOKEN is available in environment
echo "Token set: $(test -n "$AGENT_MESH_TOKEN" && echo YES || echo NO)"
```

### Obsidian vault not found

```bash
# Check vault path
echo $OBSIDIAN_VAULT_PATH
ls ~/AI-Second-Brain/AI-Second-Brain-Vault/

# Create missing directories
mkdir -p ~/AI-Second-Brain/AI-Second-Brain-Vault/{00_System,01_Agents,02_Projects,03_Memory,04_Tasks,05_Decisions,07_API_and_Tools,08_Inbox}
```

### Friday bridge failing

```bash
# Check Friday web service is running
curl -s http://127.0.0.1:8765/ 2>/dev/null && echo "Friday OK" || echo "Friday not running"

# Start Friday (from friday-local-llm repo)
cd /path/to/friday-local-llm
source .venv/bin/activate
python3 friday_web.py &
```

---

## REST API Reference

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/health` | None | Service health and counts |
| GET | `/agents` | Bearer | List all registered agents |
| POST | `/agents/register` | Bearer | Register or update an agent |
| GET | `/messages/{agent}` | Bearer | Get messages for an agent |
| POST | `/messages` | Bearer | Send a message |
| POST | `/handoff` | Bearer | Create a handoff request |
| GET | `/handoffs` | Bearer | List all handoffs |
| GET | `/tasks` | Bearer | List all tasks |
| POST | `/tasks` | Bearer | Create a task |
| POST | `/tasks/{id}/claim` | Bearer | Claim a task with lease |
| POST | `/tasks/{id}/heartbeat` | Bearer | Update task heartbeat |
| POST | `/tasks/{id}/release` | Bearer | Release a task lease |
| GET | `/tasks/stalled` | Bearer | Get stalled tasks |
| POST | `/memory` | Bearer | Store a memory entry |
| GET | `/memory/search?q=...` | Bearer | Search memory |
| GET | `/skills` | Bearer | List all skills |
| POST | `/skills/register` | Bearer | Register a skill |
| GET | `/mcp/servers` | Bearer | List MCP servers |
| POST | `/mcp/servers/register` | Bearer | Register an MCP server |

---

## Folder Structure

```
agent-obsidian-connection/
├── .env.example              # Safe example config (no real secrets)
├── .gitignore                # Excludes secrets, DB, logs, runtime files
├── LICENSE                   # MIT License
├── README.md                 # This file
├── CONNECT_NEW_AI_AGENT.md   # Full onboarding guide for new agents
├── README_SETUP.md           # Quick setup overview
├── SECURITY.md               # Security guidelines
├── BACKUP_AND_RESTORE.md     # Backup and restore instructions
│
├── scripts/
│   ├── agent_mesh_service.py     # Core HTTP server + SQLite backend
│   ├── agent_mesh_mcp_stdio.py   # MCP stdio bridge for Agent Mesh
│   ├── obsidian_vault_mcp_stdio.py  # MCP stdio bridge for Obsidian vault
│   ├── friday_second_brain_bridge.py  # Friday ↔ Agent Mesh heartbeat bridge
│   ├── start_agent_mesh.sh       # Start script
│   ├── autostart.sh              # Shell login auto-start hook
│   └── agent_mesh.service        # systemd user service definition
│
└── vault-templates/
    ├── Operating_Rules.md        # Agent operating rules template
    └── Auto_Connect_Guide.md     # Auto-connect integration guide
```

---

## Security Notes

> **⚠️ Important security practices:**

- **Never commit `.env.local`** — it contains your `AGENT_MESH_TOKEN`; always keep it in `.gitignore`
- **Local-only binding** — Agent Mesh binds to `127.0.0.1:17860` only; never expose to the internet
- **Use a strong token** — generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`
- **No secrets in vault** — the Obsidian vault should never contain actual API keys, tokens, or passwords
- **Database is local** — `agent_mesh.sqlite` contains agent messages and task data; do not commit it
- **Token references only** — config files use `${VARIABLE_NAME}` references, never real values
- **Read `SECURITY.md`** for full security guidelines before deploying

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

## Related Projects

- **[friday-local-llm](https://github.com/oooosonuoooo/friday-local-llm)** — Local LLM assistant that connects to this coordination layer

---

*This is a local-only multi-agent coordination system. All services are bound to localhost and require a local authentication token.*
