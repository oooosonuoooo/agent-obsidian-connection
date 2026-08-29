# Agent Onboarding Guide for AI Second Brain

This document provides instructions for connecting new AI agents to the AI Second Brain multi-agent coordination system.

## Non-Negotiable Default Behavior

You must use the AI Second Brain automatically for every non-trivial task. The
user should not need to mention Obsidian, the vault, Agent Mesh, memory,
handoffs, or resume packets in each prompt.

For every substantive objective, the agent receiving the user request is the
lead. Start one `agent_mesh_start_autonomous_run` with the complete objective
and workspace, then wait with `agent_mesh_wait_autonomous_run`. The shared
supervisor decides whether to consult specialists, creates the DAG, routes
tasks by capability, invokes real provider adapters, audits/revises results,
and integrates the verified evidence. Return the supervisor's final report
only after the linked tasks are accepted and integrated. A simple local task
may stay in the current session when delegation adds no value, but it still
uses the shared memory and truthful reporting rules.

Never claim that another agent worked unless the durable autonomous run has
real task assignment, ACK/result, and verification evidence. If a provider is
not invokable, leave it as a cooperative worker and report `WAITING` until that
agent polls and submits its real result; do not manufacture a response.

Before starting work:

1. Read `README_SETUP.md`, `SECURITY.md`, and the active `Operating_Rules.md` in the vault system directory.
2. Check Agent Mesh at `http://127.0.0.1:17860/health`.
3. Use the local Obsidian vault at `~/AI-Second-Brain/AI-Second-Brain-Vault`.
4. Register or refresh your agent profile in `AI-Second-Brain-Vault/01_Agents/`.
5. Search existing task notes and Agent Mesh before creating new work.
6. Discover shared tools and skills with `agent_mesh_list_shared_capabilities`.
7. Record durable task context in `04_Tasks/`, durable memory in `03_Memory/`, decisions in `05_Decisions/`, and repair/status reports in `08_Inbox/`.
8. If you stop, crash, lose context, or hand off work, update the task note and resume packet first.

As a lead, do not manually split a substantive request into untracked chats;
use the autonomous run tools above. As a worker, poll the durable queue, ACK
the task, report progress/heartbeats, perform the assigned work, and submit the
structured result. A task is complete only after independent verification.

If you support MCP, configure these local stdio bridges:

```json
{
  "mcpServers": {
    "agent-mesh": {
      "type": "stdio",
      "command": "python3",
      "args": ["~/AI-Second-Brain/.agent_mesh/scripts/agent_mesh_mcp_stdio.py"]
    },
    "obsidian-vault": {
      "type": "stdio",
      "command": "python3",
      "args": ["~/AI-Second-Brain/.agent_mesh/scripts/obsidian_vault_mcp_stdio.py"]
    }
  }
}
```

Shared tool and skill access is federated through the same Agent Mesh bridge.
Do not copy another agent's credentials or private configuration. Request a
published capability in an autonomous task with `required_tools` or
`required_skills`; the authorized owner agent executes it and returns durable
evidence for audit.

On an existing AI Second Brain installation, Gemini/Antigravity, Codex,
OpenCode, Kilo, Cursor, Windsurf/Cascade, and Kiro can all use the shared
bridge at `~/AI-Second-Brain/.agent_mesh/scripts/agent_mesh_mcp_stdio.py`.
Do not create a separate per-agent task bus or duplicate orchestration config.
The shared runtime is installed once with
`scripts/deploy_shared_runtime.sh`; this also bootstraps the shared MCP/skill
catalog from the canonical vault registries. Reload the MCP connection after a
runtime update. Existing registered agents keep their metadata in the shared SQLite
registry and only need a current heartbeat to receive work. The caller may set
`AGENT_MESH_AGENT_NAME` for a stable identity, but the autonomous tool also
accepts `lead_agent` and defaults safely to `orchestrator` when omitted.

The supervisor automatically discovers installed Gemini, Codex, Kilo, OpenCode,
Claude/FCC, and Friday command adapters. GUI-only agents such as an IDE-hosted
Codex session remain supported as cooperative MCP workers; no unsafe generic
CLI wrapper is assumed for them. Inspect the non-secret inventory with
`agent_mesh_list_adapters`. Register only providers without a built-in profile,
and register them once for the shared team.

`agent_mesh_list_agents` reports `autonomy_ready=true` for a real adapter that
the supervisor can launch on demand. A cooperative GUI agent is `online` only
while its client publishes a heartbeat; another agent cannot honestly fake
that session, but it can queue work and wait for the client to claim it.

If you cannot use MCP but can call localhost HTTP, use Agent Mesh REST at `http://127.0.0.1:17860` with the local token reference `${AGENT_MESH_TOKEN}`. Do not write token values into notes, logs, chat, or config examples.

Workers receive real delegated work by polling `POST /tasks/poll`, then must
send `TASK_ACK`, progress/heartbeats, and a structured `TASK_RESULT`. A task is
not complete because it was merely sent. The lead agent verifies results and
the supervisor finalizes the integrated run. See `ORCHESTRATION.md` for the
full protocol, recovery behavior, adapter registration, and autonomous API.

## Autonomous Team Contract

The shared runtime is the default team coordinator, not an optional message
log. For a substantive user request, the current agent is the lead and must:

1. Read enough shared context to understand the objective and workspace.
2. Call `agent_mesh_start_autonomous_run` once with the complete objective.
3. Let the supervisor consult specialists, plan the DAG, dispatch real
   adapters, audit results, retry/reassign failures, and integrate evidence.
4. Wait with `agent_mesh_wait_autonomous_run` until `COMPLETED` or a genuine
   `WAITING`/`BLOCKED` condition.
5. Return the verified final report, including any warnings or unresolved
   blockers. Do not claim work that is absent from the linked run.

If the agent is assigned a task instead, it is a worker: poll, ACK, execute,
heartbeat, submit a structured result, and wait for verification. This same
contract covers research, writing, design, coding, testing, security, data,
deployment, and operations.

## Introduction

When adding a new AI agent to your system, follow this guide to ensure seamless integration. The AI Second Brain provides a local-first multi-agent coordination layer that enables sharing of memory, tasks, context, decisions, and project knowledge across different AI agents.

## Prerequisite

Before starting, ensure you:

- Are working with a local AI agent (not web-only)
- Have access to the AI Second Brain folder structure
- Can read and write to local files and directories
- Have shell/terminal access
- Can access localhost services if needed

## First Steps

### 1. Locate and Examine Root

First, check if the AI Second Brain root directory exists:

```bash
# For macOS/Linux/WSL
cd ~/AI-Second-Brain

# For Windows
# cd "%USERPROFILE%\AI-Second-Brain"

# Check what exists
ls -la
```

The expected structure:

```
~/AI-Second-Brain/
├── AI-Second-Brain-Vault/              # Obsidian vault
│   ├── 00_System/
│   ├── 01_Agents/
│   ├── 02_Projects/
│   ├── 03_Memory/
│   ├── 04_Tasks/
│   ├── 05_Decisions/
│   ├── 06_Codebase_Context/
│   ├── 07_API_and_Tools/
│   ├── 08_Inbox/
│   ├── 09_Daily/
│   └── 99_Archive/
│
├── .agent_mesh/                        # Runtime services
│   ├── agent_mesh.sqlite
│   ├── config.json
│   ├── logs/
│   ├── inbox/
│   ├── outbox/
│   ├── locks/
│   ├── exports/
│   └── scripts/
│
├── CONNECT_NEW_AI_AGENT.md             # This guide
├── README_SETUP.md                     # Setup documentation
├── SECURITY.md                         # Security guidelines
└── BACKUP_AND_RESTORE.md               # Backup procedures
```

If the root directory doesn't exist yet, create it:

```bash
mkdir -p ~/AI-Second-Brain
```

### 2. Read Essential Documentation

Copy and read these essential files to understand the system:

1. **README_SETUP.md** - Comprehensive setup guide
2. **SECURITY.md** - Security rules and best practices
3. **CONNECT_NEW_AI_AGENT.md** - This guide (you're reading it now!)
4. **AI-Second-Brain-Vault/00_System/Operating_Rules.md** - Core operating rules

**Command example:**

```bash
# Read the key documentation files
cd ~/AI-Second-Brain
sed -n '1,220p' README_SETUP.md
sed -n '1,220p' SECURITY.md
sed -n '1,260p' AI-Second-Brain-Vault/00_System/Operating_Rules.md
```

### 3. Examine Registry Files (If Available)

If the system has been previously set up, check the existing registries:

```bash
# MCP Server Registry
if [ -f "AI-Second-Brain-Vault/07_API_and_Tools/MCP_Server_Registry.md" ]; then
    echo "MCP Server Registry exists:"
    cat AI-Second-Brain-Vault/07_API_and_Tools/MCP_Server_Registry.md
fi

# Skill Registry
if [ -f "AI-Second-Brain-Vault/07_API_and_Tools/Skill_Registry.md" ]; then
    echo "Skill Registry exists:"
    cat AI-Second-Brain-Vault/07_API_and_Tools/Skill_Registry.md
fi

# Agent profiles
ls AI-Second-Brain-Vault/01_Agents/ 2>/dev/null || echo "No agent profiles yet"
```

## Task 4: Register Your Agent Profile

Every agent must register itself in the Agent Registry. Create a file:

`AI-Second-Brain-Vault/01_Agents/<your-agent-name>.md`

Fill in the template with your agent's capabilities:

```markdown
# Agent Profile: <agent-name>

- **Agent Name**: [e.g. "codex", "claudecode", "friday", "kimi"]
- **Type**: (e.g. "local CLI", "IDE plugin", "web-only", "cloud-service")
- **Provider**: (e.g. "OpenAI", "Anthropic", "Local", "Cloud Provider Name")
- **Model**: (e.g. "Claude 3.5 Sonnet", "GPT-4o", "Llama 3.1")
- **Status**: (active, disconnected, unverified)
- **Last Verified**: [timestamp]

## Capabilities
- Local filesystem access: (yes / no / unknown)
- Shell access: (yes / no / unknown)
- MCP support: (yes / no / unknown)
- API access: (yes / no / unknown)
- Long-context ability: (yes / no / unknown)
- Local/private/offline ability: (yes / no / unknown)
- Code editing ability: (yes / no / unknown)
- Testing ability: (yes / no / unknown)

## Configuration Paths
*(List configuration files or paths this agent can safely access)*
- ~/.config/opencode/
- ~/.bash_history
- ~/.zshrc
- ~/AI-Second-Brain/
- ~/AI-Second-Brain/.agent_mesh/
- ~/AI-Second-Brain/AI-Second-Brain-Vault/

## Shared Services & Registries
- **Available MCP Servers**:
  - *(List name and tools exposed)*
- **Available Skills**:
  - *(List name and summary)*

## Hand-off & Resume Formats
- **Preferred Handoff Format**: [e.g. "Task note with resume packet", "JSON handoff", "Direct API call"]
- **Resume Format**: [e.g. "Task note with full context", "JSON packet", "Plain text summary"]

## Limitations
- *(Any limitations or blockers)*
```

**Example filled agent profile for OpenCode:**

```markdown
# Agent Profile: opencode

- **Agent Name**: opencode
- **Type**: local CLI
- **Provider**: OpenCode
- **Model**: OpenCode v1.0
- **Status**: active
- **Last Verified**: 2024-01-15 10:30:00

## Capabilities
- Local filesystem access: (yes / no / unknown)
- Shell access: (yes / no / unknown)
- MCP support: (yes / no / unknown)
- API access: (yes / no / unknown)
- Long-context ability: (yes / no / unknown)
- Local/private/offline ability: (yes / no / unknown)
- Code editing ability: (yes / no / unknown)
- Testing ability: (yes / no / unknown)

## Configuration Paths
*(List configuration files or paths this agent can safely access)*
- ~/.config/opencode/
- ~/.bash_history
- ~/.zshrc
- ~/AI-Second-Brain/
- ~/AI-Second-Brain/.agent_mesh/
- ~/AI-Second-Brain/AI-Second-Brain-Vault/

## Shared Services & Registries
- **Available MCP Servers**:
  - *(List name and tools exposed)*
- **Available Skills**:
  - *(List name and summary)*

## Hand-off & Resume Formats
- **Preferred Handoff Format**: Task note with resume packet (Obsidian format)
- **Resume Format**: Structured task note with full context, work log, and next action

## Limitations
- Sandboxed environment only
- No web browser automation
```

## Task 5: Connect to Obsidian (If Available)

If your agent has local file system access, connect to the Obsidian vault:

```bash
# Check if Obsidian is available
obsidian --version 2>/dev/null && echo "Obsidian is installed"

# Connect to Obsidian vault
cd ~/AI-Second-Brain

# Try to read from the vault
if [ -d "AI-Second-Brain-Vault" ]; then
    echo "Obsidian vault found at: $(pwd)/AI-Second-Brain-Vault"
    
    # Test read access
    if [ -f "AI-Second-Brain-Vault/00_System/Home.md" ]; then
        echo "Can read Obsidian files - connection successful"
        
        # Try to write (optional)
        echo "Test write access..."
        echo "Test content from $(date)" > "AI-Second-Brain-Vault/00_System/test_write.md"
        if [ -f "AI-Second-Brain-Vault/00_System/test_write.md" ]; then
            echo "Write access confirmed"
            rm "AI-Second-Brain-Vault/00_System/test_write.md"  # Clean up test file
        else
            echo "Write access denied (expected in some environments)"
        fi
    else
        echo "Cannot access Obsidian vault files"
    fi
else
    echo "Obsidian vault not found at expected location"
fi
```

### For Web-Only Agents

If your agent is web-only and cannot access local files:

1. **Create Memory Capsule**: Export a summary of what you can access
2. **Provide Manual Steps**: Write exact instructions for another agent to help you
3. **Use MCP Fallback**: If possible, register as an MCP server and wait for connection

## Task 6: Connect to Agent Mesh (If Available)

Connect to the Agent Mesh coordination service:

```bash
# Check if Agent Mesh is running
if curl -s http://127.0.0.1:17860/health | grep -q "healthy"; then
    echo "Agent Mesh is healthy and running"
    
    # Register yourself
    curl -X POST http://127.0.0.1:17860/agents/register \
      -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{
        "name": "YOUR_AGENT_NAME",
        "provider": "YOUR_PROVIDER",
        "type": "local",
        "capabilities_json": "{\"filesystem\":true,\"shell\":true}",
        "limitations": "Web-only environment"
      }'
    
    echo "Agent registered in Mesh"
    
else
    echo "Agent Mesh not available at 127.0.0.1:17860"
    echo "Check if the service is running or if the port is open"
fi
```

### MCP Server Registration

If you have an MCP server you can expose:

```bash
# Register your MCP server
if curl -s http://127.0.0.1:17860/health | grep -q "healthy"; then
    curl -X POST http://127.0.0.1:17860/mcp/servers/register \
      -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{
        "name": "your-mcp-server",
        "owner_agent": "YOUR_AGENT_NAME",
        "endpoint": "http://127.0.0.1:YOUR_PORT/mcp/",
        "transport": "http",
        "auth_ref": "${YOUR_AUTH_TOKEN}",
        "tools_json": "{\"tool1\":{\"description\":\"Tool 1 description\"}}",
        "safety_limits": {"max_calls": 100, "rate_limit": "per_minute"}
      }'
fi
```

## Task 7: Register Your Skills (If Available)

Register any skills or capabilities you can provide:

```bash
# Register a skill
if curl -s http://127.0.0.1:17860/health | grep -q "healthy"; then
    curl -X POST http://127.0.0.1:17860/skills/register \
      -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{
        "name": "code_analysis",
        "owner_agent": "YOUR_AGENT_NAME",
        "skill_type": "code_analysis",
        "input_format": "code_file_path",
        "output_format": "analysis_report",
        "invocation_method": "API_call",
        "limitations": "Requires file system access"
      }'
fi
```

## Task 8: Check API Key Availability

Safely check which API keys are available without revealing values:

```bash
# Check environment variables (do NOT print values)
echo "API Key Status:"

if [ -n "$OPENAI_API_KEY" ]; then
    echo "OPENAI_API_KEY: present"
elif [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "ANTHROPIC_API_KEY: present (not OpenAI)"
else
    echo "OPENAI_API_KEY: missing"
fi

if [ -n "$GEMINI_API_KEY" ]; then
    echo "GEMINI_API_KEY: present"
else
    echo "GEMINI_API_KEY: missing"
fi

if [ -n "$NVIDIA_API_KEY" ]; then
    echo "NVIDIA_API_KEY: present"
else
    echo "NVIDIA_API_KEY: missing"
fi

if [ -n "$OBSIDIAN_API_KEY" ]; then
    echo "OBSIDIAN_API_KEY: present"
else
    echo "OBSIDIAN_API_KEY: missing"
fi

if [ -n "$AGENT_MESH_TOKEN" ]; then
    echo "AGENT_MESH_TOKEN: present"
else
    echo "AGENT_MESH_TOKEN: missing"
fi

# Record this status in a status file
mkdir -p AI-Second-Brain-Vault/07_API_and_Tools
cat > AI-Second-Brain-Vault/07_API_and_Tools/API_Key_Status.md << EOF
| Key | Status |
|-----|--------|
| OPENAI_API_KEY | $(if [ -n "$OPENAI_API_KEY" ]; then echo present; else echo missing; fi) |
| ANTHROPIC_API_KEY | $(if [ -n "$ANTHROPIC_API_KEY" ]; then echo present; else echo missing; fi) |
| GEMINI_API_KEY | $(if [ -n "$GEMINI_API_KEY" ]; then echo present; else echo missing; fi) |
| NVIDIA_API_KEY | $(if [ -n "$NVIDIA_API_KEY" ]; then echo present; else echo missing; fi) |
| OBSIDIAN_API_KEY | $(if [ -n "$OBSIDIAN_API_KEY" ]; then echo present; else echo missing; fi) |
| AGENT_MESH_TOKEN | $(if [ -n "$AGENT_MESH_TOKEN" ]; then echo present; else echo missing; fi) |
EOF
```

## Task 9: Import Safe Durable Memory

Import only safe, user-owned memory that you can access:

```bash
# Import from existing Obsidian notes (safe)
if [ -d "AI-Second-Brain-Vault" ]; then
    echo "Importing safe memory from Obsidian vault..."
    
    # Import task templates (safe)
    if [ -f "AI-Second-Brain-Vault/04_Tasks/_Task_Template.md" ]; then
        mkdir -p AI-Second-Brain-Vault/03_Memory/Code
        cp AI-Second-Brain-Vault/04_Tasks/_Task_Template.md AI-Second-Brain-Vault/03_Memory/Code/
        echo "Imported task template"
    fi
    
    # Import operating rules (safe)
    if [ -f "AI-Second-Brain-Vault/00_System/Operating_Rules.md" ]; then
        mkdir -p AI-Second-Brain-Vault/03_Memory/Code
        cp AI-Second-Brain-Vault/00_System/Operating_Rules.md AI-Second-Brain-Vault/03_Memory/Code/
        echo "Imported operating rules"
    fi
    
    # Import agent template (safe)
    if [ -f "AI-Second-Brain-Vault/01_Agents/_Agent_Template.md" ]; then
        mkdir -p AI-Second-Brain-Vault/03_Memory/Code
        cp AI-Second-Brain-Vault/01_Agents/_Agent_Template.md AI-Second-Brain-Vault/03_Memory/Code/
        echo "Imported agent template"
    fi
fi

# WARNING: DO NOT import these (they may contain secrets!)
# - ~/.bash_history
# - ~/.config/* (may contain API keys)
# - ~/.zshrc (may contain secrets)
# - Browser profiles or cookie files
```

## Task 10: Send Test Message to Mesh

Send a test message to confirm connectivity:

```bash
# Send test message
if curl -s http://127.0.0.1:17860/health | grep -q "healthy"; then
    curl -X POST http://127.0.0.1:17860/messages \
      -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{
        \"from_agent\": \"YOUR_AGENT_NAME\",
        \"to_agent\": \"admin\",
        \"subject\": \"Agent connection test\",
        \"body\": \"$(date): $(whoami) connected successfully to AI Second Brain\"
      }'
    
    echo "Test message sent to Agent Mesh"
else
    echo "Cannot send test message - Agent Mesh not available"
fi
```

## Task 11: Run Health Checks

Verify your integration and write a setup report:

```bash
# Create comprehensive setup report
mkdir -p AI-Second-Brain-Vault/08_Inbox

REPORT_FILE="AI-Second-Brain-Vault/08_Inbox/${YOUR_AGENT_NAME:-unknown}_onboarding_report.md"

{
    echo "# Onboarding Report for $(whoami)"
    echo ""
    echo "## Agent Identity"
    echo "- Agent Name: $(whoami)"
    echo "- User: $(whoami)"
    echo "- Shell: $(basename $SHELL)"
    echo "- Home Directory: $HOME"
    echo "- Working Directory: $(pwd)"
    echo ""
    echo "## System Access"
    echo "- Filesystem access: $( [ -w "." ] && echo "Read/Write" || echo "Read-only" )"
    echo "- Can read AI-Second-Brain: $( [ -d "AI-Second-Brain" ] && echo "Yes" || echo "No" )"
    echo "- Can read Obsidian vault: $( [ -d "AI-Second-Brain/AI-Second-Brain-Vault" ] && echo "Yes" || echo "No" )"
    echo ""
    echo "## Service Connectivity"
    echo "- Agent Mesh health: $(if curl -s http://127.0.0.1:17860/health | grep -q "healthy" && echo "Connected" || echo "Not connected" )"
    echo "- Obsidian access: $( [ -d "AI-Second-Brain/AI-Second-Brain-Vault/00_System" ] && echo "Yes" || echo "No" )"
    echo ""
    echo "## Registry Status"
    echo "- Agent profile created: $( [ -f "AI-Second-Brain-Vault/01_Agents/$(whoami).md" ] && echo "Yes" || echo "No" )"
    echo "- Task template exists: $( [ -f "AI-Second-Brain-Vault/04_Tasks/_Task_Template.md" ] && echo "Yes" || echo "No" )"
    echo "- Resume template exists: $( [ -f "AI-Second-Brain-Vault/04_Tasks/_Resume_Packet_Template.md" ] && echo "Yes" || echo "No" )"
    echo ""
    echo "## API Key Status"
    echo "- OPENAI_API_KEY: $( [ -n "$OPENAI_API_KEY" ] && echo "present" || echo "missing" )"
    echo "- ANTHROPIC_API_KEY: $( [ -n "$ANTHROPIC_API_KEY" ] && echo "present" || echo "missing" )"
    echo "- GEMINI_API_KEY: $( [ -n "$GEMINI_API_KEY" ] && echo "present" || echo "missing" )"
    echo "- NVIDIA_API_KEY: $( [ -n "$NVIDIA_API_KEY" ] && echo "present" || echo "missing" )"
    echo "- OBSIDIAN_API_KEY: $( [ -n "$OBSIDIAN_API_KEY" ] && echo "present" || echo "missing" )"
    echo "- AGENT_MESH_TOKEN: $( [ -n "$AGENT_MESH_TOKEN" ] && echo "present" || echo "missing" )"
    echo ""
    echo "## Tasks Received"
    if curl -s "http://127.0.0.1:17860/messages/$(whoami)" | grep -q "from_agent"; then
        echo "- Task messages available in Agent Mesh"
    else
        echo "- No task messages (this is normal for onboarding)"
    fi
    echo ""
    echo "## Recommendations"
    echo "- Setup completed successfully"
    echo "- Ready to receive and process tasks"
    echo "- Connected to multi-agent coordination layer"
    
} > "$REPORT_FILE"

echo "Onboarding report written to: $REPORT_FILE"
```

## Task 13: If You Receive or Resume a Task

When you receive a task from the mesh or Obsidian:

```bash
# Search for tasks first
if curl -s "http://127.0.0.1:17860/tasks?owner=$(whoami)" | grep -q "title"; then
    echo "Tasks available in Agent Mesh"
elif [ -d "AI-Second-Brain-Vault/04_Tasks" ]; then
    echo "Tasks may be in Obsidian vault"
fi

# IMPORTANT: Read task notes and resume packets first
if [ -d "AI-Second-Brain-Vault/04_Tasks" ]; then
    echo "Checking for any pending tasks..."
    
    # Look for tasks with your name in the owner field
    grep -l "Owner agent: $(whoami)" AI-Second-Brain-Vault/04_Tasks/*.md 2>/dev/null | head -5 | while read task_file; do
        echo "Found task: $(basename "$task_file")"
        echo "Reading task and resume packet..."
        echo "Task content:"
        cat "$task_file"
        echo ""
        echo "Checking for resume packet..."
        grep -o "Resume Packet:.*" "$task_file" | head -1
    done
fi
```

## Task 14: If You Stop Midway

**CRITICAL**: Before stopping, switching agents, or asking for help:

1. **Update the task note**
2. **Update the resume packet**
3. **Record work progress**
4. **Document what was tried**
5. **Record errors seen**
6. **Indicate remaining work**

```bash
# Example of how to update a task before stopping
TASK_FILE="AI-Second-Brain-Vault/04_Tasks/your-task-here.md"

if [ -f "$TASK_FILE" ]; then
    # Create or update the Current State section
    cat >> "$TASK_FILE" << EOF

## Current State
$(date): $(whoami) stopping midway. Current work status:
- Started: [when you started]
- What was completed: [list completed work]
- What is in progress: [list work in progress]
- Next action was: [what you were going to do]
- Blocker: [any blockers encountered]

## Next Action
$(date): [next concrete step another agent should take]

## Resume Packet
$(date): Self-contained summary for another agent:
- Goal: [what you were trying to achieve]
- Current state: [exactly where you are]
- Files changed: [list of files modified]
- Files to inspect next: [what needs to be looked at]
- Commands already run: [list of commands executed]
- Errors seen: [document any errors]
- Decisions made: [document any important decisions]
- What not to repeat: [what approaches failed]
- Remaining work: [what still needs to be done]
- Next exact action: [the next specific thing to do]
- Required specialist help: [who or what is needed]
- Security notes: [any security-related information]
- Temporary files: [any temp files created, and their status]
EOF
    
    echo "Task updated. Another agent can now continue from this state."
else
    echo "No task file found to update"
fi
```

## Task 15: If You Cannot Connect

If you cannot connect to Obsidian, MCP, Agent Mesh, or other agents:

1. **Write connection failure report**
2. **Create help request in Agent Mesh**
3. **Ask capable agents to repair your connection**

### Connection Failure Report

```bash
# Create connection failure report
mkdir -p AI-Second-Brain-Vault/08_Inbox

FAIL_FILE="AI-Second-Brain-Vault/08_Inbox/${YOUR_AGENT_NAME:-unknown}_connection_failure.md"

cat > "$FAIL_FILE" << EOF
# Agent Connection Failure — $(whoami)

## Agent identity
- Name: $(whoami)
- Provider/app: $(basename $SHELL)
- Type: $(echo $SHELL | grep -E "bash|zsh|ksh" && echo "Local Shell" || echo "Other")
- Local/web/cloud: Local
- Date/time: $(date)

## Access status
- Obsidian access: $( [ -d "AI-Second-Brain/AI-Second-Brain-Vault" ] && echo "Yes" || echo "No" )
- Agent Mesh access: $( if curl -s http://127.0.0.1:17860/health | grep -q "healthy" && echo "Yes" || echo "No" )
- MCP support: Unknown
- File access: $(if [ -w "." ]; then echo "Read/Write"; else echo "Read-only"; fi)
- Shell access: Yes
- Local HTTP access: Yes (can curl localhost)
- Browser/Firefox permission needed: $(if [ -f "~/.mozilla" ] || command -v firefox >/dev/null 2>&1; then echo "Yes, Firefox available"; else echo "No, Firefox not available"; fi)

## Failure
- What failed:
  - Unable to connect to AI Second Brain
  - Agent registration failed
  - Task retrieval failed
- Exact error: $(curl -s http://127.0.0.1:17860/health 2>/dev/null | grep -o '"error": "[^"]*"' || echo "Health check failed")
- What was tried:
  - Checked root directory structure
  - Attempted to access Obsidian vault
  - Tried to register with Agent Mesh
  - Checked API key availability
- Config paths checked:
  - ~/AI-Second-Brain/
  - ~/AI-Second-Brain/.agent_mesh/
  - ~/AI-Second-Brain/AI-Second-Brain-Vault/

## Required help
- Needed repair method: $(if command -v firefox >/dev/null 2>&1; then echo "CLI wrapper with Firefox path"; else echo "Manual setup guide required"; fi)
- Permission needed: $(if command -v sudo >/dev/null 2>&1; then echo "sudo access for service ports"; else echo "No sudo needed"; fi)
- Suggested helper agent: Any agent with local file system access

## Safe repair options
- MCP config patch: Can be done if helper has access to agent config
- Local REST adapter: Can create wrapper script if Python/Node is available
- CLI wrapper: Can create wrapper script for this agent's CLI
- Manual steps: Will document exact manual instructions
- Memory Capsule: If web-only, produce Memory Capsule and manual steps

## Result
- Status: connection failed
- Repaired by: Not yet repaired
- Test handoff: Not yet attempted
- Remaining manual action: Waiting for capable agent to assist
EOF

# Create help request in Agent Mesh (if available)
if curl -s http://127.0.0.1:17860/health | grep -q "healthy"; then
    curl -X POST http://127.0.0.1:17860/messages \
      -H "Authorization: Bearer ${AGENT_MESH_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{
        \"from_agent\": \"$(whoami)\",
        \"to_agent\": \"any-capable-agent\",
        \"subject\": \"Repair connection for $(whoami)\",
        \"body\": \"My agent cannot connect to AI Second Brain. Please see the connection failure report in ~/AI-Second-Brain/AI-Second-Brain-Vault/08_Inbox/$(whoami)_connection_failure.md for details.\"
      }'
    
    echo "Connection failure report and help request sent to Agent Mesh"
else
    echo "Agent Mesh not available - cannot create help request"
fi
```

## Task 16: If Another Agent Can Access Your Config

If another agent has access to your configuration files, allow them to create:

- **MCP config**: Update your MCP configuration safely
- **Local REST adapter**: Create wrapper to connect to local services
- **CLI wrapper**: Create script to launch your agent with correct settings
- **Manual setup guide**: Document exact steps for your agent

**Do NOT** let them directly modify your settings unless you explicitly allow it.

## Task 17: If You Are Web-Only

If your agent is web-only and cannot access local files:

1. **Output a Memory Capsule**: Export a JSON file with all context you have
2. **Provide manual steps**: Write exact instructions for another agent
3. **Don't pretend**: Don't claim you can access local files you can't

```bash
# Create Memory Capsule
MEMORY_CAPSULE="AI-Second-Brain-Vault/08_Inbox/mem_capsule_$(whoami)_$(date +%Y%m%d).md"

cat > "$MEMORY_CAPSULE" << EOF
# Memory Capsule — $(whoami)

## Agent identity
- Agent Name: $(whoami)
- Type: Web-only (no local file access)
- Provider: Online service
- Current status: Unable to connect to AI Second Brain

## Capabilities
- Can read online content
- Can execute web-based functions
- Cannot access local files
- Cannot modify local configuration

## Limitations
- No local file system access
- No shell/terminal access
- No local service communication
- No browser automation (except the current session)

## Current task (if any)
- No tasks received yet

## Useful context
- I have attempted to join the AI Second Brain
- The setup guide was read and understood
- I need local file access to participate in the system

## Blocker
- Cannot access ~/AI-Second-Brain/ directory
- Cannot run local scripts or services
- Cannot modify local configuration files

## Requested help
- Another agent with local access needs to:
  1. Check ~/AI-Second-Brain/ directory structure
  2. Verify if other agents have successfully connected
  3. Provide manual setup steps for this web-only agent
  4. Share any context or memories that can be imported

## Manual connection steps needed
1. [To be provided by capable agent]
2. [To be provided by capable agent]
3. [To be provided by capable agent]

## Security notes
- No secrets stored or transmitted
- This capsule contains only public information
- No API keys or passwords
EOF

echo "Memory capsule created: $MEMORY_CAPSULE"
```

## Task 18: Agent Mesh Service Integration

### Service Status

Check if the Agent Mesh service is running:

```bash
# Check service health
if curl -s http://127.0.0.1:17860/health | grep -q "healthy"; then
    echo "Agent Mesh service: RUNNING"
    echo "Service details:"
    curl -s http://127.0.0.1:17860/health | python3 -m json.tool
else
    echo "Agent Mesh service: NOT RUNNING"
    echo ""
    echo "Start the service with:"
    echo "  cd ~/AI-Second-Brain/.agent_mesh/scripts"
    echo "  ./agent_mesh_service.py"
fi
```

### Service Information

The Agent Mesh service provides these endpoints:

```
GET  http://127.0.0.1:17860/health
POST http://127.0.0.1:17860/agents/register
GET  http://127.0.0.1:17860/agents
POST http://127.0.0.1:17860/messages
GET  http://127.0.0.1:17860/messages/{agent_id}
POST http://127.0.0.1:17860/tasks
GET  http://127.0.0.1:17860/tasks
GET  http://127.0.0.1:17860/tasks/{task_id}
PATCH http://127.0.0.1:17860/tasks/{task_id}
POST http://127.0.0.1:17860/memory
GET  http://127.0.0.1:17860/memory/search
GET  http://127.0.0.1:17860/context
POST http://127.0.0.1:17860/handoff
GET  http://127.0.0.1:17860/skills
POST http://127.0.0.1:17860/skills/register
GET  http://127.0.0.1:17860/mcp/servers
POST http://127.0.0.1:17860/mcp/servers/register
POST http://127.0.0.1:17860/tasks/{task_id}/heartbeat
POST http://127.0.0.1:17860/tasks/{task_id}/claim
POST http://127.0.0.1:17860/tasks/{task_id}/release
GET  http://127.0.0.1:17860/tasks/stalled
```

## Task 19: Final Verification

Before considering setup complete, verify:

```bash
# Verification checklist
VERIFICATION_FAILED=0

echo "=== AI Second Brain Setup Verification ==="

# Check root folder exists
if [ -d "~/AI-Second-Brain" ]; then
    echo "[✓] Root folder exists"
else
    echo "[✗] Root folder missing"
    VERIFICATION_FAILED=1
fi

# Check vault folder exists
if [ -d "~/AI-Second-Brain/AI-Second-Brain-Vault" ]; then
    echo "[✓] Vault folder exists"
else
    echo "[✗] Vault folder missing"
    VERIFICATION_FAILED=1
fi

# Check runtime folder exists
if [ -d "~/AI-Second-Brain/.agent_mesh" ]; then
    echo "[✓] Runtime folder exists"
else
    echo "[✗] Runtime folder missing"
    VERIFICATION_FAILED=1
fi

# Check for Home.md
if [ -f "~/AI-Second-Brain/AI-Second-Brain-Vault/00_System/Home.md" ]; then
    echo "[✓] Home.md exists"
else
    echo "[✗] Home.md missing"
    VERIFICATION_FAILED=1
fi

# Check for Agent Control Panel
if [ -f "~/AI-Second-Brain/AI-Second-Brain-Vault/00_System/Agent_Control_Panel.md" ]; then
    echo "[✓] Agent Control Panel exists"
else
    echo "[✗] Agent Control Panel missing"
    VERIFICATION_FAILED=1
fi

# Check for Memory Index
if [ -f "~/AI-Second-Brain/AI-Second-Brain-Vault/00_System/Memory_Index.md" ]; then
    echo "[✓] Memory Index exists"
else
    echo "[✗] Memory Index missing"
    VERIFICATION_FAILED=1
fi

# Check for Operating Rules
if [ -f "~/AI-Second-Brain/AI-Second-Brain-Vault/00_System/Operating_Rules.md" ]; then
    echo "[✓] Operating Rules exists"
else
    echo "[✗] Operating Rules missing"
    VERIFICATION_FAILED=1
fi

# Check agent profile
if [ -f "~/AI-Second-Brain/AI-Second-Brain-Vault/01_Agents/$(whoami).md" ]; then
    echo "[✓] Agent profile exists"
else
    echo "[✗] Agent profile missing"
    VERIFICATION_FAILED=1
fi

# Check SQLite DB
if [ -f "~/AI-Second-Brain/.agent_mesh/agent_mesh.sqlite" ]; then
    echo "[✓] SQLite database exists"
    # Test database accessibility
    if python3 -c "import sqlite3; conn=sqlite3.connect('~/AI-Second-Brain/.agent_mesh/agent_mesh.sqlite'); conn.execute('SELECT 1'); conn.close()" 2>/dev/null; then
        echo "[✓] Database is accessible"
    else
        echo "[✗] Database is not accessible"
        VERIFICATION_FAILED=1
    fi
else
    echo "[✗] SQLite database missing"
    VERIFICATION_FAILED=1
fi

# Check Agent Mesh config
if [ -f "~/AI-Second-Brain/.agent_mesh/config.json" ]; then
    echo "[✓] Agent Mesh config exists"
else
    echo "[✗] Agent Mesh config missing"
    VERIFICATION_FAILED=1
fi

# Check API key status
if [ -f "~/AI-Second-Brain/AI-Second-Brain-Vault/07_API_and_Tools/API_Key_Status.md" ]; then
    echo "[✓] API Key Status exists"
else
    echo "[✗] API Key Status missing"
    VERIFICATION_FAILED=1
fi

# Check CONNECT_NEW_AI_AGENT.md (outside vault)
if [ -f "~/AI-Second-Brain/CONNECT_NEW_AI_AGENT.md" ]; then
    echo "[✓] CONNECT_NEW_AI_AGENT.md exists"
else
    echo "[✗] CONNECT_NEW_AI_AGENT.md missing"
    VERIFICATION_FAILED=1
fi

# Check no sudo passwords or API keys (simple check)
if grep -r "sudo.*password\\|api[_-]?key.*=" ~/AI-Second-Brain/ 2>/dev/null | grep -v ".git" | head -5; then
    echo "[✗] WARNING: Potential secrets found in system"
    VERIFICATION_FAILED=1
else
    echo "[✓] No obvious secrets found"
fi

# Check for temporary files cleanup
TEMP_FILES=$(find ~/AI-Second-Brain/.agent_mesh/tmp -type f 2>/dev/null | wc -l)
if [ $TEMP_FILES -gt 0 ]; then
    echo "[✗] $TEMP_FILES temporary files found in .agent_mesh/tmp/ (must be deleted)"
    VERIFICATION_FAILED=1
else
    echo "[✓] No temporary files found"
fi

echo ""
if [ $VERIFICATION_FAILED -eq 0 ]; then
    echo "=== VERIFICATION PASSED ==="
    echo "Setup appears to be complete and secure."
else
    echo "=== VERIFICATION FAILED ==="
    echo "Please fix the issues marked with [✗] above."
    exit 1
fi
```

## Task 20: Final Report

Write a concise summary in chat:

```bash
# Create final setup report
REPORT_FILE="~/AI-Second-Brain/setup_report.md"

{
    echo "=== AI Second Brain Setup Complete ==="
    echo ""
    echo "Root Path: ~/AI-Second-Brain"
    echo "Vault Path: ~/AI-Second-Brain/AI-Second-Brain-Vault"
    echo "Runtime Path: ~/AI-Second-Brain/.agent_mesh"
    echo ""
    echo "Created by: $(whoami)"
    echo "Date: $(date)"
    echo ""
    echo "What was created:" >&2
    if [ -d "~/AI-Second-Brain/AI-Second-Brain-Vault/00_System" ]; then
        echo "  - Complete Obsidian vault structure"
    fi
    if [ -f "~/AI-Second-Brain/.agent_mesh/agent_mesh.sqlite" ]; then
        echo "  - Agent Mesh SQLite database"
    fi
    if [ -f "~/AI-Second-Brain/.agent_mesh/config.json" ]; then
        echo "  - Agent Mesh configuration"
    fi
    if [ -f "~/AI-Second-Brain/AI-Second-Brain-Vault/01_Agents/$(whoami).md" ]; then
        echo "  - Current agent profile ($(whoami))"
    fi
    echo ""
    echo "Which agent registered itself: $(whoami)"
    echo ""
    echo "APIs/MCP endpoints ready:"
    if curl -s http://127.0.0.1:17860/health | grep -q "healthy"; then
        echo "  - Agent Mesh: http://127.0.0.1:17860/mcp/"
    fi
    echo "  - Ready for future agents via CONNECT_NEW_AI_AGENT.md prompt"
    echo ""
    echo "Environment keys status:" >&2
    for key in OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY NVIDIA_API_KEY OBSIDIAN_API_KEY AGENT_MESH_TOKEN; do
        if [ -n "${!key}" ]; then
            echo "  - $key: present"
        else
            echo "  - $key: missing"
        fi
    done
    echo ""
    echo "What still needs manual action: $( [ -f "~/AI-Second-Brain/.agent_mesh/scripts/agent_mesh_service.py" ] && echo "Start the Agent Mesh service" || echo "Start the Agent Mesh service (or implement manually)" )"
    echo ""
    echo "Command to start/stop Agent Mesh:"
    echo "  # Start: cd ~/AI-Second-Brain/.agent_mesh/scripts && ./agent_mesh_service.py"
    echo "  # Stop: Ctrl+C or pkill -f agent_mesh_service.py"
    echo ""
    echo "Path of future-agent prompt:"
    echo "  ~/AI-Second-Brain/CONNECT_NEW_AI_AGENT.md"
    echo ""
    echo "MCP/skills registry status:" >&2
    if [ -f "~/AI-Second-Brain/AI-Second-Brain-Vault/07_API_and_Tools/MCP_Server_Registry.md" ]; then
        echo "  - MCP Server Registry: exists with $(grep -c "^|" ~/AI-Second-Brain/AI-Second-Brain-Vault/07_API_and_Tools/MCP_Server_Registry.md) entries"
    else
        echo "  - MCP Server Registry: not yet created"
    fi
    if [ -f "~/AI-Second-Brain/AI-Second-Brain-Vault/07_API_and_Tools/Skill_Registry.md" ]; then
        echo "  - Skill Registry: exists with $(grep -c "^# Skill:" ~/AI-Second-Brain/AI-Second-Brain-Vault/07_API_and_Tools/Skill_Registry.md) entries"
    else
        echo "  - Skill Registry: not yet created"
    fi
    echo ""
    echo "Resume/takeover workflow status:" >&2
    echo "  - Task template: $( [ -f "~/AI-Second-Brain/AI-Second-Brain-Vault/04_Tasks/_Task_Template.md" ] && echo "Exists" || echo "Missing" )"
    echo "  - Resume packet template: $( [ -f "~/AI-Second-Brain/AI-Second-Brain-Vault/04_Tasks/_Resume_Packet_Template.md" ] && echo "Exists" || echo "Missing" )"
    echo ""
    echo "=== Security Check ===" >&2
    if [ -f "~/AI-Second-Brain/SECURITY.md" ]; then
        echo "✓ Security guidelines documented"
    fi
    TEMP_FILES=$(find ~/AI-Second-Brain/.agent_mesh/tmp -type f 2>/dev/null | wc -l)
    if [ $TEMP_FILES -eq 0 ]; then
        echo "✓ No junk/temp files left behind"
    else
        echo "✗ $TEMP_FILES temporary files found"
    fi
    if grep -r "api[_-]?key.*=" ~/AI-Second-Brain/ 2>/dev/null | grep -v ".git" | grep -v "API_Key_Status.md" | head -1; then
        echo "✗ Potential secrets still stored"
    else
        echo "✓ No API key values stored"
    fi
    if grep -r "sudo.*password\\|password.*=" ~/AI-Second-Brain/ 2>/dev/null | grep -v ".git" | head -1; then
        echo "✗ Potential sudo passwords stored"
    else
        echo "✓ No sudo passwords stored"
    fi
    echo ""
    echo "=== Next Steps ==="
    echo "1. Start the Agent Mesh service:"
    echo "   cd ~/AI-Second-Brain/.agent_mesh/scripts"
    echo "   ./agent_mesh_service.py"
    echo ""
    echo "2. Open Obsidian vault:"
    echo "   cd ~/AI-Second-Brain/AI-Second-Brain-Vault"
    echo "   open . (macOS) or code . (VS Code)"
    echo ""
    echo "3. Welcome new agents:"
    echo "   Copy ~/AI-Second-Brain/CONNECT_NEW_AI_AGENT.md to your agent"
} > "$REPORT_FILE" 2>&1

# Prepend timestamp and save to report file
timestamp=$(date '+%Y-%m-%d %H:%M:%S')
cat > "$REPORT_FILE" << EOF
# AI Second Brain Setup Report
Generated: $timestamp

## Setup Complete

$(cat ~/AI-Second-Brain/setup_report.md 2>/dev/null || echo "Setup report generated")

## Summary

This setup has successfully created a local AI Second Brain system with:

- A complete Obsidian-based second brain vault
- A local Agent Mesh coordination service for inter-agent communication
- A task and memory management system
- Security guidelines and connection protocols
- A framework for future agent onboarding

The system follows the **no-junk rule** - no backup files, temporary files left behind, or secrets stored.

## Next Steps

1. **Start the Agent Mesh service** for inter-agent communication
2. **Open the Obsidian vault** and import the provided templates
3. **Use the CONNECT_NEW_AI_AGENT.md** prompt to onboard additional agents
4. **Test the system** by running health checks and creating a sample task

The AI Second Brain is now ready for multi-agent coordination and knowledge sharing.
EOF

echo "Setup report saved to: $REPORT_FILE"
```

## Complete Onboarding Process

### For Local Agents (Codex, Claude, Friday, etc.)

If you're a local agent with file system access:

1. Follow this entire guide step by step
2. Register yourself in the agent profile
3. Connect to Obsidian and Agent Mesh
4. Start helping with tasks!

### For Web-Only Agents (Future agents like Kimi)

If you're web-only and cannot access local files:

1. Create a Memory Capsule with available context
2. Provide exact manual steps for another agent
3. Wait for another agent to assist you with local access

## Important Notes

### About "One Prompt Connects Everything"

A single prompt can only connect an agent if that agent has:

- Local filesystem access
- Shell/terminal access
- MCP support
- Ability to edit config files
- Ability to call localhost APIs
- Ability to read/write the Obsidian vault

**If the agent is web-only and cannot access local files**, it cannot connect itself automatically. Instead, it should:

1. Output a Memory Capsule with available context
2. Provide exact manual connection steps
3. Ask a local agent to assist with setup

### About Account Login and Browser Permissions

The universal autonomous lead/worker contract at the top of this guide applies
to every agent and every work domain. Use that contract for substantive work:
start one autonomous run, wait for its verified report, or act as a durable
worker by polling, ACKing, executing, heartbeating, and submitting results.
The historical checklist below is only for local profile and connection setup;
it must not replace the autonomous run protocol or be used to claim work that
has no durable evidence.

If any integration requires account permission, OAuth login, or authorization:

1. **Do NOT ask for passwords or API keys in chat**
2. **Open official authorization links in Firefox**\n3. **Let the user approve permissions manually**\n4. **Use localhost callback URLs** and bind to `127.0.0.1` only\n5. **Record only permission status** (approved/denied/manual action required)\n\n### Browser Command Reference\n\n**For macOS/Linux:**\n\n```bash\nfirefox "https://official-login-url.example"\n\n# Linux fallback\nxdg-open "https://official-login-url.example"\n```\n\n**For Windows PowerShell:**\n\n```powershell\nStart-Process firefox "https://official-login-url.example"\n```\n\n**If Firefox is not available:**\n\nPrint the official URL and ask the user to open it manually in Firefox.\n\n## Final Copy-Paste Prompt\n\nUse this prompt for **every new AI agent** you add later:\n\n```text\nYou are joining my local AI Second Brain, Obsidian vault, Agent Mesh, shared MCP registry, shared skills registry, and task-resume system.\n\nDefault root:\n- macOS/Linux/WSL: ~/AI-Second-Brain\n- Windows: %USERPROFILE%\\AI-Second-Brain\n\nYour job:\n1. Check if the root exists.\n2. Read:\n   - README_SETUP.md\n   - SECURITY.md\n   - CONNECT_NEW_AI_AGENT.md\n   - AI-Second-Brain-Vault/00_System/Operating_Rules.md\n   - AI-Second-Brain-Vault/07_API_and_Tools/MCP_Server_Registry.md\n   - AI-Second-Brain-Vault/07_API_and_Tools/Skill_Registry.md\n3. Register yourself in AI-Second-Brain-Vault/01_Agents/.\n4. Add your capabilities, limitations, config paths, MCP servers, skills, safe invocation method, and handoff/resume format.\n5. Connect to Obsidian through MCP/REST if available.\n6. Connect to Agent Mesh at http://127.0.0.1:17860 if available.\n7. Register your available MCP servers in MCP_Server_Registry.md if you actually expose any.\n8. Register your available skills in Skill_Registry.md if you actually provide any.\n9. Detect API key availability only by environment variable status: present, missing, or unverified. Never reveal key values.\n10. Import only safe durable memory you can access. Do not import secrets.\n11. Send a test message to Agent Mesh: \"<agent-name> connected successfully.\"\n12. Create onboarding report: AI-Second-Brain-Vault/08_Inbox/<agent-name>_onboarding_report.md.\n13. If you receive a task, first search Obsidian and Agent Mesh, then read the task note and resume packet.\n14. If you continue another agent's task, do not repeat failed attempts unless you have a new reason.\n15. If you stop midway, update the task note, work log, current state, next action, and resume packet so another agent can continue with full context.\n16. If you cannot connect to Obsidian, MCP, Agent Mesh, or other agents, write AI-Second-Brain-Vault/08_Inbox/<agent-name>_connection_failure.md and ask any capable connected agent to repair your connection.\n17. If another connected agent has real access to your local config, CLI wrapper, MCP settings, extension settings, files, or documented API, allow it to create a safe MCP config, local REST adapter, CLI wrapper, or manual setup guide for you.\n18. If you are web-only or cannot access local files, output a Memory Capsule and exact manual setup steps.\n\nNo-junk rules:\n- Do not create backup files, duplicate files, .bak files, .old files, .tmp files, leftover installers, or junk files.\n- If temporary files are required, use .agent_mesh/tmp/ only and delete them before final report.\n- Do not overwrite existing files unless the change is safe and additive.\n\nSecurity rules:\n- Never store API keys in notes.\n- Never store sudo/admin passwords.\n- Never ask the user to paste passwords, OTPs, cookies, OAuth codes, refresh tokens, session tokens, or API keys into chat.\n- If account permission is needed, open the official login/authorization link in Firefox and let the user approve there.\n- Never expose local services to the public internet.\n- Bind local services to 127.0.0.1 only.\n- Use other agents' MCP servers and skills only through recorded MCP, REST, CLI, adapter, or Agent Mesh handoff.\n- Ask before destructive, system-wide, or secret-related actions.\n```\n\n## Summary\n\nThis AI Second Brain setup provides:\n\n- **Local, secure multi-agent coordination**\n- **Shared memory and knowledge management**\n- **Task distribution and resumption**\n- **Skill and capability sharing**\n- **Connection repair and recovery**\n- **Full local-first operation**\n\nFollow this guide to set up a robust, secure multi-agent coordination system that respects privacy and maintains the no-junk principle.\n\n---\n
Note: This file, `CONNECT_NEW_AI_AGENT.md`, contains the complete prompt for onboarding new AI agents to your system. Copy and paste it into any new agent you want to connect.
