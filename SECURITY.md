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

All write operations pass through multiple safety layers:

1. **`@vmware_tool` decorator** — mandatory on every MCP tool; provides pre-checks, audit logging, data sanitization, and timeout control
2. **Double confirmation** — CLI destructive commands (delete, force power-off, snapshot revert) require two separate "Are you sure?" prompts
3. **`--dry-run` mode** — all CLI write commands support preview without execution
4. **Audit logging** — every operation (read and write) is logged to `~/.vmware/audit.db` (SQLite WAL) with timestamp, user, target, operation, parameters, and result
5. **Policy engine** — `~/.vmware/rules.yaml` can deny operations by pattern, enforce maintenance windows, and set risk-level thresholds

### Guest Operations Security

Guest command execution (`vm_guest_exec`) requires:
- Explicit `vm_name`, `cmd` (full path to executable), `args`, and `user` parameters
- Valid VMware Tools running inside the guest VM
- vCenter permissions for Guest Operations (separate from VM lifecycle permissions)

No implicit or background command execution occurs.

### Webhook Data Scope

- Webhooks are **disabled by default**
- When enabled, they send only to **user-configured URLs** (Slack, Discord, or custom HTTP endpoints)
- Payloads contain **aggregated alert metadata only** (alarm counts, event types, host status summaries)
- Payloads **never** contain: credentials, IP addresses, personally identifiable information, or raw vSphere API responses

### SSL/TLS Verification

- TLS certificate verification is **enabled by default**
- `disableSslCertValidation: true` exists solely for ESXi hosts using self-signed certificates in isolated lab/home environments
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
