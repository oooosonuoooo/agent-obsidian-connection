# Durable Agent Mesh Orchestration

This repository contains the shared control plane used by the local agents. The
MCP clients do not communicate with one another directly:

```text
User request
    |
    v
Lead agent
    |
    v
Agent Mesh orchestrator and SQLite queue
    |
    +--> TASK_REQUEST --> selected worker
    |                         |
    |                         +--> TASK_ACK
    |                         +--> TASK_PROGRESS
    |                         +--> TASK_RESULT
    |                         +--> TASK_ERROR
    |
    v
Lead verification and final integration
    |
    v
One final result
```

## Why delegated work previously disappeared

The original Agent Mesh `POST /messages` route only inserted a row with
`status=queued`. There was no durable task consumer, request envelope, worker
poll/lease operation, acknowledgement deadline, result route, correlation
identifier, or result verification gate. The original MCP bridge exposed health,
listing, direct messages, and handoffs, but did not expose an executable task
lifecycle. A message could therefore be stored successfully while no receiving
agent was ever invoked and the sender had no reliable way to distinguish
delivery from completion.

The repair keeps the legacy routes and adds a deterministic control plane. It
does not fabricate provider responses. A worker result is recorded only when a
real agent calls the worker API/MCP operation.

## Shared installation

All configured local MCP clients use the same bridge path:

```text
~/AI-Second-Brain/.agent_mesh/scripts/agent_mesh_mcp_stdio.py
```

Install or refresh the shared runtime once:

```bash
bash scripts/deploy_shared_runtime.sh
```

Deployment also merges the shared bridge into the local Gemini/Antigravity,
Codex, OpenCode, Kilo, Cursor, Windsurf/Cascade, and Kiro MCP configuration
locations. It is safe to run repeatedly; unrelated provider settings are
preserved and any changed client file receives a backup under
`~/.agent_mesh/backups/`.
Deployment also synchronizes the canonical vault MCP and skill registry
metadata into Agent Mesh; credential values are never imported.

Then restart the one shared Agent Mesh service:

```bash
bash ~/AI-Second-Brain/.agent_mesh/scripts/start_agent_mesh.sh
```

Gemini/Antigravity, Codex, OpenCode, Kilo, Cursor, Windsurf/Cascade, and Kiro
inherit the same updated bridge when their MCP connection is reloaded. No
separate task protocol or provider configuration is required in each client.
Agents already registered in the
shared SQLite registry retain their metadata; a live agent must publish a
heartbeat before it is eligible for cooperative queue work. Installed or
registered supervisor-owned adapters are exposed as `autonomy_ready` and can
be invoked directly even when their GUI client is not currently open.

## Autonomous one-objective workflow

Every MCP-connected agent is a valid lead. For a substantive user request,
the receiving agent should call `agent_mesh_start_autonomous_run` with the
complete objective and workspace, then call `agent_mesh_wait_autonomous_run`.
The supervisor performs this sequence durably:

1. **Analyze and plan** — inspect the objective and current agent capability
   inventory, then use a real planner adapter to produce a task DAG when the
   caller did not provide one.
2. **Consult specialists** — for complex objectives, ask available specialists
   for risks, interfaces, and acceptance checks before the planner commits the
   DAG. Consultation is recorded separately from execution.
3. **Delegate and execute** — route each task by declared capabilities,
   candidate agents, health, artifact ownership, and concurrency limits. A
   task is real only after its provider is invoked or a cooperative worker
   polls it.
4. **Audit and revise** — an independent real auditor inspects the workspace
   and submitted result. Rejected work receives the audit reason and is
   retried/reassigned within the bounded round and retry limits.
5. **Integrate and report** — once every task is accepted, a real integrator
   produces the final report when available. Otherwise the supervisor returns
   an evidence aggregation that clearly says integration was reduced; it never
   invents a provider result.

The autonomous request state is one of `QUEUED`, `ANALYZING`, `CONSULTING`,
`PLANNING`, `DELEGATING`, `RUNNING`, `COLLECTING`, `AUDITING`, `REVISING`,
`INTEGRATING`, `FINALIZING`, `WAITING`, `COMPLETED`, `FAILED`, `PARTIALLY_FAILED`,
`BLOCKED`, or `CANCELLED`. `WAITING` means the system needs a real worker,
planner, auditor, integrator, or lead action; it is not a success state. The
lead may provide an explicit plan or restore a newly available adapter with
`agent_mesh_resume_autonomous_run`.

### Federated tools and skills

