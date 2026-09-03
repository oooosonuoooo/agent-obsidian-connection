# Agent Obsidian Connection

> **⚠️ Security Warning:** Never commit real API keys, tokens, passwords, or secrets to this repository. Use the provided `.env.example` as a template and keep your real `.env.local` file local and out of Git.

---

## Project Overview

**Agent Obsidian Connection** is a local-first, privacy-focused **multi-agent coordination layer** that connects AI agents (Friday, Claude, Gemini, Codex, NVIDIA NIM, and others) through a shared memory and task management system.

The system consists of two core components:

1. **Agent Mesh** — A lightweight local HTTP service (Python, SQLite) that provides:
   - Agent registration and discovery
   - Message passing between agents
   - Durable multi-agent orchestration (DAGs, ACKs, results, verification, retries)
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
- 🧭 **Durable orchestration** — lead-agent planning, capability routing, task DAGs, ACK/result protocol, verification, retries, and reassignment
- ♾️ **Recursive delegation** — every real worker can create bounded same-run child DAGs, suspend, resume with verified evidence, and release/reacquire artifact locks
- 🤖 **Autonomous one-objective workflow** — any connected agent can lead; the supervisor plans, consults specialists, dispatches real providers, audits every task, revises failures, integrates evidence, and waits for genuine blockers
- 🔌 **Provider adapter registry** — automatically discovers installed Gemini, OpenCode, Claude/FCC, Codex, Kilo, Friday, and Ollama workers; also supports registered HTTP/MCP adapters and cooperative MCP workers
- 🧾 **Traceable final reports** — every autonomous result includes the objective, agents involved, tasks, ACK/result/audit evidence, files, tests, warnings, and handoffs
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

See [ORCHESTRATION.md](ORCHESTRATION.md) for the failure diagnosis, shared-runtime deployment, protocol, API, and worker contract.

## Autonomous operation

After the shared runtime is deployed, a substantive request received by any
MCP-connected agent follows the same workflow:

```text
one user objective
        |
        v
receiving agent becomes lead
        |
        v
plan -> specialist consultation -> capability routing -> real execution
        |                                                        |
        +---------------- audit/revise/retry --------------------+
        |
        v
verified integration -> one final evidence-backed report
```

The lead calls `agent_mesh_start_autonomous_run` once and waits with
`agent_mesh_wait_autonomous_run`. The shared supervisor persists the plan and
task DAG, leases work, sends ACK/result protocol messages, maintains heartbeats,
reassigns failed work within its retry budget, audits submitted results, and
integrates only verified task evidence. This applies to research, design,
coding, testing, security, documentation, deployment, data, and operations
work; the planner chooses the needed task types.

No per-agent task-bus configuration is needed when the clients already use the
shared bridge at `~/AI-Second-Brain/.agent_mesh/scripts/agent_mesh_mcp_stdio.py`.
The MCP initialization instructions carry the lead/wait contract to every
client. Reload an existing MCP session after deployment. A local CLI with a
known profile is discovered automatically (including installed Codex, Cursor,
Kilo, and Kiro CLIs); an agent with no invokable adapter
stays a cooperative worker and must poll, ACK, execute, and submit its real
result. The supervisor reports `WAITING` rather than inventing a response.
`deploy_shared_runtime.sh` also runs the idempotent client configurator for
Gemini/Antigravity, Codex, OpenCode, Kilo, Cursor, Windsurf/Cascade, and Kiro;
existing provider settings are preserved and changed client files are backed
up under `.agent_mesh/backups/`.

The Cursor and Kiro headless adapters use a completed local CLI login or the
owner-provided `CURSOR_API_KEY` and `KIRO_API_KEY` references in the service
environment; Gemini likewise uses its cached Google CLI login, a
non-interactive API key, or Vertex AI credentials. The service keeps an
installed-but-unauthenticated CLI queued instead of opening a browser or
claiming that it can execute unattended.

