# SECURITY.md

# Security Guide for AI Second Brain

This document outlines the security rules, best practices, and guidelines for operating the AI Second Brain multi-agent coordination system.

## Core Security Principles

### 1. Zero-Trust Architecture

- **Local-only binding**: All services must bind to `127.0.0.1` only, never to public interfaces
- **No external exposure**: Never expose any local service to the internet
- **Isolated networking**: Services should only communicate through known, secured local channels
- **Principle of least privilege**: Agents should only have access to resources they explicitly need

### 2. Secret Management

#### Forbidden Locations

DO NOT store these anywhere in the Obsidian vault or configuration files:

- **API Keys**: OpenAI, Anthropic, Gemini, NVIDIA, Obsidian, etc.
- **Authentication Tokens**: Bearer tokens, API keys, session cookies
- **Sudo/Admin Passwords**: Never paste, store, or echo passwords
- **Private Keys**: SSH keys, SSL certificates, private API credentials
- **Browser Data**: Cookies, session storage, local storage, profile data

#### Allowed Storage

- **Environment Variables**: Check existence via `echo $KEY` or `test -n "$KEY"`
- **Config References**: Use `${NVIDIA_API_KEY}`, never actual values
- **Secret Managers**: OS keychain or dedicated secret managers (values never exposed)
- **Temporary Files**: Only in `.agent_mesh/tmp/` and deleted before completion

#### Secret Status Documentation

For each environment variable, maintain only status indicators:

```markdown
| Key | Status |
|-----|--------|
| NVIDIA_API_KEY | present |
| GEMINI_API_KEY | missing |
| ANTHROPIC_API_KEY | unverified |
```

Never document actual key values.

### 3. Sudo and Admin Rights

#### Requirements

- **Minimal use**: Only for system package installation, service configuration, or port binding
- **Interactive prompts**: Use `sudo -v` for interactive password entry, never batch operations
- **No history**: Ensure passwords are never stored in shell history or logs

#### Safe Practice

```bash
# DO: Use interactive prompt
sudo -v

# DON'T: Paste password into prompts or config files
```

### 4. Browser Permissions and Account Access

#### Rules

1. **No Password Pasting**: Never ask users to paste passwords, OTPs, cookies, OAuth codes, or tokens into AI chat
2. **Manual Approval**: User must complete login/approval manually in browser
3. **Firefox Only**: Open official authorization links in Firefox browser
4. **Local Callback Binding**: Any callback server must bind to `127.0.0.1` only
5. **Status Logging**: Record only permission status, not secrets or credentials

#### Safe Commands

```bash
# macOS/Linux
firefox "https://official-oauth-provider.com/auth"
xdg-open "https://official-oauth-provider.com/auth"

# Windows PowerShell
Start-Process firefox "https://official-oauth-provider.com/auth"
```

#### Preferred Browser Command (macOS/Linux)

```bash
firefox "https://official-permission-or-login-url.example"
```

#### Fallback Browser Command (Linux)

```bash
xdg-open "https://official-permission-or-login-url.example"
```

#### Windows PowerShell Command

```powershell
Start-Process firefox "https://official-permission-or-login-url.example"
```

## Agent Security Rules

### 1. Access Control

#### File Access

- **Read-only for web agents**: Cannot modify files or config
- **Local agents only**: Can read/write files if explicitly trusted
- **Capability-based access**: Agents only access what they need to function

#### Shell Access

- **No global modifications**: Do not run `sudo apt-get install` or similar
- **Command safety**: Only run verified, safe commands
- **Interactive sessions**: Use interactive prompts for password-sensitive operations

#### Browser Access

- **Manual navigation only**: Cannot automate login or form filling
- **Firefox requirement**: All browser interactions through Firefox only

### 2. Skill and MCP Server Security

#### Registration Requirements

When advertising skills or MCP servers:

- **Safe invocation**: Document how to invoke safely
- **Input/output validation**: Define and validate input/output formats
- **Capability limits**: Define clear safety limits and restrictions
- **Owner accountability**: Track which agent owns the capability

#### Skill Registry Security

```markdown
# Skill: <skill-name>

- Owner agent:
- Skill type:
- Input format:
- Output format:
- How to invoke safely:
- Required context:
- Limitations:
- Example request:
- Last verified:
```

#### MCP Server Registry Security

