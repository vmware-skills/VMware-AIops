# Security Policy

## Disclaimer

This is a community-maintained open-source project and is **not affiliated with, endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" and "vSphere" are trademarks of Broadcom Inc.

**Author**: Wei Zhou, VMware by Broadcom — wei-wz.zhou@broadcom.com

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it privately:

- **Email**: wei-wz.zhou@broadcom.com
- **GitHub**: Open a [private security advisory](https://github.com/vmware-skills/VMware-AIops/security/advisories/new)

Do **not** open a public GitHub issue for security vulnerabilities.

## Security Design

### Credential Management

- Passwords are stored exclusively in `~/.vmware-aiops/.env` (never in `config.yaml`, never in code)
- `.env` file permissions are verified at startup (`chmod 600` required)
- No credentials are logged, echoed, or included in audit entries
- Each vCenter/ESXi target uses a separate environment variable: `VMWARE_<TARGET_NAME_UPPER>_PASSWORD`

### Destructive Operation Safeguards

Layers 1, 4 and 5 apply to every write on every surface. **Layers 2 and 3 are
CLI-only** — the MCP tools an AI agent calls have no confirmation step and no
dry-run:

1. **`@vmware_tool` decorator** — mandatory on every MCP tool; provides pre-checks, audit logging, data sanitization, and timeout control
2. **Double confirmation (CLI only)** — CLI destructive commands (delete, force power-off, snapshot revert, guest exec/upload) require two separate "Are you sure?" prompts. An agent with a shell can satisfy both with `yes |`; this defends the mistyped command, not a determined caller
3. **`--dry-run` mode (CLI only)** — all CLI write commands support preview without execution
4. **Audit logging** — every operation (read and write) is logged to `~/.vmware/audit.db` (SQLite WAL) with timestamp, user, target, operation, parameters, and result
5. **Policy engine** — `~/.vmware/rules.yaml` can deny operations by pattern or risk level and enforce maintenance windows. It is opt-in: nothing is denied until you write a rule

**The primary control is the vCenter/ESXi service account.** This skill ships
full read+write and does not gate read-versus-write itself; a write the account
may not perform is refused by vCenter, on every surface, with no way around it
from inside the skill. To run an AI agent read-only, give it a read-only vCenter
role rather than relying on any switch here — the earlier `VMWARE_READ_ONLY`
switch was removed in v1.8.7 precisely because it was enforced on the MCP path
only and any agent with a shell stepped around it. The gated-versus-ungated
inventory is documented, and machine-checked against the tool registry, in
[references/capabilities.md](skills/vmware-aiops/references/capabilities.md#what-gates-a-write).

### Guest Operations Security

Guest command execution (`vm_guest_exec`) requires:
- Explicit `vm_name`, `command` (full path to executable), `arguments`, and `username` parameters
- Valid VMware Tools running inside the guest VM
- vCenter permissions for Guest Operations (separate from VM lifecycle permissions)

No implicit or background command execution occurs.

**This is the widest blast radius in the skill, and it is ungated over MCP.**
The command string is caller-supplied and unbounded, and it runs with the guest
credentials passed to the call — which the documented example makes `root`. The
CLI form double-confirms; the MCP tool does not, and neither do
`vm_guest_exec_output` or the `exec` steps inside `vm_guest_provision`.

The **guest** account is a second authorization boundary, independent of the
vCenter one: a read-only vCenter role does not constrain what these tools do
*inside* a VM. Give them a guest account scoped to the work, and if you do not
need guest operations, do not configure guest credentials at all. All four tools
that push a command or a file into a guest declare `destructiveHint: true` so a
client can ask its user before calling them.

### Webhook Data Scope

- Webhooks are **disabled by default**
- When enabled, they send only to **user-configured URLs** (Slack, Discord, or custom HTTP endpoints)
- Payloads contain **aggregated alert metadata only** (alarm counts, event types, host status summaries)
- Payloads **never** contain: credentials, IP addresses, personally identifiable information, or raw vSphere API responses

### SSL/TLS Verification

- TLS certificate verification is **enabled by default**
- `verify_ssl: false` exists solely for ESXi hosts using self-signed certificates in isolated lab/home environments
- In production, always use CA-signed certificates with full TLS verification

### Transitive Dependencies

- `vmware-policy` is the only transitive dependency auto-installed; it provides the `@vmware_tool` decorator and audit logging
- All other dependencies are standard Python packages (pyVmomi, Click, Rich, APScheduler, python-dotenv)
- No post-install scripts or background services are started during installation

### Prompt Injection Protection

- All vSphere-sourced content (VM names, event messages, host logs) is processed through `_sanitize()`
- Sanitization truncates to 500 characters and strips C0/C1 control characters
- Output is wrapped in boundary markers (`[VSPHERE_EVENT]`, `[VSPHERE_HOST_LOG]`) when consumed by LLM agents

## Static Analysis

This project is scanned with [Bandit](https://bandit.readthedocs.io/) before every release, targeting 0 Medium+ issues:

```bash
uvx bandit -r vmware_aiops/
```

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.5.x   | Yes       |
| < 1.5   | No        |