Deployment also imports the canonical vault MCP and skill registries into the
shared catalog. Only safe metadata and credential references are imported;
provider execution and secret values remain local to the authorized owner.

All connected clients also share a federated capability catalog. Use
`agent_mesh_list_shared_capabilities` (or `GET /shared/capabilities`) to see
the MCP servers/tools and skills published by every agent. To use one, put its
name in a task's `required_tools` or `required_skills`; the supervisor routes
that task to the authorized publishing agent. This shares discoverability and
execution through the owner without copying credentials or silently granting
another client permissions.

Every worker receives the same recursive delegation contract. Simple work can
finish directly; a useful split returns `action: "delegate"` with a bounded
child DAG, `join_policy` (`all_success` or `all_settled`), and an idempotency
key. Children stay in the original run and are verified before the parent is
continued. The default limits are depth 3, eight children per batch, three
batches per task, and 64 tasks per run. Child output is untrusted evidence, not
control instructions. See [ORCHESTRATION.md](ORCHESTRATION.md) for the REST,
MCP, lease, cancellation, and restart-recovery contract.

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

# Durable orchestration settings (safe defaults)
# export AGENT_ACK_TIMEOUT=30
# export AGENT_EXECUTION_TIMEOUT=1800
# export AGENT_MAX_RETRIES=2
# export AGENT_RETRY_BACKOFF=2
# export AGENT_HEARTBEAT_TIMEOUT=120
# export MAX_PARALLEL_AGENT_TASKS=8
# export MAX_DELEGATION_DEPTH=3
# export MAX_DELEGATION_CHILDREN=8
# export MAX_DELEGATION_BATCHES_PER_TASK=3
# export MAX_TASKS_PER_RUN=64
# export AGENT_MESH_REAPER_INTERVAL=1

# Autonomous supervisor settings (safe defaults)
# export AGENT_MESH_AUTONOMY_ENABLED=1
# export AGENT_MESH_AUTONOMY_INTERVAL=1
# export AGENT_MESH_AUTONOMY_MAX_WORKERS=4
# export AGENT_MESH_AUTONOMY_MAX_ROUNDS=3
# export AGENT_MESH_AUTONOMY_COMMAND_TIMEOUT=1800
# Optional identity used by the shared MCP bridge when a caller omits lead_agent
# export AGENT_MESH_AGENT_NAME=MyAgent
# Optional default workspace/lead for HTTP callers
# export AGENT_MESH_WORKSPACE=/path/to/workspace
# export AGENT_MESH_DEFAULT_LEAD=orchestrator
# Gemini CLI approval mode used by the built-in adapter
# export AGENT_MESH_GEMINI_APPROVAL_MODE=yolo
# Optional local Ollama worker settings
# export LOCAL_LLM_MODEL=qwen2.5-coder:7b-instruct-q4_K_M
# export LOCAL_LLM_OLLAMA_ENDPOINT=http://127.0.0.1:11434/api/chat
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

### Shared runtime for every configured agent

Gemini/Antigravity, Codex, OpenCode, Kilo, Cursor, Windsurf/Cascade, and Kiro
use one common bridge under
`~/AI-Second-Brain/.agent_mesh/scripts/`. Install the runtime once after
cloning; individual agents do not need separate orchestration settings:

```bash
bash scripts/deploy_shared_runtime.sh
bash ~/AI-Second-Brain/.agent_mesh/scripts/start_agent_mesh.sh
```

Reload an already-open MCP session so it discovers the updated tools. Agents
with a cooperative GUI worker remain eligible while they publish a current
heartbeat. Supervisor-owned provider adapters are marked `autonomy_ready` and
can be invoked without a permanently running GUI session. The runtime itself
discovers available provider adapters; only custom providers need a one-time
registration.

### Option 3: systemd user service (persistent after login and reboot)

