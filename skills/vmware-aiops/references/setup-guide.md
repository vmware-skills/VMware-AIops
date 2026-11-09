# Setup Guide

## Installation

All install methods fetch from the same source: [github.com/vmware-skills/VMware-AIops](https://github.com/vmware-skills/VMware-AIops) (MIT licensed). We recommend reviewing the source code before installing.

```bash
# Via PyPI (recommended for version pinning)
uv tool install vmware-aiops==2.4.6

# Via Skills.sh (fetches from GitHub)
npx skills@1.7.6 add vmware-skills/VMware-AIops#v2.4.6

# Via ClawHub (fetches from ClawHub registry snapshot of GitHub)
clawhub install @zw008/vmware-aiops --version 2.4.6
```

The installer is pinned to `skills@1.7.6` (Node.js >=22.20.0); the repository tag separately selects the skill revision. Review the installer and its dependencies before running it. Pinning the top-level package does not lock its dependency graph. Alternative: use the release-tagged manual copy below.

### Claude Code

`npx skills add` and `clawhub install` both place the skill in Claude Code's skills
directory. To install it manually from a clone:

```bash
mkdir -p ~/.claude/skills/vmware-aiops
cp -r skills/vmware-aiops/. ~/.claude/skills/vmware-aiops/
```

For tool access (not just skill context), register the MCP server:

```bash
claude mcp add vmware-aiops -- vmware-aiops mcp
```

## Configuration

```bash
# 1. Install from PyPI (source: github.com/vmware-skills/VMware-AIops)
uv tool install vmware-aiops==2.4.6

# 2. Verify installation source
vmware-aiops --version  # confirms installed version

# 3. Configure
mkdir -p ~/.vmware-aiops
vmware-aiops init  # generates config.yaml and .env templates
chmod 600 ~/.vmware-aiops/.env
# Edit ~/.vmware-aiops/config.yaml and .env with your target details
```

### Declare `environment:` on each target

```yaml
targets:
  - name: prod-vcenter
    host: vcenter-prod.example.com
    environment: production   # production | staging | lab | <your own label>
```

`environment:` is an optional free-form label. Policy scopes its rules by this
value, so an environment-scoped `deny` rule in `~/.vmware/rules.yaml` can match
on it — for example, to freeze state-changing writes on `production`. A target
with no label is simply not matched by such a rule. A rule refuses only what its
`operations` / `min_risk_level` filters match, so scope it to write operations if
reads should keep working. Rules are evaluated before every MCP tool call and
every CLI command that reaches vCenter, and `operations` are MCP tool names — CLI commands are
authorised under the same names, so one rule covers both. The shipped baseline
denies nothing.

## What Gets Installed

The `vmware-aiops` package installs a Python CLI binary and its dependencies (pyVmomi, Click, Rich, APScheduler, python-dotenv). No background services, daemons, or system-level changes are made during installation. The scheduled scanner (`daemon start`) only runs when explicitly started by the user.

## Development Install

```bash
git clone --branch v2.4.6 https://github.com/vmware-skills/VMware-AIops.git
cd VMware-AIops
uv venv && source .venv/bin/activate
# --no-sources: pyproject's [tool.uv.sources] points vmware-monitor at a sibling
# checkout (../VMware-Monitor) for family development. A fresh clone has none, so
# plain `uv pip install -e .` fails with "Distribution not found"; this takes
# vmware-monitor (>=1.11.3) from PyPI instead.
uv pip install --no-sources -e .
```

To develop against an unreleased vmware-monitor, clone
[VMware-Monitor](https://github.com/vmware-skills/VMware-Monitor) next to this
checkout (so `../VMware-Monitor` exists) and drop `--no-sources`. Plain `pip`
ignores `[tool.uv.sources]` and needs neither.

### Password obfuscation at rest

On first load, any plaintext `*_PASSWORD` value in `.env` is automatically
rewritten to a grep-safe `b64:<encoded>` form and decoded transparently at
runtime, so a casual `grep` of the file no longer reveals the password. Values
are read and written through python-dotenv's own parser, so the stored secret
never drifts from what you configured (quotes, inline comments, and trailing
whitespace are handled correctly).

> **This is obfuscation, not encryption.** Anyone who can read the file can
> still decode it. For real secrecy at rest, do not store the password in `.env`
> at all — inject it from a secret manager (HashiCorp Vault, CyberArk, AWS
> Secrets Manager, or a Kubernetes Secret) into the `*_PASSWORD` environment
> variable at process start. The code reads the env var either way.

## Security

> **Disclaimer**: This is a community-maintained open-source project and is **not affiliated with, endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" and "vSphere" are trademarks of Broadcom.

- **Source Code**: Fully open source at [github.com/vmware-skills/VMware-AIops](https://github.com/vmware-skills/VMware-AIops) (MIT). The `uv` installer fetches the `vmware-aiops` package from PyPI, which is built from this GitHub repository. We recommend reviewing the source code and commit history before deploying in production.
- **TLS Verification**: Enabled by default. Setting `verify_ssl: false` is solely for ESXi hosts using self-signed certificates in isolated lab/home environments. In production, always use CA-signed certificates with full TLS verification.
- **Credentials & Config**: This skill requires the following secrets, all stored in `~/.vmware-aiops/.env` (`chmod 600`, loaded via `python-dotenv`):
  - `VMWARE_<TARGET>_PASSWORD` — per-target password where `<TARGET>` is the uppercased target name from `config.yaml` (hyphens become underscores). Example: target named `vcenter-prod` uses `VMWARE_VCENTER_PROD_PASSWORD`.
  - (Optional) Webhook URLs for Slack/Discord notifications

  The config file `~/.vmware-aiops/config.yaml` stores only target hostnames, ports, and usernames — it does **not** contain passwords or tokens. The env var `VMWARE_AIOPS_CONFIG` points to this YAML file.
- **Webhook Data Scope**: Webhook notifications are **disabled by default**. When enabled, the daemon posts to **user-configured URLs only** (Slack, Discord, or any HTTP endpoint you control); no data is sent to any other service. Each payload carries critical/warning counts plus every critical issue and every alarm/event warning from that scan — host-log warnings go to `scan.log` only, and `info` rows (unreadable or partly-read host logs) are never sent. Each issue carries the entity name and one of: the alarm name, vCenter event message (sanitized, ≤500 chars), ESXi log line matching critical/panic/corrupt (sanitized, ≤200 chars), or the error text for a target the daemon could not connect to. Event, log, and error text can contain host names, IP addresses, and user names — treat the webhook destination as receiving operational data. No credentials from the skill's config or `.env` are included.
- **Daemon host-log reads**: the scanner daemon reads the ESXi `hostd`, `vmkernel` and `vpxa` logs, which needs the `Global.Diagnostics` privilege — vCenter's built-in Read-Only role does not include it. Without it each log is recorded in `scan.log` as an `info` row with the reason instead of being scanned; grant it only if you want host-log scanning.
- **Prompt Injection Protection**: All vSphere-sourced content (event messages, host logs) is truncated, stripped of control characters, and wrapped in boundary markers (`[VSPHERE_EVENT]`/`[VSPHERE_HOST_LOG]`) before output to prevent prompt injection when consumed by LLM agents.
- **Least Privilege**: 23 of the 43 MCP write tools — every destructive one — return a no-write blast-radius preview unless called with `confirm=True`, which is refused on a blocker or an unreadable measurement; `delete_vm` also requires the preview's acknowledgement echoed back. The other 20 (create, clone, deploy, power-on, reconfigure) act on the first call. A preview is not authorization. The enforcement boundary is the RBAC of the vCenter/ESXi account in `.env`, so use a dedicated service account scoped to what the agent may change. For monitoring-only use cases, prefer the read-only [VMware-Monitor](https://github.com/vmware-skills/VMware-Monitor) skill which has zero destructive code paths. The CLI's double confirmation and `--dry-run` do not apply to MCP calls.
- **Guest Credentials**: Guest operations run with whatever guest account is passed to them — the `username` is required (there is no default account) and over MCP the password is a tool argument the agent sees (the audit row redacts it). A read-only vCenter role does not limit what they do inside a VM. Pass a least-privilege guest account; avoid root unless the task needs it. `upload_vm_guest_file` reads any local file the server process can read.
- **Policy & Audit**: Optional `deny` rules in `~/.vmware/rules.yaml` refuse matching operations before every MCP call and every CLI command that reaches vCenter (see `environment:` above); they run in-process and are a guardrail, not a substitute for RBAC. Every such call is recorded in `~/.vmware/audit.db` with credentials redacted (best-effort: an audit write failure warns and does not block).

To run the agent read-only, give it a read-only vCenter/ESXi service account (RBAC) — enforced at the platform.

## Supported AI Platforms

| Platform | Status | Config File |
|----------|--------|-------------|
| Claude Code | ✅ Native Skill | `skills/vmware-aiops/SKILL.md` |
| Gemini CLI | ✅ Context file + MCP | `skills/vmware-aiops/SKILL.md` |
| OpenAI Codex CLI | ✅ Skill + AGENTS.md | `skills/vmware-aiops/SKILL.md` |
| Aider | ✅ Conventions | `skills/vmware-aiops/SKILL.md` |
| Continue CLI | ✅ Rules | `skills/vmware-aiops/SKILL.md` |
| Trae IDE | ✅ Rules | `skills/vmware-aiops/SKILL.md` |
| Kimi Code CLI | ✅ Skill | `skills/vmware-aiops/SKILL.md` |
| MCP Server | ✅ MCP Protocol | `vmware_aiops/mcp_server/` |
| Python CLI | ✅ Standalone | N/A |

## MCP Server — Local Agent Compatibility

The MCP server works with any MCP-compatible agent via stdio transport. Config templates in `examples/mcp-configs/`:

| Agent | Local Models | Config Template |
|-------|:----------:|-----------------|
| Goose (Block) | ✅ Ollama, LM Studio | `goose.json` |
| LocalCowork (Liquid AI) | ✅ Fully offline | `localcowork.json` |
| mcp-agent (LastMile AI) | ✅ Ollama, vLLM | `mcp-agent.yaml` |
| VS Code Copilot | — | `vscode-copilot.json` |
| Cursor | — | `cursor.json` |
| Continue | ✅ Ollama | `continue.yaml` |
| Claude Code | — | `claude-code.json` |

```bash
# Example: Aider + Ollama (fully local, no cloud API)
aider --conventions skills/vmware-aiops/SKILL.md --model ollama/qwen2.5-coder:32b
```

## MCP Mode (Optional)

For Claude Code / Cursor users who prefer structured tool calls, add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "vmware-aiops": {
      "command": "vmware-aiops",
      "args": ["mcp"],
      "env": {
        "VMWARE_AIOPS_CONFIG": "~/.vmware-aiops/config.yaml"
      }
    }
  }
}
```

> v1.5.15+ recommends the single-command form `vmware-aiops mcp`. Pre-1.5.15 used
> `uvx --from vmware-aiops==2.4.6 vmware-aiops-mcp`, which still works but re-resolves from
> PyPI on each launch and breaks behind corporate TLS proxies. The legacy
> `vmware-aiops-mcp` entry point is also kept for backward compatibility.

MCP exposes 61 tools across 11 categories. All accept optional `target` parameter.

## Limiting which tools are offered

By default the MCP server offers every tool. To offer only read tools, start it with `--expose read`, or set
`VMWARE_AIOPS_MCP_EXPOSE=read` in the MCP client's `env` block (useful when the client launches `vmware-aiops mcp` itself). To offer
chosen write tools as well, add `--allow-write` with a comma-separated list (or `VMWARE_AIOPS_MCP_ALLOW_WRITE`):

```bash
vmware-aiops mcp --expose read --allow-write acknowledge_vcenter_alarm
```

- A read tool is one annotated `readOnlyHint: true`; every other tool counts as a write.
- An unknown setting, or a name that is not a write tool of this server, stops the server before it serves (exit
  code 2), so it never offers more than you asked for. Pre-2.0 tool names are accepted.
- With `--expose read`, the server prints one line to stderr saying what it exposes.
- Flags and environment variables are not mixed: if either flag is given, both environment variables are
  ignored, so a leftover `_ALLOW_WRITE` in the shell cannot widen `--expose read`. A blank flag value counts as
  not given.
- A tool that runs other steps (a plan, workflow or batch tool) carries every write it can execute, including
  writes this setting hides. Expose such a tool only if you would expose those writes too.
- A launcher that imports the server object can call
  `vmware_policy.exposure.restrict_tools(mcp, skill="aiops", expose="read", allow_write=[...])` before running it,
  instead of editing the SDK's tool registry.
- This limits what clients are offered. It is **not** authorization: whether a call may change anything is decided by
  the account's permissions, and deny rules in `~/.vmware/rules.yaml` still apply to every exposed tool.
- An agent that can run shell commands can also reach the platform without MCP (SSH, curl, govc, PowerCLI). So
  the service account itself must be least-privilege: exposure and deny rules are guardrails, not the boundary.

## Serving MCP over HTTPS

By default the MCP server speaks stdio to one local client. To let clients on other machines reach it, serve it over
HTTPS. Every caller then needs a bearer token.

```bash
vmware-audit token create alice                      # prints the token once; give it to alice
vmware-aiops mcp --transport https --host 0.0.0.0 --port 8443 \
    --cert /etc/vmware-mcp/server.pem --key /etc/vmware-mcp/server-key.pem \
    --public-name mcp.example.com
```

- Clients connect to `https://mcp.example.com:8443/mcp` and send `Authorization: Bearer <token>`.
- `--public-name` lists every host name clients use (comma-separated); requests for any other Host or Origin are
  refused. Use a certificate your clients trust for those names.
- Behind a reverse proxy that terminates TLS (Nginx, HAProxy) on the same machine, use
  `--transport http --host 127.0.0.1 --public-name mcp.example.com` (the names the proxy forwards in `Host`), and
  have the proxy pass the `Authorization` header through.
- Behind a load balancer or gateway on another machine, keep `--transport https` (a self-signed certificate is
  fine: the balancer is the client that has to trust it), list in `--public-name` the host name the balancer sends
  as `Host`, and have it forward `Authorization`. Point its health probe at `GET /healthz`: it needs no token, returns
  only `{"status": "ok"}` and accepts any `Host` (vmware-policy ≥ 1.23.0). Every other path still needs the token.
- Correlation: each audit row stores the caller's `traceparent` trace-id, else its `X-Request-ID` /
  `X-Correlation-ID`. Have the agent platform send one, and `vmware-audit log --correlation-id ID` lists the calls
  behind one platform request.
- Tools that read or write files on this server (here: guest file upload/download, OVA and deployment-spec files) never touch its configuration, tokens, audit log or
  TLS key, and over the network they are off unless `VMWARE_MCP_FILE_ROOT` names a directory for them
  (vmware-policy ≥ 1.23.1).
- `vmware-audit token list` / `token revoke NAME` manage callers; a revoked token is refused from its next request.
  Replace a leaked token under a new name.
- The server will not start without at least one token, and checks before it binds that unauthenticated requests
  are refused.
- Each audit row records the caller as `token:<name>`. Calls run side by side, with up to `VMWARE_MCP_POOL_SIZE`
  (default 2) sessions per target; each session is a login on the target, and a call waits at most 300 s for one.
- **Everyone who can reach the endpoint acts with this server's account.** Tokens identify callers; they grant
  nothing on the platform. Run one endpoint per privilege level (for example a read-only account behind
  `--expose read`, and a separate endpoint for operators).
- **Several people or roles on one endpoint:** run `vmware-audit roles enable` once, then give each token a role (`vmware-audit token create NAME --role reader`, or a role in `roles.yaml` naming tools and targets; vmware-policy ≥ 1.24.0). A role limits what the token may call on this endpoint; it cannot grant more than the account has. See vmware-policy's README, "Roles for network callers".