Every MCP-connected client uses the same shared capability catalog. Agents can
inspect it with `agent_mesh_list_shared_capabilities`,
`agent_mesh_list_shared_tools`, and `agent_mesh_list_shared_skills`. A planner
or lead requests a published capability by putting its name in a task's
`required_tools` or `required_skills`; capability matching then selects the
healthy publishing agent and preserves that agent's local permissions and
credentials. The selected owner performs the tool/skill operation and returns
normal ACK, result, audit, and integration evidence. Sharing does not copy
secret-bearing MCP configuration into another client and does not turn an
offline GUI session into a callable provider.

### Real provider adapters

The shared supervisor discovers installed local CLIs for Gemini, Codex, Kilo,
OpenCode, Claude/FCC, and Friday. It also resolves one-time registrations for HTTP JSON,
MCP stdio, or custom command providers. The inventory endpoint is safe to use
for diagnostics because it omits command lines and credential values:

```text
agent_mesh_list_adapters
```

For a provider not covered by a built-in profile, register its invocation once
for the shared team. Use an executable plus argument tokens; do not put shell
pipelines in the command. Reference credentials by environment-variable name:

```json
{
  "name": "MyProvider",
  "provider": "Internal",
  "capabilities": ["research", "coding"],
  "autonomy_adapter": {
    "kind": "http",
    "endpoint": "http://127.0.0.1:9000/agent",
    "auth_env": "MY_PROVIDER_TOKEN",
    "timeout": 900
  }
}
```

For a GUI-only agent, omit the adapter. The agent remains a cooperative
worker: it polls `agent_mesh_poll_tasks`, acknowledges the request, performs
the work in its own session, sends progress/heartbeats, and submits the real
structured result. This is the correct fallback for Codex/IDE clients without
a safe generic one-shot CLI and preserves truthful completion semantics.

## Task protocol

1. The lead discovers agents with `GET /agents` or `agent_mesh_list_agents`.
2. The lead creates a run with an explicit plan and task DAG using
   `POST /orchestration/runs`.
3. The orchestrator selects a healthy, capable agent, locks its declared
   artifacts, and persists a `TASK_REQUEST` with task, run, conversation, and
   correlation identifiers.
4. The worker polls `POST /tasks/poll`, which atomically leases the request.
5. The worker acknowledges with `POST /tasks/{task_id}/ack`.
6. The worker reports progress and heartbeats while running.
7. The worker submits a structured result with `POST /tasks/{task_id}/result`.
8. The lead verifies it with `POST /tasks/{task_id}/verify`.
9. Once every task is verified, the lead stores the integrated final response
   with `POST /orchestration/runs/{run_id}/finalize`.

The task is not complete merely because it was sent or acknowledged.

### Structured worker result

At minimum, a result must contain `summary`. The contract also accepts these
optional arrays:

```json
{
  "summary": "Implemented and tested the assigned change.",
  "files_changed": [],
  "files_created": [],
  "commands_executed": [],
  "tests": [],
  "warnings": [],
  "errors": [],
  "handoff_notes": []
}
```

The worker payload includes the project goal, assigned description, task type,
dependencies and their verified results, interfaces, relevant files,
constraints, acceptance criteria, and the expected result contract. It does not
blindly copy the entire conversation or repository.

## State and reliability

Task states include `pending`, `waiting_dependency`, `waiting_agent`,
`retrying`, `sent`, `acknowledged`, `running`, `verifying`, `completed`,
`failed`, `blocked`, and `cancelled`. Run states include the planning,
delegating, executing, verifying, waiting, blocked, failed, cancelled, and
completed phases.

The SQLite queue is WAL-backed and survives process restart. Every transition
is traced in `orchestration_events`; messages retain delivery timestamps,
attempts, leases, errors, and correlation identifiers.

The following settings are configurable through `.env.local`:

| Setting | Default | Purpose |
|---|---:|---|
| `AGENT_ACK_TIMEOUT` | `30` | Seconds before an unacknowledged request is recovered |
| `AGENT_EXECUTION_TIMEOUT` | `1800` | Maximum worker execution lease |
| `AGENT_MAX_RETRIES` | `2` | Maximum retries after the first attempt |
| `AGENT_RETRY_BACKOFF` | `2` | Base retry delay in seconds |
| `AGENT_HEARTBEAT_TIMEOUT` | `120` | Age after which an agent is treated as offline |
| `MAX_PARALLEL_AGENT_TASKS` | `8` | Per-service dispatch/poll limit |
| `MAX_DELEGATION_DEPTH` | `3` | Child-delegation loop guard |
| `AGENT_MESH_REAPER_INTERVAL` | `1` | Timeout-reaper interval in seconds |
| `AGENT_MESH_AUTONOMY_ENABLED` | `1` | Enable the background autonomous supervisor |
| `AGENT_MESH_AUTONOMY_INTERVAL` | `1` | Supervisor reconciliation interval |
| `AGENT_MESH_AUTONOMY_MAX_WORKERS` | `4` | Concurrent provider invocations |
| `AGENT_MESH_AUTONOMY_MAX_ROUNDS` | `3` | Maximum audit/revision rounds |
| `AGENT_MESH_AUTONOMY_COMMAND_TIMEOUT` | `1800` | Maximum built-in/custom command duration |