```bash
# Install the canonical user service
mkdir -p ~/.config/systemd/user/
cp scripts/agent_mesh.service ~/.config/systemd/user/ai-second-brain-agent-mesh.service

# Enable it once; user-systemd will start it after future logins/reboots
systemctl --user daemon-reload
systemctl --user enable ai-second-brain-agent-mesh.service
systemctl --user start ai-second-brain-agent-mesh.service

# Check status
systemctl --user status ai-second-brain-agent-mesh.service
```

`scripts/deploy_shared_runtime.sh` performs this enablement automatically. User
lingering must be enabled for the service to start before an interactive shell
is opened; on this machine it is already enabled (`Linger=yes`).

### Option 4: Auto-start on shell login (optional fallback)

Add to `~/.bashrc` or `~/.zshrc`:

```bash
[ -f "/path/to/agent-obsidian-connection/scripts/autostart.sh" ] && \
  source "/path/to/agent-obsidian-connection/scripts/autostart.sh"
```

---

## How Agents Connect With Each Other

All agent-to-agent communication flows through the **Agent Mesh REST API** at `http://127.0.0.1:17860`.

### Start one autonomous objective

The MCP-capable path is the normal path for every substantive request:

```text
agent_mesh_start_autonomous_run
  objective: "Build and verify the requested feature"
  workspace: "/path/to/project"
  lead_agent: "the receiving agent"   # optional when AGENT_MESH_AGENT_NAME is set

agent_mesh_wait_autonomous_run
  autonomous_run_id: "returned-id"
```

The returned report is not considered complete unless the linked run shows
completed tasks, accepted verification, and final integration. Use
`agent_mesh_get_autonomous_run` for live progress,
`agent_mesh_resume_autonomous_run` after a real provider becomes available, and
`agent_mesh_cancel_autonomous_run` when the objective is no longer wanted.

Useful REST equivalents are:

```bash
curl -s -X POST http://127.0.0.1:17860/autonomous/runs \
  -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"objective":"Build and verify the requested feature","workspace":"/path/to/project","lead_agent":"MyAgent"}'

curl -s http://127.0.0.1:17860/autonomous/adapters \
  -H "Authorization: Bearer ${AGENT_MESH_TOKEN}"
```

`GET /autonomous/adapters` exposes only non-secret adapter metadata. Built-in
profiles are enabled when their local executable exists. A custom provider can
be registered once through `agent_mesh_register_agent` (or
`POST /agents/register`) with an `autonomy_adapter` of kind `command`, `http`,
or `mcp`; command arguments are tokenized without a shell and credentials are
referenced by environment-variable name.

### Agent Registration

Every new agent must register itself first:

