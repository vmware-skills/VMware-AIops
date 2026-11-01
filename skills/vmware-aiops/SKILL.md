---
name: vmware-aiops
description: >
  Use this skill whenever the user needs to manage VMs in VMware/vSphere/ESXi — it's the entry point for all VM operations.
  Directly handles: power on/off, clone, snapshot, migrate, deploy from OVA or templates, run commands inside VMs, batch operations, cluster management, vCenter alarm acknowledgment, a one-glance cluster-health triage ("is anything on fire?"), and VM/host/datastore investigation drill-downs.
  Always use this skill for any "power on", "clone", "deploy", "migrate", "batch", "guest exec", "alarm", or VM lifecycle task, and for triage like "is anything on fire" / "what needs attention now" / "investigate this VM", when the context is explicitly VMware, vSphere, or ESXi.
  Do NOT use for general read-only queries (inventory/events/VM details — use vmware-monitor), NSX networking (use vmware-nsx), storage/iSCSI/vSAN (use vmware-storage), or Kubernetes cluster lifecycle (use vmware-vks).
  For multi-step workflows use vmware-pilot. For load balancing/AVI/AKO use vmware-avi.
installer:
  kind: uv
  package: vmware-aiops
argument-hint: "[vm-name or describe your task]"
allowed-tools:
  - Bash
metadata: {"openclaw":{"requires":{"anyBins":["vmware-aiops","uvx"]},"optional":{"env":["VMWARE_AIOPS_CONFIG","VMWARE_TARGET_PASSWORD","VMWARE_<TARGET>_USERNAME","SLACK_WEBHOOK_URL","DISCORD_WEBHOOK_URL","VMWARE_AUDIT_APPROVED_BY"],"bins":["vmware-policy"]},"homepage":"https://github.com/vmware-skills/VMware-AIops","emoji":"🖥️","os":["macos","linux"]}}
compatibility: >
  vmware-policy auto-installed as Python dependency (provides @vmware_tool decorator and audit logging). All write operations audited to ~/.vmware/audit.db.
  Credentials: Each vCenter/ESXi target requires a per-target password env var in ~/.vmware-aiops/.env following the pattern VMWARE_<TARGET_NAME_UPPER>_PASSWORD. Passwords are never logged or echoed.
  Destructive operations: All write tools require explicit parameters and pass through the @vmware_tool decorator (policy check + audit + sanitize). Every MCP write tool annotated destructive (23 of 43: power-off, delete, migrate, snapshot revert/delete, guest exec/upload/provision, cluster delete/remove-host, TTL, Clean Slate, plan apply/rollback, host-network, DRS and alarm reset) takes one confirm argument whose default returns a no-write blast-radius preview (HLD section 7); confirm=True is refused, and audited as a failure, on a blocker or an unreadable measurement, and delete_vm also requires the preview's acknowledgement echoed back and still matching; the other 20 write tools (create, clone, deploy, power-on, reconfigure) act on the first call. The enforcement boundary is the RBAC of the vCenter/ESXi account the server connects with, so run it under a dedicated least-privilege service account (a read-only role makes it read-only). Optional deny rules in ~/.vmware/rules.yaml are checked before every MCP call and remote CLI command; the shipped baseline denies nothing. CLI destructive commands additionally require double confirmation and most CLI writes support --dry-run; neither applies to MCP calls.
  Guest operations: vm_name and command are required; no implicit or background execution. The command is unbounded and runs with the guest credentials supplied — the username is required on MCP and CLI alike (no root default) and over MCP the password is a tool argument the agent sees (redacted from the audit row). The guest account is a second authorization boundary that a read-only vCenter role does not limit; pass a least-privilege guest account. upload_vm_guest_file reads any local file the server process can read.
  Webhooks: Disabled by default. When enabled, the daemon posts to user-configured URLs only: issue counts plus every critical issue and every alarm/event warning (host-log warnings and info rows are not sent), each with its entity name and the sanitized alarm, event, or ESXi log text, or a connection error — which can include host names, IPs, and user names. No credentials from the skill's config are sent. Reading host logs needs the Global.Diagnostics privilege; an unreadable log is recorded, not skipped.
  TLS verification is on by default (verify_ssl: true); set verify_ssl: false only for self-signed certs in isolated lab environments.
  Transitive dependencies: Only vmware-policy (audit/policy). No post-install scripts or background services.