```markdown
| MCP Server | Owner Agent | Endpoint | Transport | Auth | Tools | Status |
|---|---|---|---|---|---|---|
| obsidian | shared | https://127.0.0.1:27124/mcp/ | HTTP | ${OBSIDIAN_API_KEY} | vault read/write/search | active |
| agent-mesh | shared | http://127.0.0.1:17860/mcp/ | HTTP | ${AGENT_MESH_TOKEN} | tasks/messages/handoffs/memory | active |
```

### 3. Connection Repair and Recovery

#### When Connection Fails

If an agent cannot connect to Obsidian, Agent Mesh, MCP, or other agents:

1. **Failure Reporting**: Write report to `08_Inbox/<agent-name>_connection_failure.md`
2. **Help Request**: Create help request in Agent Mesh to `any-capable-agent`
3. **Inspection**: Connected agents inspect the failure report
4. **Repair Methods**: Attempt one of:
   - **MCP config patch**: Update config using env var references only
   - **Local REST adapter**: Write adapter directing to local endpoints
   - **CLI wrapper**: Create wrapper script in `.agent_mesh/scripts/`
   - **Manual steps**: Document exact manual instructions
   - **Memory Capsule**: Export capsule for manual import

#### Repair Completion Checklist

A repair is complete only when:

- The repaired agent has an agent profile note
- It can read or receive Obsidian context
- It can send or receive Agent Mesh messages
- A test handoff is recorded
- A connection report is in `08_Inbox/`
- No secrets were written
- No junk/temp files remain

## Task and Memory Security

### 1. Task Security

#### Task Note Requirements

Every task must have:

- **Status tracking**: Current state, owner, assignee
- **Work log**: Agent actions and results
- **Attempt tracking**: What was tried and results
- **Context preservation**: Resume packet before handoff
- **Security notes**: Any security-related issues or decisions

#### Safe Task Transfer

When transferring tasks:

1. **Resume packet**: Update task note before stopping
2. **No secret repetition**: Never copy previously attempted failed strategies
3. **Limited scope**: Transfer only necessary context
4. **Verification**: Verify transfer was successful

### 2. Memory Security

#### Safe Memory Sources

Import memory only from:

- Existing Obsidian notes
- Exported chats
- Project README files
- Project documentation
- Local Friday memory files (clearly user-owned)
- Task files
- Issue files
- Planning files
- Codebase context files

#### Memory Types to Avoid

DO NOT import:

- API keys or passwords
- Cookies or session tokens
- SSH keys
- Password manager data
- Private token files
- Unrelated credential files

#### Memory Status Fields

For each memory entry, include:

- **Source**: Where the memory came from
- **Confidence**: How certain you are (high/medium/low)
- **Sensitivity**: Access level (public/internal/restricted)
- **Redacted secrets**: Any secrets that were redacted

## Browser Security Rules

### 1. Account Permission Guidelines

If any integration requires account permission, OAuth login, dashboard access, or authorization:

- **No password entry**: Do not ask for passwords or API keys
- **Manual completion**: User must complete authorization in browser
- **Official URLs**: Use official authorization links only
- **Firefox only**: Open links in Firefox browser

### 2. Callback Server Security

For any OAuth or authorization callback:

- **Local binding**: Must bind to `127.0.0.1` only
- **URL validation**: Use localhost callback URLs only
- **State validation**: Implement OAuth state parameter validation
- **Secure storage**: Store temporary codes only in `.agent_mesh/tmp/` (deleted after)

### 3. Session Management

- **No cookie storage**: Never save browser cookies
- **Session handling**: Each authorization session is one-time use only
- **Cleanup**: Clear all temporary data after authorization flow

## Operation Security

### 1. File System Security

#### Temporary Files

- **Location**: Only in `.agent_mesh/tmp/`
- **Creation**: Only when absolutely necessary
- **Deletion**: Must delete before finishing any task
- **Contents**: No secrets, only temporary workspace data

#### Backup Files

- **No automatic backups**: Only create manual backups if explicitly requested
- **Risk assessment**: Never back up while risky operations are in progress
- **Manual process**: Use BACKUP_AND_RESTORE.md for manual backup procedures

#### Junk File Prevention

DO NOT create:

