<!-- mcp-name: io.github.vmware-skills/vmware-aiops -->
# VMware AIops

[![MCP Toplist](https://mcptoplist.com/badge/io.github.vmware-skills%2Fvmware-aiops.svg)](https://mcptoplist.com/server/io.github.vmware-skills%2Fvmware-aiops)

> **Author**: Wei Zhou, VMware by Broadcom — wei-wz.zhou@broadcom.com
> This is a community-driven project by a VMware engineer, not an official VMware product.
> For official VMware developer tools see [developer.broadcom.com](https://developer.broadcom.com).

English | [中文](README-CN.md)

AI-powered VMware vCenter/ESXi VM lifecycle and deployment tool — 60 tools.

> **Companion skills** handle everything else:
>
> | Skill | Scope | Install |
> |-------|-------|---------|
> | **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | Read-only: inventory, health, alarms, events, metrics | `uv tool install vmware-monitor` |
> | **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | Datastores, iSCSI, vSAN management | `uv tool install vmware-storage` |
> | **[vmware-vks](https://github.com/vmware-skills/VMware-VKS)** | Tanzu Namespaces, TKC cluster lifecycle | `uv tool install vmware-vks` |
>
> **Need read-only monitoring only?** Use [VMware-Monitor](https://github.com/vmware-skills/VMware-Monitor) — zero destructive code in the codebase.

[![ClawHub](https://img.shields.io/badge/ClawHub-vmware--aiops-orange)](https://clawhub.ai/skills/vmware-aiops)
[![Skills.sh](https://img.shields.io/badge/Skills.sh-Install-blue)](https://skills.sh/vmware-skills/VMware-AIops)
[![Claude Code Marketplace](https://img.shields.io/badge/Claude_Code-Marketplace-blueviolet)](https://github.com/vmware-skills/VMware-AIops)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## ⚡ Quick Investigation Reports (read-only)

Triage → investigate → act, all in one conversation. Five opinionated read-only reports **aggregate and correlate server-side** and hand back a high-signal result (never raw inventory), so you can decide *where to look* before changing anything. Each renders a **self-contained offline HTML snapshot** with `--html` (no external assets; drill-down detail collapses in native `<details>`, zero JavaScript). All delegate to the [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor) library using AIops's own vCenter connection.

| Question | Command | What it correlates |
|----------|---------|--------------------|
| **"What needs attention now?"** across all vCenters | `vmware-aiops attention` | Every vCenter merged into one globally-ranked issue list; unreachable targets degrade gracefully |
| **"Is anything on fire?"** across all clusters | `vmware-aiops summary` | Every cluster's hosts + VM power + live CPU/mem + alarms → ranked top-N issues + per-cluster status |
| **"What's happening around this VM?"** | `vmware-aiops investigate vm <name>` | VM state + host + cluster + backing datastores + snapshots + alarms + performance + a merged event timeline |
| **"What's happening around this host?"** | `vmware-aiops investigate host <name>` | Host state + cluster + the VMs it runs + mounted datastores + alarms + performance + correlated timeline |
| **"What's happening around this datastore?"** | `vmware-aiops investigate datastore <name>` | Capacity/free + mounting hosts + VMs it backs + alarms + correlated timeline |

```bash
vmware-aiops attention                            # what needs attention now, all vCenters
vmware-aiops investigate vm web-01 --hours 72     # everything around a VM, then act on it
vmware-aiops investigate vm web-01 --html         # → offline snapshot in ~/vmware-health/
```

Via MCP these are the tools `cluster_health_summary`, `cross_vcenter_attention`, `vm_investigation_bundle`, `host_investigation_bundle`, `datastore_investigation_bundle`. (Requires `vmware-monitor` installed.)

### Quick Install (Recommended)

Works with Claude Code, Cursor, Codex, Gemini CLI, Trae, and 30+ AI agents:

```bash
# Via Skills.sh
npx skills add vmware-skills/VMware-AIops

# Via ClawHub
clawhub install vmware-aiops
```

### PyPI Install (No GitHub Access Required)

```bash
# Install via uv (recommended)
uv tool install vmware-aiops

# Or via pip
pip install vmware-aiops

# China mainland mirror (faster)
pip install vmware-aiops -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Offline / Air-Gapped Install (from source)

This project uses the modern PEP 517 build system (hatchling), so there is **no
`setup.py`** by design — that is expected, not a missing file. If you cloned the
source and hit `ERROR: File "setup.py" or "setup.cfg" not found ... editable mode
currently requires a setuptools-based build`, your `pip` is older than 21.3 and
cannot do an *editable* (`-e`) install with a non-setuptools backend. Editable
mode is a developer convenience, not needed to run the tool — do one of:

```bash
# From the source tree — a normal (non-editable) install builds a wheel:
pip install .              # NOT  pip install -e .

# ...or upgrade pip first, and editable works too:
pip install --upgrade pip && pip install -e .
```

For a **truly air-gapped host**, build the wheels on a connected machine and copy
them over — the target then needs no network:

```bash
# On a connected machine, collect this package + its dependencies as wheels:
pip wheel . -w dist        # → dist/*.whl   (or: uv build, for just this package)

# Copy dist/ to the air-gapped host, then install offline:
pip install --no-index --find-links dist vmware-aiops
```

---

## Why this over other VMware MCP servers

Most open-source VMware MCP servers (e.g. `bright8192/esxi-mcp-server`,
`giuliolibrando/vmware-vsphere-mcp-server`) are **single-vCenter VM wrappers**:
list/power/snapshot a VM, basic monitoring, a `confirm=True` flag. They explicitly
do not cover networking, storage, Kubernetes, ops analytics, load balancing, or
compliance — and "logging is documented" is not an audit trail.

This is one skill in an **11-package family** that covers the whole estate and runs
every tool through a governed harness:

| | Other VMware MCP servers | This family |
|---|:---:|:---:|
| VM lifecycle + monitoring | ✅ | ✅ |
| NSX networking (segments/gateways/NAT/routing/IPAM) | ❌ | ✅ vmware-nsx |
| NSX security (DFW/groups/IDS-IPS/traceflow) | ❌ | ✅ vmware-nsx-security |
| Storage (datastore/iSCSI/vSAN) | ❌ | ✅ vmware-storage |
| Tanzu Kubernetes (Supervisor/Namespace/TKC) | ❌ | ✅ vmware-vks |
| Aria Operations (metrics/alerts/capacity) | ❌ | ✅ vmware-aria |
| AVI / NSX ALB load balancing + AKO | ❌ | ✅ vmware-avi |
| Compliance baselines + drift (CIS/SCG/等保/PCI) | ❌ | ✅ vmware-harden |
| **Governed harness** (unified audit, policy engine, token budget + runaway breaker, graduated risk tiers, undo-token, prompt-injection sanitize) | ❌ | ✅ vmware-policy on every tool |

If you only ever power-cycle VMs in one vCenter, a single-file server is fine. If you
run a real (regulated, NSX-segmented, multi-domain) VMware estate and need an AI
operator an auditor can sign off on, that's what this family is for — see
[docs/compliance-ready.md](../docs/compliance-ready.md).

## Capabilities Overview

### What This Skill Does

| Category | Tools | Count |
|----------|-------|:-----:|
| **VM Lifecycle** | power on/off, TTL auto-delete, clean slate | 6 |
| **Deployment** | OVA, template, linked clone, batch clone/deploy | 8 |
| **Guest Ops** | exec commands, upload/download files, provision | 5 |
| **Plan/Apply** | multi-step planning with rollback | 4 |
| **Cluster** | create, delete, HA/DRS config, add/remove hosts | 6 |
| **Datastore** | browse files, scan for images | 2 |
| **Network** | dvSwitch portgroup list/create, host VMkernel list/add/remove, DF-bit MTU-path ping | 6 |

### CLI vs MCP: Which Mode to Use

| Scenario | Recommended | Why |
|----------|:-----------:|-----|
| **Local/small models** (Ollama, Qwen <32B) | **CLI** | ~2K tokens context vs ~10K for MCP; small models struggle with many tool schemas |
| **Token-sensitive workflows** | **CLI** | SKILL.md + Bash tool = minimal overhead |
| **Cloud models** (Claude, GPT-4o) | Either | Both work; MCP gives structured JSON I/O |
| **Automated pipelines / Agent chaining** | **MCP** | Type-safe parameters, structured output, no shell parsing |
| **Monitoring / storage / K8s** | Companion skills | See [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor), [vmware-storage](https://github.com/vmware-skills/VMware-Storage), [vmware-vks](https://github.com/vmware-skills/VMware-VKS) |

> **Rule of thumb**: Use CLI for cost efficiency and small models. Use MCP for structured automation with large models.

### Architecture

```
User (Natural Language)
  ↓
AI CLI Tool (Claude Code / Gemini / Codex / Aider / Continue / Trae / Kimi)
  ↓ reads SKILL.md / AGENTS.md / rules
  ↓
vmware-aiops CLI
  ↓ pyVmomi (vSphere SOAP API)
  ↓
vCenter Server ──→ ESXi Cluster ──→ VM
    or
ESXi Standalone Host ──→ VM
```

### Version Compatibility

| vSphere / VCF Version | Support | Notes |
|----------------|---------|-------|
| VCF 9.1 / vSphere 9.1 | ✅ Full | Released 2026-05-12. pyVmomi `<10.0` resolves and connects via SOAP; new REST-only features (`PATCH /deployment/size`, IPv6-only GOSC) not yet wrapped — see [VCF Python SDK](https://developer.broadcom.com/sdks) for those. |
| VCF 9.0 / vSphere 9.0 | ✅ Full | pyVmomi 8.0.3+ connects against vSphere 9 SOAP API. From VCF 9, pyVmomi is also bundled inside the unified VCF Python SDK. |
| 8.0 / 8.0U1-U3 | ✅ Full | `CreateSnapshot_Task` deprecated → use `CreateSnapshotEx_Task` |
| 7.0 / 7.0U1-U3 | ✅ Full | All APIs supported |
| 6.7 | ✅ Compatible | Backward-compatible, tested |
| 6.5 | ✅ Compatible | Backward-compatible, tested |

> pyVmomi auto-negotiates the API version during SOAP handshake — no manual configuration needed. The same codebase manages 7.0 / 8.0 / 9.0 / 9.1 environments seamlessly.

#### Official Broadcom References

- **SDKs**: <https://developer.broadcom.com/sdks> — VCF Python SDK (recommended for VCF 9+, bundles pyVmomi + vSAN SDK), vSphere Automation SDK for Python (REST APIs)
- **REST APIs**: <https://developer.broadcom.com/xapis> — vSphere Automation API, VCF API, SDDC Manager API
- **CLI Tools**: <https://developer.broadcom.com/tools> — PowerCLI 9.1, ESXCLI, OVF Tool

---

## Common Workflows

### Deploy a Lab Environment

1. Browse datastore for OVA images → `vmware-aiops datastore browse <ds> --pattern "*.ova"`
2. Deploy VM from OVA → `vmware-aiops deploy ova ./image.ova --name lab-vm --datastore ds1`
3. Install software inside VM → `vmware-aiops vm guest-exec lab-vm --cmd /bin/bash --args "-c 'apt-get install -y nginx'" --user root`
4. Create baseline snapshot → `vmware-aiops vm snapshot-create lab-vm --name baseline`
5. Set TTL for auto-cleanup → `vmware-aiops vm set-ttl lab-vm --minutes 480`

### Batch Clone for Testing

1. Create plan: `vm_create_plan` with multiple clone + reconfigure steps
2. Review plan with user (shows affected VMs, irreversible warnings)
3. Apply: `vm_apply_plan` executes sequentially, stops on failure
4. If failed: `vm_rollback_plan` reverses executed steps
5. Set TTL on all clones for auto-cleanup

### Migrate VM to Another Host

1. Check VM info via `vmware-monitor` → verify power state and current host
2. Migrate: `vmware-aiops vm migrate my-vm --to-host esxi-02`
3. Verify migration completed

---

## VM Lifecycle

| Operation | Command | Confirmation | vCenter | ESXi |
|-----------|---------|:------------:|:-------:|:----:|
| Power On | `vm power-on <name>` | — | ✅ | ✅ |
| Graceful Shutdown | `vm power-off <name>` | Double | ✅ | ✅ |
| Force Power Off | `vm power-off <name> --force` | Double | ✅ | ✅ |
| Reset | `vm reset <name>` | — | ✅ | ✅ |
| Suspend | `vm suspend <name>` | — | ✅ | ✅ |
| Create VM | `vm create <name> --cpu --memory --disk` | — | ✅ | ✅ |
| Delete VM | `vm delete <name>` | Double | ✅ | ✅ |
| Reconfigure | `vm reconfigure <name> --cpu --memory` | Double | ✅ | ✅ |
| Create Snapshot | `vm snapshot-create <name> --name <snap>` | — | ✅ | ✅ |
| List Snapshots | `vm snapshot-list <name>` | — | ✅ | ✅ |
| Revert Snapshot | `vm snapshot-revert <name> --name <snap>` | — | ✅ | ✅ |
| Delete Snapshot | `vm snapshot-delete <name> --name <snap> [--no-wait]` | — | ✅ | ✅ |
| Task Status | `vm task-status <task-id>` | — | ✅ | ✅ |
| Clone VM | `vm clone <name> --new-name <new>` | — | ✅ | ✅ |
| vMotion | `vm migrate <name> --to-host <host>` | — | ✅ | ❌ |
| **Set TTL** | `vm set-ttl <name> --minutes <n>` | — | ✅ | ✅ |
| **Cancel TTL** | `vm cancel-ttl <name>` | — | ✅ | ✅ |
| **List TTLs** | `vm list-ttl` | — | ✅ | ✅ |
| **Clean Slate** | `vm clean-slate <name> [--snapshot baseline]` | Double | ✅ | ✅ |
| **Guest Exec** | `vm guest-exec <name> --cmd /bin/bash --args "..."` | — | ✅ | ✅ |
| **Guest Exec (with output)** | `vm guest-exec-output <name> --cmd "df -h"` | — | ✅ | ✅ |
| **Guest Upload** | `vm guest-upload <name> --local f.sh --guest /tmp/f.sh` | — | ✅ | ✅ |
| **Guest Download** | `vm guest-download <name> --guest /var/log/syslog --local ./syslog` | — | ✅ | ✅ |

> Guest Operations require VMware Tools running inside the guest OS. `guest-exec-output` auto-detects Linux/Windows shell and captures stdout/stderr.

### Plan → Apply (Multi-step Operations)

For complex operations involving 2+ steps or 2+ VMs, use the plan/apply workflow instead of executing individually:

| Step | What Happens |
|------|-------------|
| 1. **Create Plan** | AI calls `vm_create_plan` — validates actions, checks targets in vSphere, generates plan with rollback info |
| 2. **Review** | AI shows plan to user: steps, affected VMs, irreversible warnings |
| 3. **Apply** | `vm_apply_plan` executes sequentially; stops on failure |
| 4. **Rollback** (if failed) | Asks user whether to rollback, then `vm_rollback_plan` reverses executed steps (irreversible steps skipped) |

Plans stored in `~/.vmware-aiops/plans/`, auto-deleted on success, auto-cleaned after 24h.

## VM Deployment & Provisioning

| Operation | Command | Speed | vCenter | ESXi |
|-----------|---------|:-----:|:-------:|:----:|
| Deploy from OVA | `deploy ova <path> --name <vm>` | Minutes | ✅ | ✅ |
| Deploy from Template | `deploy template <tmpl> --name <vm>` | Minutes | ✅ | ✅ |
| Linked Clone | `deploy linked-clone --source <vm> --snapshot <snap> --name <new>` | Seconds | ✅ | ✅ |
| Attach ISO | `deploy iso <vm> --iso "[ds] path/to.iso"` | Instant | ✅ | ✅ |
| Convert to Template | `deploy mark-template <vm>` | Instant | ✅ | ✅ |
| Batch Clone | `deploy batch-clone --source <vm> --count <n>` | Minutes | ✅ | ✅ |
| Batch Deploy (YAML) | `deploy batch spec.yaml` | Auto | ✅ | ✅ |

## Cluster Management

| Operation | Command | Confirmation | vCenter | ESXi |
|-----------|---------|:------------:|:-------:|:----:|
| Cluster Info | `cluster info <name>` | — | ✅ | ❌ |
| Create Cluster | `cluster create <name> [--ha] [--drs]` | — | ✅ | ❌ |
| Delete Cluster | `cluster delete <name>` | Double | ✅ | ❌ |
| Add Host | `cluster add-host <cluster> --host <host>` | Double | ✅ | ❌ |
| Remove Host | `cluster remove-host <cluster> --host <host>` | Double | ✅ | ❌ |
| Configure HA/DRS | `cluster configure <name> [--ha/--no-ha] [--drs/--no-drs]` | Double | ✅ | ❌ |

> `remove-host` requires the host to be in **maintenance mode** first; the host is moved out of the cluster into the datacenter's host folder as a standalone host.

## Alarm Management

| Operation | Command | Confirmation | vCenter | ESXi |
|-----------|---------|:------------:|:-------:|:----:|
| List Triggered Alarms | `alarm list [--target <t>]` | — | ✅ | ❌ |
| Acknowledge Alarm | `alarm acknowledge <entity> <alarm>` | — | ✅ | ❌ |
| Clear (Reset) Alarms | `alarm reset <entity> <alarm>` | Double | ✅ | ❌ |

> **Blast radius**: vSphere has no per-alarm clear API. `alarm reset` uses `AlarmManager.ClearTriggeredAlarms`, which clears **all** triggered alarms matching the named alarm's entity type (host/VM/all) and current status (red/yellow) — not just the named one. The named alarm is looked up first (typos fail fast), and the output's `scope` field reports exactly what was cleared. Cleared alarms re-trigger automatically if their underlying condition persists.

## Datastore Browser

| Feature | vCenter | ESXi | Details |
|---------|:-------:|:----:|---------|
| Browse Files | ✅ | ✅ | List files/folders in any datastore path |
| Scan Images | ✅ | ✅ | Discover ISO, OVA, OVF, VMDK across all datastores |

## Scheduled Scanning & Notifications

| Feature | Details |
|---------|---------|
| Daemon | APScheduler-based, configurable interval (default 15 min) |
| Multi-target Scan | Sequentially scan all configured vCenter/ESXi targets |
| Scan Content | Alarms + Events + Host logs (hostd, vmkernel, vpxd) |
| Log Analysis | Regex pattern matching: error, fail, critical, panic, timeout, corrupt |
| Structured Log | JSONL output to `~/.vmware-aiops/scan.log` |
| Webhook | Slack, Discord, or any HTTP endpoint |
| Daemon Management | `daemon start/stop/status`, PID file, graceful shutdown |

## Safety Features

| Feature | Details |
|---------|---------|
| **Dry-Run Mode** | `--dry-run` on any destructive command prints exact API calls without executing |
| **Plan → Confirm → Execute → Log** | Structured workflow: show current state, confirm changes, execute, audit log |
| **Double Confirmation** | All destructive ops (power-off, delete, reconfigure, snapshot-revert/delete, clone, migrate) require 2 sequential confirmations — no bypass flags |
| **Rejection Logging** | Declined confirmations are recorded in the audit trail |
| **Audit Trail** | All operations logged to `~/.vmware-aiops/audit.log` (JSONL) with before/after state |
| **Input Validation** | VM name, CPU (1-128), memory (128-1048576 MB), disk (1-65536 GB) validated |
| **Password Protection** | `.env` file loading with permission check; never in shell history |
| **SSL Self-signed Support** | `disableSslCertValidation` — only for ESXi with self-signed certs in isolated labs; production should use CA-signed certificates |
| **Prompt Injection Protection** | vSphere event messages and host logs are truncated, stripped of control characters, and wrapped in boundary markers before output |
| **Webhook Data Scope** | Sends notifications to user-configured URLs only — no third-party services by default |
| **Task Waiting** | All async operations wait for completion and report result |
| **State Validation** | Pre-operation checks (VM exists, power state correct) |

### vCenter vs ESXi Comparison

| Capability | vCenter | ESXi Standalone |
|------------|:-------:|:----:|
| vMotion migration | ✅ | ❌ |
| Cross-host clone | ✅ | ❌ |
| Cluster management | ✅ | ❌ |
| All VM lifecycle ops | ✅ | ✅ |
| OVA/Template/Linked Clone deploy | ✅ | ✅ |
| Datastore browsing & image scan | ✅ | ✅ |
| Snapshots | ✅ | ✅ |
| Guest operations | ✅ | ✅ |

> Inventory, alarms, events, sensors, host services, and scanning are now in [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor).

---

## Troubleshooting

### "VM not found" error
VM names are case-sensitive in vSphere. Use exact name from `vmware-monitor inventory vms`.

### Guest exec returns empty output
Use `vm_guest_exec_output` instead of `vm_guest_exec` — it auto-captures stdout/stderr. Basic `vm_guest_exec` only returns exit code.

### Deploy OVA times out
Large OVA files (>10GB) may exceed the default 120s timeout. The upload happens via HTTP NFC lease — ensure network between the machine running vmware-aiops and ESXi is stable.

### Plan apply fails mid-way
Run `vmware-aiops plan list` to see failed plan status. Ask user if they want to rollback with `vm_rollback_plan`. Irreversible steps (delete_vm) are skipped during rollback.

### Connection refused / SSL error
1. Verify target is reachable: `vmware-aiops doctor`
2. For self-signed certs: set `disableSslCertValidation: true` in config.yaml (lab environments only)

---

## Supported AI Platforms

| Platform | Status | Config File | AI Model |
|----------|--------|-------------|----------|
| **Claude Code** | ✅ Native Skill | `skills/vmware-aiops/SKILL.md` | Anthropic Claude |
| **Gemini CLI** | ✅ Context file + MCP | `skills/vmware-aiops/SKILL.md` | Google Gemini |
| **OpenAI Codex CLI** | ✅ Skill + AGENTS.md | `skills/vmware-aiops/SKILL.md` | OpenAI GPT |
| **Aider** | ✅ Conventions | `skills/vmware-aiops/SKILL.md` | Any (cloud + local) |
| **Continue CLI** | ✅ Rules | `skills/vmware-aiops/SKILL.md` | Any (cloud + local) |
| **Trae IDE** | ✅ Rules | `skills/vmware-aiops/SKILL.md` | Claude/DeepSeek/GPT-4o/Doubao |
| **Kimi Code CLI** | ✅ Skill | `skills/vmware-aiops/SKILL.md` | Moonshot Kimi |
| **MCP Server** | ✅ MCP Protocol | `vmware_aiops/mcp_server/` | Any MCP client |
| **Python CLI** | ✅ Standalone | N/A | N/A |

### Platform Comparison

| Feature | Claude Code | Gemini CLI | Codex CLI | Aider | Continue | Trae IDE | Kimi CLI |
|---------|-------------|------------|-----------|-------|----------|----------|----------|
| Cloud AI | Anthropic | Google | OpenAI | Any | Any | Multi | Moonshot |
| Local models | — | — | — | Ollama | Ollama | — | — |
| Skill system | SKILL.md | Context file | SKILL.md | — | Rules | Rules | SKILL.md |
| MCP support | Native | Native | Via Skills | Third-party | Native | — | — |
| Free tier | — | 60 req/min | — | Self-hosted | Self-hosted | — | — |

### MCP Server Integrations

The vmware-aiops MCP server works with **any MCP-compatible agent or tool**. Ready-to-use configuration templates are in [`examples/mcp-configs/`](examples/mcp-configs/).

| Agent / Tool | Local Model Support | Config Template | Integration Guide |
|-------------|:-------------------:|-----------------|-------------------|
| **[Xiaoguai (小怪)](https://github.com/xiaoguai-agent/xiaoguai)** | ✅ Self-hosted, any LLM | [MCP setup](https://github.com/xiaoguai-agent/xiaoguai/blob/main/docs/book/src/api/mcp.md) | [Guide](https://github.com/xiaoguai-agent/xiaoguai) |
| **[Goose](https://github.com/block/goose)** | ✅ Ollama, LM Studio | [`goose.json`](examples/mcp-configs/goose.json) | [Guide](docs/integrations/goose.md) |
| **[LocalCowork](https://github.com/Liquid4All/localcowork)** | ✅ Fully offline | [`localcowork.json`](examples/mcp-configs/localcowork.json) | [Guide](docs/integrations/localcowork.md) |
| **[mcp-agent](https://github.com/lastmile-ai/mcp-agent)** | ✅ Ollama, vLLM | [`mcp-agent.yaml`](examples/mcp-configs/mcp-agent.yaml) | [Guide](docs/integrations/mcp-agent.md) |
| **VS Code Copilot** | — | [`vscode-copilot.json`](examples/mcp-configs/vscode-copilot.json) | [Guide](docs/integrations/vscode-copilot.md) |
| **[Cursor](https://www.cursor.com)** | — | [`cursor.json`](examples/mcp-configs/cursor.json) | [Guide](docs/integrations/cursor.md) |
| **Continue** | ✅ Ollama | [`continue.yaml`](examples/mcp-configs/continue.yaml) | [Guide](docs/integrations/continue.md) |
| **Claude Code** | — | [`claude-code.json`](examples/mcp-configs/claude-code.json) | — |

> **[Xiaoguai (小怪)](https://github.com/xiaoguai-agent/xiaoguai)** — a self-hostable, audit-first agent platform (Rust, single binary + embedded SQLite) from the same maintainer. It runs the vmware-aiops MCP server as one of its toolboxes; being both an MCP *consumer* and an MCP *server*, its HMAC-chained audit log and human-on-the-loop approval gates line up with this skill's own audit + confirm design. See its [MCP integration guide](https://github.com/xiaoguai-agent/xiaoguai/blob/main/docs/book/src/api/mcp.md).

**Fully local operation** (no cloud API required):

```bash
# Aider + Ollama + vmware-aiops (via SKILL.md)
aider --conventions skills/vmware-aiops/SKILL.md --model ollama/qwen2.5-coder:32b

# Any MCP agent + local model + vmware-aiops MCP server
# See examples/mcp-configs/ for your agent's config format
```

---

## Installation

### Step 0: Prerequisites

```bash
# Python 3.10+ required
python3 --version

# Node.js 18+ required for Gemini CLI and Codex CLI
node --version
```

### Step 1: Clone & Install Python Backend

All platforms share the same Python backend.

```bash
git clone https://github.com/vmware-skills/VMware-AIops.git
cd VMware-AIops
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Step 2: Configure

```bash
mkdir -p ~/.vmware-aiops
cp config.example.yaml ~/.vmware-aiops/config.yaml
# Edit config.yaml with your vCenter/ESXi targets
```

Set passwords via `.env` file (recommended):
```bash
# Use the template
cp .env.example ~/.vmware-aiops/.env

# Edit and fill in your passwords, then lock permissions
chmod 600 ~/.vmware-aiops/.env
```

> **Security note**: Prefer `.env` file over command-line `export` to avoid passwords appearing in shell history. The `.env` file should have `chmod 600` (owner-only read/write).

Password environment variable naming convention:
```
VMWARE_{TARGET_NAME_UPPER}_PASSWORD
# Replace hyphens with underscores, UPPERCASE
# Example: target "home-esxi" → VMWARE_HOME_ESXI_PASSWORD
# Example: target "prod-vcenter" → VMWARE_PROD_VCENTER_PASSWORD
```

### Security Best Practices

- **NEVER** hardcode passwords in scripts or config files
- **NEVER** pass passwords as command-line arguments (visible in `ps`)
- **ALWAYS** use `~/.vmware-aiops/.env` with `chmod 600`
- **ALWAYS** configure connections via `config.yaml` — credentials are loaded from `.env` automatically
- **Config File Contents**: `config.yaml` stores target hostnames, ports, and a reference to the `.env` file. It does **not** contain passwords or tokens. All secrets are stored exclusively in `.env`
- **TLS**: Enabled by default. Disable only for ESXi hosts with self-signed certificates in isolated lab environments
- **Webhook**: Disabled by default. When enabled, sends monitoring summaries to your own configured URL only — payloads contain no credentials, IPs, or PII, only aggregated alert metadata. No data sent to third-party services
- **Least Privilege**: Use a dedicated vCenter service account with minimal permissions. For monitoring-only use cases, prefer the read-only [VMware-Monitor](https://github.com/vmware-skills/VMware-Monitor)
- **Prompt Injection Protection**: All vSphere-sourced content is truncated, stripped of control characters, and wrapped in boundary markers before output
- **Code Review**: We recommend reviewing the [source code](https://github.com/vmware-skills/VMware-AIops) and commit history before deploying in production
- **Production Safety**: For production environments, use the read-only [VMware-Monitor](https://github.com/vmware-skills/VMware-Monitor) instead. AI agents can misinterpret context and execute unintended destructive operations — real-world incidents have shown that AI-driven infrastructure tools without proper isolation can delete production databases and entire environments. VMware-Monitor eliminates this risk at the code level: no destructive functions exist in its codebase

### Step 3: Connect Your AI Tool

Choose one (or more) of the following:

---

#### Option A: Claude Code

**Method 1: Skills.sh or ClawHub (recommended)**

Either installer places the skill in Claude Code's skills directory for you:

```bash
npx skills add vmware-skills/VMware-AIops
# or
clawhub install vmware-aiops
```

**Method 2: Manual skill install**

```bash
git clone https://github.com/vmware-skills/VMware-AIops.git
cd VMware-AIops

# Copy the skill into Claude Code's personal skills directory
mkdir -p ~/.claude/skills/vmware-aiops
cp -r skills/vmware-aiops/. ~/.claude/skills/vmware-aiops/
```

For tool access (not just skill context), also register the MCP server:

```bash
claude mcp add vmware-aiops -- vmware-aiops mcp
```

Restart Claude Code, then:
```
> Show me all VMs on esxi-lab.example.com
```

**Submit to Official Marketplace**

This plugin can also be submitted to the [Anthropic official plugin directory](https://clau.de/plugin-directory-submission) for public discovery.

---

#### Option B: Gemini CLI

```bash
# Install Gemini CLI
npm install -g @google/gemini-cli

# Load the skill as project context (Gemini CLI reads GEMINI.md on startup)
cp skills/vmware-aiops/SKILL.md ./GEMINI.md
```

For tool access (not just context), register the MCP server in `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "vmware-aiops": {
      "command": "vmware-aiops",
      "args": ["mcp"],
      "env": { "VMWARE_AIOPS_CONFIG": "~/.vmware-aiops/config.yaml" }
    }
  }
}
```

Then start Gemini CLI:
```
gemini
> Show me all VMs on my ESXi host
```

---

#### Option C: OpenAI Codex CLI

```bash
# Install Codex CLI
npm i -g @openai/codex
# Or on macOS:
# brew install --cask codex

# Copy skill to Codex skills directory
mkdir -p ~/.codex/skills/vmware-aiops
cp skills/vmware-aiops/SKILL.md ~/.codex/skills/vmware-aiops/SKILL.md

# Copy AGENTS.md to project root
cp skills/vmware-aiops/SKILL.md ./AGENTS.md
```

Then start Codex CLI:
```bash
codex --enable skills
> List all VMs on my ESXi
```

---

#### Option D: Aider (supports local models)

```bash
# Install Aider
pip install aider-chat

# Install Ollama for local models (optional)
# macOS:
brew install ollama
ollama pull qwen2.5-coder:32b

# Run with cloud API
aider --conventions skills/vmware-aiops/SKILL.md

# Or with local model via Ollama
aider --conventions skills/vmware-aiops/SKILL.md \
  --model ollama/qwen2.5-coder:32b
```

---

#### Option E: Continue CLI (supports local models)

```bash
# Install Continue CLI
npm i -g @continuedev/cli

# Copy rules file
mkdir -p .continue/rules
cp skills/vmware-aiops/SKILL.md .continue/rules/vmware-aiops.md
```

Configure `~/.continue/config.yaml` for local model:
```yaml
models:
  - name: local-coder
    provider: ollama
    model: qwen2.5-coder:32b
```

Then:
```bash
cn
> Check ESXi health and alarms
```

---

#### Option F: Trae IDE

Copy the rules file to your project's `.trae/rules/` directory:

```bash
mkdir -p .trae/rules
cp skills/vmware-aiops/SKILL.md .trae/rules/project_rules.md
```

Trae IDE's Builder Mode reads `.trae/rules/` Markdown files at startup.

> **Note**: You can also install Claude Code extension in Trae IDE and use `.claude/skills/` format directly.

---

#### Option G: Kimi Code CLI

```bash
# Copy skill file to Kimi skills directory
mkdir -p ~/.kimi/skills/vmware-aiops
cp skills/vmware-aiops/SKILL.md ~/.kimi/skills/vmware-aiops/SKILL.md
```

---

#### Option H: MCP Server (Glama / Claude Desktop)

The MCP server exposes VMware operations as tools via the [Model Context Protocol](https://modelcontextprotocol.io). Works with any MCP-compatible client (Claude Desktop, Cursor, etc.).

**After `uv tool install vmware-aiops`, start the MCP server with one command** (v1.5.15+):

```bash
# Recommended — single command, no network re-resolve
vmware-aiops mcp

# With a custom config path
VMWARE_AIOPS_CONFIG=/path/to/config.yaml vmware-aiops mcp
```

**Claude Desktop config** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "vmware-aiops": {
      "command": "vmware-aiops",
      "args": ["mcp"],
      "env": {
        "VMWARE_AIOPS_CONFIG": "/path/to/config.yaml"
      }
    }
  }
}
```

<details>
<summary>Alternative: uvx (no install) or legacy entry point</summary>

```bash
# Run without installing (requires PyPI access each launch)
uvx --from vmware-aiops vmware-aiops mcp

# Legacy entry point (still works, kept for backward compatibility)
vmware-aiops-mcp
```

> **Behind a corporate TLS proxy?** uvx may fail with `invalid peer certificate: UnknownIssuer`.
> Use the recommended `vmware-aiops mcp` form above (no network needed), or set `UV_NATIVE_TLS=true`.

</details>


---

#### Option I: Standalone CLI (no AI)

```bash
# Already installed in Step 1
source .venv/bin/activate

vmware-aiops vm power-on my-vm --target home-esxi
vmware-aiops deploy ova ./ubuntu.ova --name my-vm --target home-esxi
vmware-aiops datastore browse datastore1 --target home-esxi
```

---

## Update / Upgrade

Already installed? Re-run the install command for your channel to get the latest version:

| Install Channel | Update Command |
|----------------|----------------|
| ClawHub | `clawhub install vmware-aiops` |
| Skills.sh | `npx skills add vmware-skills/VMware-AIops` |
| Git clone | `cd VMware-AIops && git pull origin main && uv pip install -e .` |
| uv | `uv tool install vmware-aiops --force` |

Check your current version: `vmware-aiops --version`

---

## Chinese Cloud Models

For users in China who prefer domestic cloud APIs or have limited access to overseas services.

### DeepSeek

Cost-effective, strong coding capability.

```bash
# Set DeepSeek API key (get from https://platform.deepseek.com)
export DEEPSEEK_API_KEY="your-key"

# Run with Aider
aider --conventions skills/vmware-aiops/SKILL.md \
  --model deepseek/deepseek-coder
```

Persistent config `~/.aider.conf.yml`:
```yaml
model: deepseek/deepseek-coder
conventions: skills/vmware-aiops/SKILL.md
```

### Qwen (Alibaba Cloud)

Alibaba Cloud's coding model, free tier available.

```bash
# Set DashScope API key (get from https://dashscope.console.aliyun.com)
export DASHSCOPE_API_KEY="your-key"

aider --conventions skills/vmware-aiops/SKILL.md \
  --model qwen/qwen-coder-plus
```

Or via OpenAI-compatible endpoint:
```bash
export OPENAI_API_BASE="https://dashscope.aliyuncs.com/compatible-mode/v1"
export OPENAI_API_KEY="your-dashscope-key"

aider --conventions skills/vmware-aiops/SKILL.md \
  --model qwen-coder-plus-latest
```

### Doubao (ByteDance)

```bash
export OPENAI_API_BASE="https://ark.cn-beijing.volces.com/api/v3"
export OPENAI_API_KEY="your-ark-key"

aider --conventions skills/vmware-aiops/SKILL.md \
  --model your-doubao-endpoint-id
```

### With Continue CLI

Configure `~/.continue/config.yaml`:

```yaml
# DeepSeek
models:
  - name: deepseek-coder
    provider: openai-compatible
    apiBase: https://api.deepseek.com/v1
    apiKey: your-deepseek-key
    model: deepseek-coder

# Qwen
models:
  - name: qwen-coder
    provider: openai-compatible
    apiBase: https://dashscope.aliyuncs.com/compatible-mode/v1
    apiKey: your-dashscope-key
    model: qwen-coder-plus-latest
```

---

## Local Models (Aider + Ollama)

For fully offline operation — no cloud API, no internet, full privacy.

**Aider + Ollama + local Qwen/DeepSeek** is ideal for air-gapped environments.

### Step 1: Install Ollama

```bash
# macOS
brew install ollama

# Linux — download from https://ollama.com/download and install manually
# See https://github.com/ollama/ollama for platform-specific instructions
```

### Step 2: Pull a model

| Model | Command | Size | Note |
|-------|---------|------|------|
| **Qwen 2.5 Coder 32B** | `ollama pull qwen2.5-coder:32b` | ~20GB | Best local coding model |
| **Qwen 2.5 Coder 7B** | `ollama pull qwen2.5-coder:7b` | ~4.5GB | Low-memory option |
| **DeepSeek Coder V2** | `ollama pull deepseek-coder-v2` | ~8.9GB | Strong reasoning |
| **CodeLlama 34B** | `ollama pull codellama:34b` | ~19GB | Meta coding model |

> **Hardware**: 32B → ~20GB VRAM (or 32GB RAM for CPU). 7B → 8GB RAM.

### Step 3: Run with Aider

```bash
pip install aider-chat
ollama serve

# Aider + local Qwen (recommended)
aider --conventions skills/vmware-aiops/SKILL.md \
  --model ollama/qwen2.5-coder:32b

# Aider + local DeepSeek
aider --conventions skills/vmware-aiops/SKILL.md \
  --model ollama/deepseek-coder-v2

# Low-memory option
aider --conventions skills/vmware-aiops/SKILL.md \
  --model ollama/qwen2.5-coder:7b
```

Persistent config `~/.aider.conf.yml`:
```yaml
model: ollama/qwen2.5-coder:32b
conventions: skills/vmware-aiops/SKILL.md
```

### Local Architecture

```
User → Aider CLI → Ollama (localhost:11434) → Qwen / DeepSeek local model
  │                                                    ↓
  │                                          reads AGENTS.md instructions
  │                                                    ↓
  └──────────────────────────────→ vmware-aiops CLI ──→ ESXi / vCenter
```

> **Tip**: Local models are fully offline — perfect for air-gapped environments or strict data compliance.

---

## CLI Reference

```bash
# Diagnostics
vmware-aiops doctor                   # Check environment, config, connectivity
vmware-aiops doctor --skip-auth       # Skip vSphere auth check (faster)

# MCP Config Generator
vmware-aiops mcp-config generate --agent goose        # Generate config for Goose
vmware-aiops mcp-config generate --agent claude-code  # Generate config for Claude Code
vmware-aiops mcp-config list                          # List all supported agents

# VM operations
vmware-aiops vm power-on my-vm                                 # Power on
vmware-aiops vm power-off my-vm                                # Graceful shutdown (2x confirm)
vmware-aiops vm power-off my-vm --force                        # Force power off (2x confirm)
vmware-aiops vm create my-new-vm --cpu 4 --memory 8192 --disk 100  # Create VM
vmware-aiops vm delete my-vm --confirm                         # Delete VM (2x confirm)
vmware-aiops vm reconfigure my-vm --cpu 4 --memory 8192        # Reconfigure (2x confirm)
vmware-aiops vm snapshot-create my-vm --name "before-upgrade"  # Create snapshot
vmware-aiops vm snapshot-list my-vm                            # List snapshots
vmware-aiops vm snapshot-revert my-vm --name "before-upgrade"  # Revert snapshot
vmware-aiops vm snapshot-delete my-vm --name "before-upgrade"  # Delete snapshot (waits ≤30 min for consolidation)
vmware-aiops vm snapshot-delete my-vm --name "old-big" --no-wait  # Fire async, return a task id
vmware-aiops vm task-status task-1234                          # Poll an async task by id
vmware-aiops vm clone my-vm --new-name my-vm-clone             # Clone VM
vmware-aiops vm migrate my-vm --to-host esxi-02                # vMotion
vmware-aiops vm set-ttl my-vm --minutes 60                     # Auto-delete in 60 min
vmware-aiops vm cancel-ttl my-vm                               # Cancel TTL
vmware-aiops vm list-ttl                                       # Show all TTLs
vmware-aiops vm clean-slate my-vm --snapshot baseline          # Revert to baseline (2x confirm)

# Guest Operations (requires VMware Tools in guest)
vmware-aiops vm guest-exec my-vm --cmd /bin/bash --args "-c 'whoami'" --user root
vmware-aiops vm guest-upload my-vm --local ./script.sh --guest /tmp/script.sh --user root
vmware-aiops vm guest-download my-vm --guest /var/log/syslog --local ./syslog.txt --user root

# Plan → Apply (multi-step operations)
vmware-aiops plan list                                        # List pending/failed plans

# Deploy
vmware-aiops deploy ova ./ubuntu.ova --name my-vm --datastore ds1      # Deploy from OVA
vmware-aiops deploy template golden-ubuntu --name new-vm               # Deploy from template
vmware-aiops deploy linked-clone --source base-vm --snapshot clean --name test-vm  # Linked clone (seconds)
vmware-aiops deploy iso my-vm --iso "[datastore1] iso/ubuntu-22.04.iso"  # Attach ISO
vmware-aiops deploy mark-template golden-vm                            # Convert VM to template
vmware-aiops deploy batch-clone --source base-vm --count 5 --prefix lab  # Batch clone
vmware-aiops deploy batch deploy.yaml                                  # Batch deploy from YAML spec

# Cluster
vmware-aiops cluster info my-cluster                                   # Cluster details (HA/DRS status)
vmware-aiops cluster create my-cluster --ha --drs                      # Create cluster with HA+DRS
vmware-aiops cluster delete my-cluster                                 # Delete cluster (2x confirm)
vmware-aiops cluster add-host my-cluster --host esxi-03                # Add host to cluster (2x confirm)
vmware-aiops cluster remove-host my-cluster --host esxi-03             # Remove host (2x confirm)
vmware-aiops cluster configure my-cluster --ha --drs                   # Configure HA/DRS (2x confirm)

# Alarm management
vmware-aiops alarm list                                                # List triggered alarms
vmware-aiops alarm acknowledge esxi-01 "Host memory usage"             # Acknowledge alarm
vmware-aiops alarm reset esxi-01 "Host memory usage"                   # Clear alarms (2x confirm; clears ALL matching entity type + status)

# Datastore (browse and scan only — iSCSI/vSAN moved to vmware-storage)
vmware-aiops datastore browse datastore1 --path "iso/"                 # Browse datastore
vmware-aiops datastore scan-images --target home-esxi                  # Scan all datastores for images

# Scan
vmware-aiops scan now              # One-time scan

# Daemon
vmware-aiops daemon start          # Start scanner
vmware-aiops daemon status         # Check status
vmware-aiops daemon stop           # Stop daemon

# Companion skills for other operations:
#   vmware-monitor: inventory, alarms, events, sensors
#   vmware-storage: datastores, iSCSI, vSAN
#   vmware-vks:     Tanzu/TKC cluster lifecycle
```

---

## Configuration

See `config.example.yaml` for all options.

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| targets | name | — | Friendly name |
| targets | host | — | vCenter/ESXi hostname or IP |
| targets | type | vcenter | `vcenter` or `esxi` |
| targets | port | 443 | Connection port |
| targets | verify_ssl | false | SSL certificate verification |
| scanner | interval_minutes | 15 | Scan frequency |
| scanner | severity_threshold | warning | Min severity: critical/warning/info |
| scanner | lookback_hours | 1 | How far back to scan |
| scanner | log_types | [vpxd, hostd, vmkernel] | Log sources |
| notify | log_file | ~/.vmware-aiops/scan.log | JSONL log output |
| notify | webhook_url | — | Webhook endpoint (Slack, Discord, etc.) |

---

## Project Structure

```
VMware-AIops/
├── skills/                        # Skills index (npx skills add)
│   └── vmware-aiops/
│       ├── SKILL.md               # Slimmed-down skill (progressive disclosure)
│       └── references/            # Detailed docs loaded on-demand
│           ├── capabilities.md    # Full capabilities tables
│           ├── cli-reference.md   # Complete CLI reference
│           └── setup-guide.md     # Install, security, AI platforms
├── vmware_aiops/                  # Python backend
│   ├── config.py                  # YAML + .env config
│   ├── connection.py              # Multi-target pyVmomi
│   ├── cli/                       # Typer CLI (double confirm)
│   ├── ops/                       # Operations
│   │   ├── inventory.py           # VMs, hosts, datastores, clusters
│   │   ├── health.py              # Alarms, events, sensors
│   │   ├── vm_lifecycle.py        # VM CRUD, snapshots, clone, migrate
│   │   ├── vm_deploy.py           # OVA, template, linked clone, batch deploy
│   │   └── datastore_browser.py   # Datastore browsing, image discovery
│   ├── scanner/                   # Log scanning daemon
│   ├── notify/                    # Notifications (JSONL + webhook)
│   └── mcp_server/                # MCP server wrapper
│       ├── server.py              # FastMCP server with tools
│       └── __main__.py
├── examples/mcp-configs/          # MCP client config templates
├── tests/                         # Test suite
├── smithery.yaml                  # Smithery marketplace config
├── RELEASE_NOTES.md
├── config.example.yaml
└── pyproject.toml
```

## API Coverage

Built on **pyVmomi** (vSphere Web Services API / SOAP).

| API Object | Usage |
|------------|-------|
| `vim.VirtualMachine` | VM lifecycle, snapshots, clone, migrate |
| `vim.HostSystem` | ESXi host info, sensors, services |
| `vim.Datastore` | Storage capacity, type, accessibility |
| `vim.host.DatastoreBrowser` | File browsing, image discovery (ISO/OVA/VMDK) |
| `vim.OvfManager` | OVA import and deployment |
| `vim.ClusterComputeResource` | Cluster, DRS, HA |
| `vim.Network` | Network listing |
| `vim.alarm.AlarmManager` | Active alarm monitoring |
| `vim.event.EventManager` | Event/log queries |

## Related Projects

| Skill | Scope | Tools | Install |
|-------|-------|:-----:|---------|
| **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** | VM lifecycle, deployment, guest ops, cluster, datastore browse, triage | 49 | `uv tool install vmware-aiops` |
| **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | Read-only monitoring, alarms, events, investigation bundles | 27 | `uv tool install vmware-monitor` |
| **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | Datastores, iSCSI, vSAN | 11 | `uv tool install vmware-storage` |
| **[vmware-vks](https://github.com/vmware-skills/VMware-VKS)** | Tanzu Namespaces, TKC cluster lifecycle | 20 | `uv tool install vmware-vks` |
| **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX networking: segments, gateways, NAT, routing, IPAM | 33 | `uv tool install vmware-nsx-mgmt` |
| **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW policies/rules, security groups, Traceflow, IDS/IPS | 21 | `uv tool install vmware-nsx-security` |
| **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Operations metrics, alerts, capacity, anomalies | 28 | `uv tool install vmware-aria` |
| **[vmware-avi](https://github.com/vmware-skills/VMware-AVI)** | AVI (NSX ALB) load balancing, AKO Kubernetes ops | 28 | `uv tool install vmware-avi` |
| **[vmware-harden](https://github.com/vmware-skills/VMware-Harden)** | Compliance baselines (CIS / vSphere SCG / 等保 / PCI-DSS), drift detection | 6 | `uv tool install vmware-harden` |

---

## Troubleshooting & Contributing

If you encounter any errors or issues, please send the error message, logs, or screenshots to **zhouwei008@gmail.com**. Contributions are welcome — feel free to join us in maintaining and improving this project!

## License

MIT