---

# VMware AIops

> Self-test: [guide](https://github.com/vmware-skills/marketplace/blob/main/MODEL_FIT_SELF_TEST.md).

> **Disclaimer**: This is a community-maintained open-source project and is **not affiliated with, endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" and "vSphere" are trademarks of Broadcom. Source code is publicly auditable at [github.com/vmware-skills/VMware-AIops](https://github.com/vmware-skills/VMware-AIops) under the MIT license.

VMware family entry point — AI-powered VM lifecycle, deployment, and alarm management — 61 MCP tools.

> **Start here**: install vmware-aiops first, then add modules as needed.
> Run `vmware-aiops hub status` to see which family members are installed.
> **Family**: [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor) (inventory/health), [vmware-storage](https://github.com/vmware-skills/VMware-Storage) (iSCSI/vSAN), [vmware-vks](https://github.com/vmware-skills/VMware-VKS) (Tanzu Kubernetes), [vmware-nsx](https://github.com/vmware-skills/VMware-NSX) (NSX networking), [vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security) (DFW/firewall), [vmware-aria](https://github.com/vmware-skills/VMware-Aria) (metrics/alerts/capacity), [vmware-avi](https://github.com/vmware-skills/VMware-AVI) (AVI/ALB/AKO), [vmware-harden](https://github.com/vmware-skills/VMware-Harden) (compliance baselines).
> | [vmware-pilot](../vmware-pilot/SKILL.md) (workflow orchestration) | [vmware-policy](../vmware-policy/SKILL.md) (audit/policy)

## What This Skill Does

| Category | Tools | Count |
|----------|-------|:-----:|
| **VM Lifecycle** | power on/off, create, reconfigure, clone, migrate, delete, snapshot CRUD, TTL auto-delete, clean slate | 16 |
| **Deployment** | OVA, template, linked clone, batch clone/deploy | 8 |
| **Guest Ops** | exec commands, upload/download files, provision | 5 |
| **Plan/Apply** | multi-step planning with rollback | 4 |
| **Cluster** | create, delete, HA/DRS config, add/remove hosts, DRS VM-VM rules (list/create/delete/enable-disable) | 10 |
| **Datastore** | browse files, scan for images | 2 |
| **Network** | dvSwitch portgroup list/create, host VMkernel list/add/remove/tag-service, DF-bit MTU-path ping | 7 |
| **Alarm Management** | list alarms, acknowledge, reset | 3 |
| **Triage & Investigation** (read-only, delegates to vmware-monitor) | one-glance cluster health summary, object-centered VM/host/datastore drill-down bundles, cross-vCenter "what needs attention now?" | 5 |
| **Access** | which account is in use, and whether it holds each tool's privileges | 1 |

## Audit & Safety

Read before connecting an agent. Per-tool inventory: `references/capabilities.md`.

- **MCP gates default to a no-write preview.** 23 write tools — every destructive one — return their blast radius unless `confirm=True`, which is refused on a blocker or unreadable measurement; `delete_vm` also needs `acknowledge_blast_radius` echoing the preview. The other 20 (create, clone, deploy, power-on, reconfigure) act on the first call.
- **The enforcement boundary is vCenter/ESXi RBAC**: an agent can do whatever the configured account can. Use a dedicated, least-privilege service account scoped to what the agent may change (a read-only role makes the skill read-only). Store its password in `~/.vmware-aiops/.env` (0600) or a secret manager (`VMWARE_<TARGET>_PASSWORD`).
- **CLI only**: destructive commands require double confirmation; most CLI writes take `--dry-run`. Neither applies to MCP.
- **Policy**: deny rules and a maintenance window in `~/.vmware/rules.yaml` are checked before every MCP and remote CLI call (e.g. deny writes to `environment: production` targets). The shipped baseline denies nothing. An in-process guardrail, not a substitute for RBAC.
- **Audit**: every MCP call is recorded in `~/.vmware/audit.db`, credentials redacted (`vmware-audit log --last 20`). Best-effort: a failed audit write warns, never blocks.
- **Guest ops** run any command or file write the guest account allows — a read-only vCenter role does not limit this. `username` is required (no default account) and over MCP the password is a tool argument the agent sees; pass a minimal guest account. `upload_vm_guest_file` reads any local file the server can read.

## Quick Install

```bash
uv tool install vmware-aiops==2.4.2
vmware-aiops doctor
vmware-aiops hub status   # see which family members are installed
```

## VMware Family — Install What You Need

vmware-aiops is the entry point. Add modules for additional capabilities:

| Module | Install | Adds |
|--------|---------|------|
| **vmware-monitor** | `uv tool install vmware-monitor` | Read-only inventory, alarms, events |
| **vmware-storage** | `uv tool install vmware-storage` | iSCSI, vSAN, datastore management |
| **vmware-vks** | `uv tool install vmware-vks` | Tanzu Kubernetes (vSphere 8.x+) |
| **vmware-nsx** | `uv tool install vmware-nsx-mgmt` | NSX networking: segments, gateways, NAT |
| **vmware-nsx-security** | `uv tool install vmware-nsx-security` | DFW microsegmentation, security groups |
| **vmware-aria** | `uv tool install vmware-aria` | Aria Ops metrics, alerts, capacity |
| **vmware-avi** | `uv tool install vmware-avi` | AVI load balancer, ALB, AKO, Ingress |

> Each module stays independent — small tool count keeps local models (Ollama, Qwen) accurate.

## When to Use This Skill

- Power on/off, create, delete, snapshot, clone, or migrate VMs
- Deploy VMs from OVA, templates, linked clones, or batch specs
- Run commands or transfer files inside a VM (Guest Operations)
- Create/configure clusters (HA/DRS)
- Browse datastores for deployable images
- Plan and execute multi-step operations with rollback
- List, acknowledge, and clear vCenter triggered alarms (clear matches by entity type + status — see MCP Tools section)

**Use companion skills for**:
- Inventory, health, alarms, VM info → `vmware-monitor`
- iSCSI, vSAN, datastore management → `vmware-storage`
- Tanzu Kubernetes (Supervisor, Namespace, TKC) → `vmware-vks`
- Load balancing, AVI/ALB, AKO, Ingress → `vmware-avi`

## Related Skills — Skill Routing

| User Intent | Recommended Skill |
|-------------|------------------|
| Read-only monitoring, zero risk | **vmware-monitor** (`uv tool install vmware-monitor`) |
| Storage: iSCSI, vSAN, datastores | **vmware-storage** (`uv tool install vmware-storage`) |
| VM lifecycle, deployment, guest ops | **vmware-aiops** ← this skill |
| Tanzu Kubernetes (vSphere 8.x+) | **vmware-vks** (`uv tool install vmware-vks`) |
| NSX networking: segments, gateways, NAT | **vmware-nsx** (`uv tool install vmware-nsx-mgmt`) |
| NSX security: DFW rules, security groups | **vmware-nsx-security** (`uv tool install vmware-nsx-security`) |
| Aria Ops: metrics, alerts, capacity | **vmware-aria** (`uv tool install vmware-aria`) |
| Multi-step workflows with approval | **vmware-pilot** |
| Compliance baselines (CIS / 等保 / PCI-DSS), drift detection, LLM remediation advisor | **vmware-harden** (`uv tool install vmware-harden`) |
| Load balancer, AVI, ALB, AKO, Ingress | **vmware-avi** (`uv tool install vmware-avi`) |
| Audit log query | **vmware-policy** (`vmware-audit` CLI) |

## Common Workflows

> **Diagnostic investigations**: Before remediating any "why is X slow / failing / down" issue, follow [`references/investigation-protocol.md`](references/investigation-protocol.md). It enforces four root-cause completeness criteria and a deepening loop. Only invoke L3+ write tools after the four criteria are satisfied AND the user has approved a remediation plan.

### Cluster Health Triage ("what's wrong right now?")

Start here when the ask is "is anything on fire?" before diving into a specific VM. This is a read-only rollup delegated to vmware-monitor, exposed here so triage-then-act stays in one conversation.

1. One glance --> `get_cluster_health_summary` (MCP) or `vmware-aiops summary` (CLI). Read `top_issues` first — ranked anomalies (disconnected hosts, red/yellow alarms, capacity pressure), each with a drill-down hint; the per-cluster table is context
2. Act on what it surfaces --> a `host_down` row → investigate the host; an alarm → `acknowledge_vcenter_alarm` / `reset_vcenter_alarm`; a hot VM implicated → `migrate_vm` or `reconfigure_vm` (after the investigation protocol)
3. Save/share a snapshot --> `vmware-aiops summary --html` writes an offline, timestamped HTML file (same renderer as vmware-monitor)
4. **If vmware-monitor is not installed** --> this command/tool is unavailable (AIops delegates to it); install `vmware-monitor`, or use the deeper per-object read tools in that skill

### Object-Centered Investigation → Act (drill-down before you change anything)

**Judgment**: after triage points at a problem object, drill in with one correlated read *before* actuating — the bundle aggregates the object with its surrounding infrastructure and recent history so you (and the operator) see the full picture, not a guess. AIops is the conversational entry point, so triage → investigate → act stays in one conversation.

1. Estate-wide --> `get_cross_vcenter_attention` ("what needs attention now?" across every vCenter). One vCenter configured → skip to `get_cluster_health_summary`
2. Drill into the flagged object (offer the level; skip the question when unambiguous):
   - a VM --> `investigate_vm` → state, host it runs on, cluster context, backing datastores, snapshots, alarms & recent changes, performance signals, correlated event timeline
   - a host --> `investigate_host`; a datastore --> `investigate_datastore`
3. **Then** act on the evidence --> e.g. bundle shows a wedged VM on a hot host → `migrate_vm`; a full datastore → `delete_vm_snapshot` on the sprawl the bundle surfaced. Follow the investigation protocol before any destructive action
4. Widen the window with `hours=72`; render an offline snapshot with `--html` (drill-down sections collapse natively, nothing uploaded)
5. **If the object name is unknown** --> the bundle returns a teaching error naming how to list objects; get the exact name and retry. **If vmware-monitor is not installed** --> these delegated tools are unavailable

### Deploy a Lab Environment

**Pre-flight (judgment, not blind sequence)**:
- Free space: target datastore must have ≥ OVA size × 2 (delta files + thin-provision overhead).
- Name hygiene: prefix with date or owner (`lab-2026-04-30-alice`) so the TTL cleanup audit trail is meaningful.
- TTL: always set. 480 min for a single test session, 7200 min for a week-long sandbox. **Never deploy a "lab" VM without a TTL** — that is how datastores fill up at 3 AM.
- Snapshot timing: take the baseline **after** provisioning succeeds, not before — a pre-provision snapshot is just an empty checkpoint.

**Steps**:
1. `vmware-aiops datastore browse <ds> --pattern "*.ova"` → confirm image present and size
2. `vmware-aiops deploy ova <path> --name <date>-<owner>-<purpose> --datastore <ds>`
3. `vmware-aiops vm guest-exec <name> --cmd /usr/bin/python3 --args "setup.py" --user admin` → if exit ≠ 0, **stop**, do not snapshot a half-provisioned VM
4. `vmware-aiops vm snapshot-create <name> --name baseline` (only if multi-iteration testing; skip for one-shot)
5. `vmware-aiops vm set-ttl <name> --minutes 480`

### Batch Clone for Testing

**Pre-flight**:
- Source VM state: powered-off is safest. If powered-on, VMware Tools must be running and quiesce-capable, else clones may have inconsistent disk state.
- Capacity math: `free_space ≥ source.size × count × 1.2` (full clone) or `≥ count × 2 GB` (linked clone, delta-only).
- Decision rule: **count > 10 → use linked clones** (`deploy linked-clone`); seconds vs minutes per clone, ~100× less storage. Tradeoff: linked clones depend on source snapshot — deleting the snapshot breaks all children.
- Network: batches > 200 can exhaust the vSphere MAC pool.
- TTL: every clone must have one. Use the plan's metadata to track ownership.
- Privileges: `get_access_info(tool="clone_vm")` (CLI `vmware-aiops access-info --tool clone_vm`) — root folder only; `undetermined` is not granted.

**Steps**:
1. `create_vm_plan` with clone + reconfigure + set-ttl steps grouped per VM (atomic per clone)
2. Review the plan with the user — surface count, datastore, irreversible warnings
3. `apply_vm_plan` — stops on first failure (intentional, do not auto-resume)
4. On failure: `rollback_vm_plan` → reverses completed clones; manually verify rollback before retrying

### Migrate VM to Another Host

**Pre-flight (ALL must pass before issuing migrate)**:
- CPU compatibility: target host CPU family must match source, OR cluster must be in EVC mode. Live migration across mismatched CPUs **fails mid-flight** and may leave the VM stunned.
- Network parity: every portgroup the VM uses must exist on the target host's vSwitch with the same VLAN. Missing portgroup → vNICs disconnected post-migration.
- Storage visibility: target host must see all of the VM's datastores; otherwise this is a Storage vMotion, not a host migration — different (slower) operation.
- Affinity rules: if the VM is pinned to source by a DRS host-affinity rule, migration silently violates intent. Check `cluster info` first.
- Hardware passthrough: VMs with PCI passthrough (GPU, USB) **cannot live-migrate** — schedule a cold migration window.

**Steps**:
1. Verify VM state and current host via `vmware-monitor vm info <name>`
2. Verify target host: same cluster, EVC compatible, has required networks/datastores
3. `vmware-aiops vm migrate <name> --to-host <target>` — wait for task completion, do not assume success on return
4. Post-check: `vm info` confirms new host AND power state unchanged AND vNICs connected

## Usage Mode

| Scenario | Recommended | Why |
|----------|:-----------:|-----|
| Local/small models (Ollama, Qwen) | **CLI** | ~2K tokens vs ~8K for MCP |
| Cloud models (Claude, GPT-4o) | Either | MCP gives structured JSON I/O |
| Automated pipelines | **MCP** | Type-safe parameters, structured output |

## MCP Tools (61 — 18 read, 43 write)

| Category | Tools | R/W |
|----------|-------|:---:|
| VM Lifecycle (16) | `list_vm_ttls`, `list_vm_snapshots`, `get_vm_task_status` | Read |
| | `power_on_vm`, `power_off_vm`, `create_vm`, `reconfigure_vm`, `clone_vm`, `migrate_vm`, `delete_vm`, `create_vm_snapshot`, `revert_vm_snapshot`, `delete_vm_snapshot`, `set_vm_ttl`, `cancel_vm_ttl`, `restore_vm_clean_slate` | Write |
| Deployment (8) | `deploy_vm_from_ova`, `deploy_vm_from_template`, `deploy_linked_clone`, `attach_iso_to_vm`, `convert_vm_to_template`, `batch_clone_vms`, `batch_linked_clone_vms`, `batch_deploy_from_spec` | Write |
| Guest Ops (5) | `run_vm_guest_command`, `run_vm_guest_command_with_output`, `upload_vm_guest_file`, `download_vm_guest_file`, `provision_vm_guest` | Write |
| Plan/Apply (4) | `list_vm_plans` | Read |
| | `create_vm_plan`, `apply_vm_plan`, `rollback_vm_plan` | Write |
| Datastore (2) | `browse_datastore`, `scan_all_datastore_images` | Read |
| Network (7) | `list_dvs_portgroups`, `list_host_vmks`, `ping_from_vmk` | Read |
| | `create_dvs_portgroup`, `add_host_vmk`, `remove_host_vmk`, `set_vmk_service` | Write |
| Cluster (10) | `get_cluster_info`, `list_drs_rules` | Read |
| | `create_cluster`, `delete_cluster`, `add_cluster_host`, `remove_cluster_host`, `configure_cluster`, `set_drs_rule_enabled`, `create_drs_rule`, `delete_drs_rule` | Write |
| Alarm Management (3) | `list_vcenter_alarms` | Read |
| | `acknowledge_vcenter_alarm`, `reset_vcenter_alarm` | Write |
| Cluster Triage (1) | `get_cluster_health_summary` (delegates to vmware-monitor) | Read |
| Object Investigation (4) | `investigate_vm`, `investigate_host`, `investigate_datastore`, `get_cross_vcenter_attention` (all delegate to vmware-monitor) | Read |
| Access (1) | `get_access_info` | Read |

**List envelope**: the read list tools — `browse_datastore`, `list_vcenter_alarms`, `list_vm_plans`, `list_vm_snapshots`, `list_vm_ttls` — return `{items, returned, limit, total, truncated, hint}` rather than a bare array. Read the rows from `items` and check `truncated` before concluding a listing is complete; empty `items` with `truncated: false` means checked-and-none, not a failure. The write `batch_*` tools keep their bare list (complete by construction). Rationale, `total` semantics, error shape: `references/capabilities.md`.

**Read/write split**: 18 tools are read-only (per `[READ]` docstring marker), 43 modify state — gating in [Audit & Safety](#audit--safety). `set_vm_ttl` schedules an unattended auto-delete.

**Network write gating**: all four network writes are preview/confirm-gated — `confirm=False` (default) returns the exact spec without writing. `remove_host_vmk` is **fail-closed**: it refuses when the vmk is selected for a host service (management/vMotion/vSAN), lives on a non-default netstack (NSX TEPs, dedicated vMotion stacks), carries a default gateway route, or when any of that cannot be verified — pass `force_unprotected=True` to override the non-absolute protections. The host's only management-enabled vmk is never removable (no override). `set_vmk_service` is **fail-closed** too: it refuses both directions when the host's service map is unreadable, and refuses (no override) to untag `management` from the host's only management-enabled vmk — the call rides the interface it would untag.

**DRS rule gating**: `set_drs_rule_enabled`, `create_drs_rule`, `delete_drs_rule` are preview/confirm-gated and idempotent (matching state returns a no-write noop). `create_drs_rule` handles VM-VM affinity/anti-affinity only (≥2 distinct VMs, all cluster members); VM-Host rules are read via `list_drs_rules` but managed in the vSphere UI. `delete_drs_rule` **refuses non-VM-VM rules** (they can carry licensing/compliance placement constraints) and records the full rule definition in both preview and result so a mistaken delete can be recreated from the audit trail.

**Alarm reset blast radius**: vSphere has no per-alarm clear API. `reset_vcenter_alarm` uses `AlarmManager.ClearTriggeredAlarms`, which clears **all** triggered alarms matching the named alarm's entity type (host/VM/all) and current status (red/yellow) — not just the one named. A bare MCP call previews every alarm it would clear (`blast_radius.will_clear`); `confirm=True` clears and `scope` reports it. A typo fails fast, clearing nothing.

## CLI Quick Reference

```bash
# VM operations
vmware-aiops vm power-on <name> [--target <t>]
vmware-aiops vm power-off <name> [--force]
vmware-aiops vm create <name> --cpu 4 --memory 8192 --disk 100
vmware-aiops vm delete <name>
vmware-aiops vm clone <name> --new-name <new> [--to-host <host>] [--to-datastore <ds>] [--power-on]
vmware-aiops vm migrate <name> --to-host <host> [--to-datastore <ds>]
vmware-aiops vm snapshot-create <name> --name <snap> [--description <text>] [--memory]
vmware-aiops vm snapshot-list <name>
vmware-aiops vm snapshot-revert <name> --name <snap>
vmware-aiops vm snapshot-delete <name> --name <snap> [--remove-children] [--no-wait]
vmware-aiops vm task-status <task-id>                      # poll an async (--no-wait) operation by id
vmware-aiops vm set-ttl <name> --minutes 480 [--dry-run]   # double confirm; daemon auto-deletes VM on expiry

# Guest operations (requires VMware Tools)
vmware-aiops vm guest-exec <name> --cmd <script-path> --args "<args>" --user <username>
vmware-aiops vm guest-upload <name> --local ./script.sh --guest /tmp/script.sh --user <username>

# Deploy
vmware-aiops deploy ova <path> --name <vm> --datastore <ds>
vmware-aiops deploy linked-clone --source <vm> --snapshot <snap> --name <new>

# Cluster
vmware-aiops cluster create <name> --ha --drs
vmware-aiops cluster info <name>
vmware-aiops cluster drs-rules <name>                                     # list DRS rules
vmware-aiops cluster drs-rule-set <name> --rule <r> --enable|--disable [--dry-run]
vmware-aiops cluster drs-rule-create <name> --rule <r> --type antiAffinity --vm <vm1> --vm <vm2> [--disabled] [--dry-run]
vmware-aiops cluster drs-rule-delete <name> --rule <r> [--dry-run]        # VM-VM only; double confirm

# Datastore
vmware-aiops datastore browse <ds> --pattern "*.ova"

# Alarm management
vmware-aiops alarm list [--target <t>]
vmware-aiops alarm acknowledge <entity_name> <alarm_name> [--target <t>]
vmware-aiops alarm reset <entity_name> <alarm_name> [--target <t>]   # double confirm; see blast radius above

# Family
vmware-aiops hub status        # show installed family members + install commands
```

> Full CLI reference: see `references/cli-reference.md`

## Troubleshooting

### "VM not found" error
VM names are case-sensitive in vSphere. Use exact name from `vmware-monitor inventory vms`.

### Guest exec returns empty output
Use `run_vm_guest_command_with_output` instead of `run_vm_guest_command` — it auto-captures stdout/stderr. Basic `run_vm_guest_command` only returns exit code.

### Deploy OVA times out
Large OVA files (>10GB) may exceed the default 120s timeout. The upload happens via HTTP NFC lease — ensure network between the machine running vmware-aiops and ESXi is stable.

### Snapshot delete is slow / "still running after Ns"
Deleting a large snapshot consolidates its delta disk, often for minutes. `vm snapshot-delete` waits
up to 30 min; a "still running, NOT failed" message with a task id means the delete did **not** fail —
poll `vm task-status <task-id>`; do not re-issue it. For large ones, use
`vm snapshot-delete <name> --name <snap> --no-wait` and poll.

### Plan apply fails mid-way
Run `vmware-aiops plan list` to see failed plan status. Ask user if they want to rollback with `rollback_vm_plan`. Irreversible steps (delete_vm) are skipped during rollback.

### Connection refused / SSL error
1. Verify target is reachable: `vmware-aiops doctor`
2. For self-signed certs: set `verify_ssl: false` in config.yaml (lab environments only)

### Every command fails with "session is not authenticated"
The account has no vSphere role. Assign one (Read-Only or higher) on the vCenter root folder; `vmware-aiops doctor` reports this case by name.

## Setup

```bash
uv tool install vmware-aiops==2.4.2
mkdir -p ~/.vmware-aiops
vmware-aiops init  # generates config.yaml and .env templates
chmod 600 ~/.vmware-aiops/.env
```

> Full setup guide, security details, and AI platform compatibility: see `references/setup-guide.md`

## License

MIT — [github.com/vmware-skills/VMware-AIops](https://github.com/vmware-skills/VMware-AIops)