- **Backup files**: `.bak`, `.backup`, `.old`, `.tmp`, `.temp`
- **Configuration duplicates**: Never copy `.env` files with real keys
- **Exported keys**: Never export key files or certificates
- **Scripts**: Don't create unnecessary installer scripts
- **Archives**: Never create unnecessary zip/tar files
- **Temporary folders**: Use only `.agent_mesh/tmp/` if needed

### 2. Logging Security

#### Safe Logging

- **No secrets**: Never log API keys, passwords, or tokens
- **Redaction**: Always redact sensitive information before logging
- **Minimal data**: Only log what's necessary for debugging

#### Log Audit

Review logs for:

- Unusual access patterns
- Failed authentication attempts
- Suspicious file operations
- Unexpected network connections

## Service Security

### 1. Agent Mesh Service Security

#### Service Binding

- **Host binding**: Only bind to `127.0.0.1`
- **Port restrictions**: Use default port `17860` unless changed in config
- **Authentication**: Bearer token validation via `Authorization: Bearer ${AGENT_MESH_TOKEN}`
- **CORS restrictions**: Only allow local origins

#### API Security

- **Input validation**: Validate all API request parameters
- **Rate limiting**: Prevent brute force attacks
- **Error handling**: Don't leak information in error messages
- **HTTPS only**: Use HTTPS for all external communications (if any)

### 2. MCP Server Security

#### Server Configuration

- **Transport security**: Use secure transport (HTTPS for network, file permissions for local)
- **Authentication**: Bearer token via `${OBSIDIAN_API_KEY}`
- **Access limits**: Define and enforce tool access limits
- **Audit trail**: Log all MCP server access attempts

## Incident Response

### 1. Unauthorized Access

If unauthorized access is detected:

1. **Isolate affected agent**: Block communication with compromised agent
2. **Change tokens**: Rotate all affected API tokens
3. **Audit logs**: Review all logs from the affected time period
4. **Recover**: Restore from safe backup if needed
5. **Notify**: Inform all connected agents of the incident

### 2. Data Leak Detection

If a potential data leak is discovered:

1. **Identify source**: Find where the leak occurred
2. **Contain**: Stop further data exposure
3. **Recover**: Restore affected components
4. **Report**: Document the incident for future prevention

### 3. Service Disruption

If service is disrupted:

1. **Diagnose**: Identify the root cause
2. **Recover**: Restore service using available backups or repair procedures
3. **Verify**: Ensure service is fully functional before resuming operations
4. **Document**: Record the incident and resolution

## Compliance and Policies

### 1. Local-First Policy

- **No cloud dependencies**: All services run locally
- **No data export**: Don't send any data to external services
- **No remote access**: All administration is done locally

### 2. Privacy Policy

- **No user data**: Don't collect or store personal user information
- **No tracking**: No analytics or tracking mechanisms
- **No data sharing**: Don't share data with third parties

### 3. Security Maintenance

Regular maintenance tasks:

- **Review logs**: Weekly review of security-relevant logs
- **Update agents**: Keep all agents and dependencies updated
- **Verify configurations**: Ensure all configuration files are secure
- **Test backups**: Verify backup and restore procedures work
- **Audit permissions**: Review agent permissions and access rights

## Emergency Procedures

### 1. Agent Crash Recovery

If an agent crashes or becomes unresponsive:

1. **Detect**: Check heartbeats and task status
2. **Notify**: Alert other capable agents
3. **Resume**: Another agent claims and resumes the task
4. **Document**: Record the crash and recovery

### 2. Service Failure Response

If the Agent Mesh service fails:

1. **Backup check**: Verify backup exists
2. **Restore**: Restore from backup if needed
3. **Rebuild**: Rebuild the service if necessary
4. **Test**: Verify service is functional before use

### 3. Security Incident Response

1. **Contain**: Isolate affected components
2. **Recover**: Restore from clean backups
3. **Rebuild**: Re-deploy secure versions
4. **Document**: Record the incident for future prevention

## Conclusion

Security is not a feature but a fundamental requirement for the AI Second Brain. By following these guidelines and rules, you ensure that your multi-agent coordination system remains secure, private, and reliable.

Always prioritize security in all decisions. When in doubt, follow the most conservative security approach. Remember: security is a continuous process, not a one-time setup.

For ongoing security concerns, refer to the Security Scorecards in the Memory Index and update them regularly.