Independent tasks can be dispatched concurrently, subject to each agent's
`max_concurrent_tasks`. Dependencies are stored as a DAG and circular plans
are rejected. Active artifact locks serialize tasks that claim the same path.
Failed prerequisites block dependent tasks. Retries can exclude failed agents
and select another capable registered agent; exhausted retries produce a real
failed state.

## API surface

The legacy `/messages`, `/tasks`, claim, release, and heartbeat routes remain
available. The durable protocol adds:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/agents/{agent}/capabilities` | Read capability and health metadata |
| `GET` | `/capabilities` | Read the capability inventory |
| `GET` | `/shared/capabilities` | Read the federated agent, tool, MCP, and skill catalog |
| `GET` | `/shared/tools` | List safe published tools |
| `GET` | `/shared/skills` | List safe published skills |
| `POST` | `/orchestration/runs` | Create and dispatch an explicit plan |
| `GET` | `/orchestration/runs/{run_id}` | Read tasks, results, and trace events |
| `POST` | `/orchestration/runs/{run_id}/advance` | Reconcile and dispatch runnable tasks |
| `POST` | `/orchestration/runs/{run_id}/finalize` | Store the lead's integrated result |
| `POST` | `/orchestration/runs/{run_id}/cancel` | Cancel pending and active work |
| `GET` | `/autonomous/runs` | List high-level objective states |
| `POST` | `/autonomous/runs` | Submit one objective for automatic planning and execution |
| `GET` | `/autonomous/runs/{run_id}` | Read linked tasks, consultation, events, and final report |
| `POST` | `/autonomous/runs/{run_id}/resume` | Resume a waiting/blocked objective with optional plan/provider role changes |
| `POST` | `/autonomous/runs/{run_id}/cancel` | Cancel the objective and active delegated work |
| `GET` | `/autonomous/adapters` | List non-secret provider adapter availability |
| `POST` | `/tasks/poll` | Deliver and lease requests to a worker |
| `POST` | `/tasks/{task_id}/ack` | Accept or reject a request |
| `POST` | `/tasks/{task_id}/progress` | Publish progress and refresh activity |
| `POST` | `/tasks/{task_id}/result` | Submit a structured worker result |
| `POST` | `/tasks/{task_id}/error` | Report failure and schedule recovery |
| `POST` | `/tasks/{task_id}/verify` | Accept or request revision |
| `POST` | `/tasks/{task_id}/heartbeat` | Refresh a task execution lease |
| `POST` | `/tasks/{task_id}/cancel` | Cancel one task and notify its worker |

The MCP bridge exposes equivalent `agent_mesh_*` tools and supports both
newline-delimited JSON-RPC and `Content-Length` framing.

## Security and fallback

The service remains bound to `127.0.0.1`. `/health` and the metadata-only
`/mcp/` compatibility endpoint are unauthenticated; everything else requires
the existing `AGENT_MESH_TOKEN` reference. Payloads,
vault notes, and error responses redact credential-like values. Worker output
is untrusted data and cannot alter orchestration policy. Cancellation sends a
durable `TASK_CANCEL` notice to an assigned worker where possible.

If no healthy capable worker is available, the task remains durably in
`waiting_agent` and the autonomous request reports `WAITING`; it is not falsely
marked complete. If a planner, auditor, or integrator is unavailable, the same
truthful waiting/fallback rules apply. If the service is unavailable, ordinary
single-agent operation is not replaced with a fake response. Once the service
returns, persisted tasks can be advanced and recovered.

`GET /agents` reports both the normal session `health` and `autonomy_ready`.
`health=offline` means a cooperative client has not sent a recent heartbeat;
`autonomy_ready=true` means the supervisor has a real callable adapter for that
agent and may launch it on demand. A stale or missing adapter is never treated
as successful work.

Cancellation is durable. The supervisor marks the request and linked tasks
cancelled, releases artifact locks, sends `TASK_CANCEL` to assigned cooperative
workers, and terminates running local command process groups at the next
heartbeat/cancellation check. A cancelled run cannot later be finalized.

## Verification

Run the standard-library-only regression suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

The suite covers discovery, consultation tasks, DAG ordering, parallelism,
artifact ownership, ACK/result correlation, duplicate-result protection,
timeouts, retry/reassignment, dependency blocking, persistence/migration,
real subprocess adapter execution through planner/worker/auditor/integrator,
cooperative-worker waiting, HTTP authentication and autonomous HTTP workflow,
backward-compatible routes, plus MCP initialization and tool discovery.