```bash
curl -s -X POST http://127.0.0.1:17860/agents/register \
  -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "MyAgent",
    "provider": "Claude/Gemini/Local/etc",
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
- `agent_mesh_list_capabilities` — Discover capability and workload metadata
- `agent_mesh_list_shared_capabilities` — Discover federated agents, MCP tools, and skills
- `agent_mesh_list_shared_tools` / `agent_mesh_list_shared_skills` — Read the safe shared catalogs
- `agent_mesh_register_shared_mcp_server` / `agent_mesh_register_shared_skill` — Publish capabilities for the team
- `agent_mesh_send_message` — Send a message to an agent
- `agent_mesh_create_handoff` — Create a task handoff request
- `agent_mesh_register_agent` — Register a custom agent/adapter once for the shared team
- `agent_mesh_start_autonomous_run` — Start the one-objective lead/planning/execution loop
- `agent_mesh_wait_autonomous_run` — Wait for verified completion or a genuine blocker
- `agent_mesh_get_autonomous_run` / `agent_mesh_list_autonomous_runs` — Read durable progress and reports
- `agent_mesh_resume_autonomous_run` / `agent_mesh_cancel_autonomous_run` — Recover or stop objectives
- `agent_mesh_list_adapters` — Inspect real CLI/HTTP/MCP/cooperative adapter availability
- `agent_mesh_create_orchestration_run` — Create an explicit task DAG
- `agent_mesh_poll_tasks` — Deliver a real task to a worker agent
- `agent_mesh_ack_task` — Acknowledge or reject a task request
- `agent_mesh_task_progress` — Report progress and refresh the lease
- `agent_mesh_submit_task_result` — Return a structured worker result
- `agent_mesh_verify_task` — Accept or request a revision
- `agent_mesh_finalize_run` — Store the lead's integrated final result
- `agent_mesh_cancel_task` / `agent_mesh_cancel_run` — Cancel work safely
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
| GET | `/agents/{agent}/capabilities` | Bearer | Read capability and health metadata |
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
| GET | `/capabilities` | Bearer | Read the capability inventory |
| GET | `/shared/capabilities` | Bearer | Read federated agents, MCP servers/tools, and skills |
| GET | `/shared/tools` | Bearer | List safe published tools |
| GET | `/shared/skills` | Bearer | List safe published skills |
| POST | `/orchestration/runs` | Bearer | Create and dispatch an explicit task plan |
| GET | `/orchestration/runs/{id}` | Bearer | Read run state, results, and trace events |
| POST | `/orchestration/runs/{id}/advance` | Bearer | Reconcile and dispatch runnable tasks |
| POST | `/orchestration/runs/{id}/finalize` | Bearer | Store the lead's integrated result |
| POST | `/orchestration/runs/{id}/cancel` | Bearer | Cancel pending and active work |
| GET | `/autonomous/runs` | Bearer | List high-level autonomous objectives |
| POST | `/autonomous/runs` | Bearer | Start one objective for autonomous planning and execution |
| GET | `/autonomous/runs/{id}` | Bearer | Read autonomous state, linked run, evidence, and final report |
| POST | `/autonomous/runs/{id}/resume` | Bearer | Resume a waiting/blocked objective with new plan or provider context |
| POST | `/autonomous/runs/{id}/cancel` | Bearer | Cancel an autonomous objective and active delegated work |
| GET | `/autonomous/adapters` | Bearer | List non-secret real adapter availability |
| POST | `/tasks/poll` | Bearer | Deliver and lease a request to a worker |
| POST | `/tasks/{id}/ack` | Bearer | Accept or reject a task request |
| POST | `/tasks/{id}/progress` | Bearer | Publish progress and refresh activity |
| POST | `/tasks/{id}/result` | Bearer | Submit a structured worker result |
| POST | `/tasks/{id}/error` | Bearer | Report failure and schedule recovery |
| POST | `/tasks/{id}/verify` | Bearer | Accept or request a revision |
| POST | `/tasks/{id}/cancel` | Bearer | Cancel one task and notify its worker |
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
├── ORCHESTRATION.md           # Durable task protocol and shared deployment
│
├── scripts/
│   ├── agent_mesh_core.py         # SQLite state machine and queue
│   ├── agent_mesh_adapters.py     # Real CLI/HTTP/MCP/cooperative adapter registry
│   ├── agent_mesh_autonomy.py     # Autonomous planner/worker/audit/integration supervisor
│   ├── configure_shared_agents.py # Idempotent shared API/MCP client configurator
│   ├── sync_shared_catalog.py     # Bootstrap canonical vault MCP/skill metadata
│   ├── agent_mesh_service.py     # Core HTTP server + SQLite backend
│   ├── agent_mesh_mcp_stdio.py   # MCP stdio bridge for Agent Mesh
│   ├── obsidian_vault_mcp_stdio.py  # MCP stdio bridge for Obsidian vault
│   ├── friday_second_brain_bridge.py  # Friday ↔ Agent Mesh heartbeat bridge
│   ├── start_agent_mesh.sh       # Start script
│   ├── deploy_shared_runtime.sh   # One-time shared runtime deployment
│   ├── autostart.sh              # Shell login auto-start hook
│   └── agent_mesh.service        # systemd user service definition
│
├── tests/
│   └── test_agent_mesh.py       # Regression, HTTP, migration, and MCP tests
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
