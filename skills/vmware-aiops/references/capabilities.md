# Capabilities Reference

## Automation Level Reference

Each operation is classified by autonomy level per the Enterprise Harness Engineering framework. This tells AI agents how much human gating each tool needs:

| Level | Meaning | Agent autonomy | Examples in this skill |
|:-:|---|---|---|
| **L1** | Read-only, raw data | Always auto-run | `cluster_info`, `browse_datastore`, `scan_datastore_images`, `list_vcenter_alarms`, `vm_list_snapshots`, `vm_list_ttl`, `vm_task_status` |
| **L2** | Read + analysis / recommendation | Always auto-run | `cluster_health_summary`, `cross_vcenter_attention`, `vm_investigation_bundle`, `host_investigation_bundle`, `datastore_investigation_bundle`; scheduled scan reports, alarm/event correlation, log pattern analysis |
| **L3** | Single write | The level is a statement about blast radius, not about an enforced gate. On the CLI these commands double-confirm; over MCP they run on the first call — see [What gates a write](#what-gates-a-write) | `vm_power_on`, `vm_power_off`, `vm_delete`, `vm_create_snapshot`, `vm_clone`, `vm_migrate` |
| **L4** | Multi-step plan / apply workflow | Plan generation auto; apply gated by user approval | `vm_create_plan` → `vm_apply_plan` → `vm_rollback_plan`, batch-clone, batch-deploy YAML |
| **L5** | Auto-remediation from learned pattern | Pattern library only; requires `risk:low` + `reversible:true` + `repeatable:true` + signed approval | *(roadmap — not implemented; candidates: snapshot consolidation, orphaned VM cleanup)* |

**Notes**:
- L1/L2 tools are read-only and safe for agents to call unprompted.
- **The levels describe risk, not enforcement.** Nothing in this skill stops an agent calling an L3 or L4 tool over MCP. What decides whether the write lands is the vCenter account — see [What gates a write](#what-gates-a-write).
- **List envelope**: the read list tools (`browse_datastore`, `list_vcenter_alarms`, `vm_list_plans`, `vm_list_snapshots`, `vm_list_ttl`) return `{items, returned, limit, total, truncated, hint}` instead of a bare array, so an agent can tell a complete answer from a first page rather than inferring it (issue #31). All five enumerate their collection in full before any limit is applied, so `total` is always the real count; only `list_vcenter_alarms` takes a `limit` and can therefore report `truncated: true`. The write `batch_*` tools deliberately keep a bare list — each row is a per-item result of work already done, complete by construction. Errors from these read tools are `{error, hint}` (a dict, not a one-element list).
- L3+ tools always pass through the `@vmware_tool` decorator: connection check → policy check (opt-in `deny` rules only; nothing is denied by default) → audit log. There is no confirmation step in that chain.
- Multi-party approval, where it is genuinely required, is [vmware-pilot](https://github.com/vmware-skills/VMware-Pilot)'s job — it has the state machine and a real human approval step. See it also for cross-skill L4 orchestration and the Dispatcher/Subagent pattern.

## What gates a write

<!-- gate-inventory: the counts and names below are checked against the live
     tool registry by tests/eval/regression/test_documented_gates_match_the_registry.py.
     Adding a write tool makes them wrong and turns that test red. -->

| Surface | Confirmation | Preview |
|---|---|---|
| **CLI** | Two interactive `typer.confirm` prompts before every irreversible or guest-writing command. The set is derived from the MCP `destructiveHint` annotations, so a new such command fails the test suite until it has them, and a declined prompt is audited as `rejected`. *Honest limitation:* an agent with a shell satisfies both prompts by piping `yes` into the command. This defends the mistyped command, not a determined caller. | `--dry-run` on every write command |
| **MCP** | **None, by design.** A write tool acts on the first call. There is no `confirmed=` handshake, no approval tier, and no read-only switch — the switch existed in v1.8.0–1.8.6 and was removed in v1.8.7 (decision **D-2** of the family security HLD, 2026-07-21) because it was enforced on the MCP path only and any agent with a shell stepped around it. A two-step handshake was considered in the same review and cut: it is neither authorization nor accountability, only a speed-bump that a model intending to act steps over by passing `confirmed=True`. | 7 of the 43 write tools default to a no-write preview (below) |

**What actually protects the estate over MCP is the vCenter/ESXi service account.**
Writes the account may not perform are refused by vCenter itself, whatever the
agent intends, on every surface, with no way around it from inside the skill. To
run an agent read-only, give it a read-only vCenter role and point the skill's
`.env` at that account — one decision, enforced where it is made. What happened
is then recoverable from `~/.vmware/audit.db`, which every write goes through
before the caller sees a result. Nothing in this skill will stop `vm_delete`
deleting a VM the account is allowed to delete.

- **Write tools: 43** — every tool whose description starts `[WRITE]` and whose `readOnlyHint` is `false`.
- **Confirm-gated: 7** — `add_host_vmk`, `create_drs_rule`, `create_dvs_portgroup`, `delete_drs_rule`, `remove_host_vmk`, `set_drs_rule_enabled`, `set_vmk_service`
  <br>These host-networking and DRS authoring tools take a `confirm` argument that defaults to false, in which case they validate everything they can and return what *would* change without writing. It is a preview switch, not an approval gate: a single call passing confirm true is all it takes, and the tool has no way to know a human saw the preview.
- **Ungated: 36** — everything else, including `vm_delete`, `cluster_delete`, `vm_revert_snapshot`, `vm_clean_slate` and `vm_guest_exec`. These act immediately on the first call.

### `vm_guest_exec` deserves naming

`vm_guest_exec` runs a caller-supplied command inside the guest OS through
VMware Tools, with the credentials passed to it — which its own documented
example makes `root`. Nothing in this skill bounds what the command may be:
`rm -rf /` is a well-formed argument, and the same is true of
`vm_guest_exec_output` and of the `exec` steps inside `vm_guest_provision`.
It is the widest blast radius in the skill and it is ungated over MCP.

Two things follow. First, the guest credentials are a second, separate
authorization boundary — a read-only vCenter role does not constrain what these
tools do *inside* a VM, because that is decided by the guest account. Give the
skill a guest account with the privileges the work actually needs. Second, until
2026-08-30 all four tools that push content into a guest were annotated
`destructiveHint: false` — the field a client consults before deciding whether
to ask its user. They now declare `true`. These tools exist only in this repo,
so that correction is complete here; whether the comparable high-blast-radius
tools in the other thirteen skills carry honest annotations is a family-wide
question this repo cannot settle on its own.

<!-- /gate-inventory -->

## Triage & Object Investigation (read-only)

Five opinionated read-only reports that **aggregate and correlate server-side** and
return high-signal results — never raw inventory. They exist so the agent can decide
*where to look* before actuating anything. All five delegate to the
[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor) library using AIops' own
vCenter connection, so **`vmware-monitor` must be installed**; without it these tools
are unavailable. All are point-in-time (no trending). Each has a `--html` CLI form
that writes a self-contained, timestamped offline snapshot (no external references,
drill-downs collapse via native `<details>`, zero JavaScript).

| Operation | CLI | MCP Tool | vCenter | ESXi |
|-----------|-----|----------|:-------:|:----:|
| Cluster health summary | `summary` | `cluster_health_summary` | ✅ | ❌ |
| Cross-vCenter attention | `attention` | `cross_vcenter_attention` | ✅ | ❌ |
| VM investigation bundle | `investigate vm <name>` | `vm_investigation_bundle` | ✅ | ✅ |
| Host investigation bundle | `investigate host <name>` | `host_investigation_bundle` | ✅ | ✅ |
| Datastore investigation bundle | `investigate datastore <name>` | `datastore_investigation_bundle` | ✅ | ✅ |

### `cluster_health_summary` — "is anything on fire?"

The first look. Rolls up hosts, VM power state, live CPU/memory pressure and triggered
alarms per cluster, assigns an opinionated `ok` / `warn` / `critical` status, and
flattens individual anomalies into a ranked `top_issues` focus list (worst first, each
carrying a drill-down hint). Returns `{totals, top_issues, issues_total, clusters,
snapshot, customization_hint}` — lead with `top_issues`, show `clusters` as context.

| Parameter | Type | Default | Behavior |
|-----------|------|---------|----------|
| `target` | str (optional) | default target | Named vCenter/ESXi target from `config.yaml` |
| `cluster_filter` | str (optional) | None (all) | Case-insensitive substring; suppresses standalone-hosts bucket |
| `include_vms` | bool | True | Roll up VM power counts; False skips the VM pass (faster on huge fleets) |
| `top_n` | int | 10 | Cap the `top_issues` focus list; `issues_total` keeps the pre-cap count; 0 hides the list |

**Typical response tokens**: ~120–400 (one compact row per cluster + totals); scales
with cluster count, not VM count. Aggregation happens in the tool — the model never
sees raw inventory.

### `cross_vcenter_attention` — "where do I look first, anywhere in the estate?"

Merges every configured target's cluster-health summary into a single globally ranked
`top_issues` list (each item tagged with its `vcenter`) plus a per-target rollup.
Degrades gracefully: an unreachable target is listed under `unreachable` and the rest
still aggregate. Use it before `cluster_health_summary` when more than one vCenter is
configured; with a single target, go straight to `cluster_health_summary`.

| Parameter | Type | Default | Behavior |
|-----------|------|---------|----------|
| `cluster_filter` | str (optional) | None (all) | Case-insensitive cluster substring applied to every target |
| `top_n` | int | 10 | Cap the merged `top_issues` focus list |

**Typical response tokens**: ~200–600 (ranked issue list + one row per target); scales
with target count, not inventory size.

### `*_investigation_bundle` — one correlated drill-down per object

Use **after** triage points at a specific object. Each bundle collects and *correlates*
the object with its surrounding infrastructure and recent history in one batched call,
so the agent does not stitch together separate info/alarm/snapshot/performance/event
reads. All three accept `hours` (event-timeline look-back, default 24) and an optional
`target`. An unknown object name returns a teaching error naming how to list objects.

| Tool | Required arg | Correlates |
|------|--------------|------------|
| `vm_investigation_bundle` | `vm_name` | VM state, the host it runs on, cluster context, backing datastores, snapshots, triggered alarms, live performance, merged event timeline (VM + host + cluster + datastores, newest first) |
| `host_investigation_bundle` | `host_name` | Connection state, CPU/memory, ESXi version, uptime, cluster context, rollup of VMs it runs, datastores it mounts, alarms across host/cluster/datastore, live performance, merged event timeline |
| `datastore_investigation_bundle` | `datastore_name` | Capacity/free space/accessibility, hosts that mount it, rollup of VMs it backs, alarms across datastore/host, merged event timeline. (Per-datastore latency is a separate perf report, not included.) |

**Typical response tokens**: ~400–1200 per bundle (correlated summary + capped event
timeline); grows with the `hours` window, not with fleet size. Explain the result in
operational language — do not dump it raw.

## VM Lifecycle

| Operation | Command | Confirmation | vCenter | ESXi |
|-----------|---------|:------------:|:-------:|:----:|
| Power On | `vm power-on <name>` | — | ✅ | ✅ |
| Graceful Shutdown | `vm power-off <name>` | Double | ✅ | ✅ |
| Force Power Off | `vm power-off <name> --force` | Double | ✅ | ✅ |
| Reset | `vm reset <name>` | — | ✅ | ✅ |
| Suspend | `vm suspend <name>` | — | ✅ | ✅ |
| VM Info | `vm info <name>` | — | ✅ | ✅ |
| Create VM | `vm create <name> --cpu --memory --disk` | — | ✅ | ✅ |
| Delete VM | `vm delete <name>` | Double | ✅ | ✅ |
| Reconfigure | `vm reconfigure <name> --cpu --memory` | Double | ✅ | ✅ |
| Create Snapshot | `vm snapshot-create <name> --name <snap> [--description <text>] [--memory]` | — | ✅ | ✅ |
| List Snapshots | `vm snapshot-list <name>` | — | ✅ | ✅ |
| Revert Snapshot | `vm snapshot-revert <name> --name <snap>` | Double | ✅ | ✅ |
| Delete Snapshot | `vm snapshot-delete <name> --name <snap> [--remove-children]` | Double | ✅ | ✅ |
| Poll Async Task | `vm task-status <task-id>` | — | ✅ | ✅ |
| Clone VM | `vm clone <name> --new-name <new> [--to-host <host>] [--to-datastore <ds>]` | Double | ✅ | ✅ |
| vMotion | `vm migrate <name> --to-host <host> [--to-datastore <ds>]` | Double | ✅ | ❌ |
| Set TTL | `vm set-ttl <name> --minutes <n>` | Double | ✅ | ✅ |
| Cancel TTL | `vm cancel-ttl <name>` | — | ✅ | ✅ |
| List TTLs | `vm list-ttl` | — | ✅ | ✅ |
| Clean Slate | `vm clean-slate <name> [--snapshot baseline]` | Double | ✅ | ✅ |
| Guest Exec | `vm guest-exec <name> --cmd /bin/bash --args "-c 'whoami'"` | Double | ✅ | ✅ |
| Guest Upload | `vm guest-upload <name> --local f.sh --guest /tmp/f.sh` | Double | ✅ | ✅ |
| Guest Download | `vm guest-download <name> --guest /var/log/syslog --local ./syslog` | — | ✅ | ✅ |

> Guest Operations require VMware Tools running inside the guest OS.

> `vm task-status` / `vm_task_status` polls a vSphere task id returned by an async
> write (today: `vm_delete_snapshot`) instead of re-running the operation. Returns
> state (`queued` / `running` / `success` / `error` / `gone`), progress percent, and
> the entity name. `gone` means vCenter already garbage-collected a completed task —
> re-list the resource to confirm the final state. A failed task carries its fault
> under `task_error`, not `error` — the poll succeeded, the task did not.
> **Typical response tokens**: ~40–80 (single status record).

## Plan → Apply (Multi-step Operations)

For complex operations involving 2+ steps or 2+ VMs, use the plan/apply workflow:

| Step | MCP Tool / CLI | Description |
|------|---------------|-------------|
| 1. Create Plan | `vm_create_plan` | Validates actions, checks targets in vSphere, generates plan with rollback info |
| 2. Review | — | AI shows plan to user: steps, affected VMs, irreversible warnings |
| 3. Apply | `vm_apply_plan` | Executes sequentially; stops on failure |
| 4. Rollback (if failed) | `vm_rollback_plan` | Asks user, then reverses executed steps (skips irreversible) |

Plans are stored in `~/.vmware-aiops/plans/`, deleted on success, auto-cleaned after 24h.

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

### Guest Operations Notes

`vm_guest_exec_output` — execute a shell command and **capture stdout/stderr** automatically. OS auto-detected (Linux/Windows) via `vm.guest.guestFamily`. No manual redirection needed.

`vm_guest_provision` — run an ordered sequence of exec/upload/service steps in one call. Stops on first failure. Typical use: SSH key injection → package install → service start.

## Datastore Browser

| Feature | vCenter | ESXi | Details |
|---------|:-------:|:----:|---------|
| Browse Files | ✅ | ✅ | List files/folders in any datastore path |
| Scan Images | ✅ | ✅ | Discover ISO, OVA, OVF, VMDK across all datastores |

> For datastore management, iSCSI, and vSAN, use [vmware-storage](https://github.com/vmware-skills/VMware-Storage). For Tanzu Kubernetes, use [vmware-vks](https://github.com/vmware-skills/VMware-VKS).

## Network (dvSwitch portgroups + host VMkernel)

MCP-only (no CLI subcommand). Seven tools for distributed-switch portgroup and host VMkernel authoring, plus an MTU-path diagnostic. Writes are preview/confirm gated; `remove_host_vmk` and `set_vmk_service` are fail-closed. For NSX overlay segments/gateways/NAT, use [vmware-nsx](https://github.com/vmware-skills/VMware-NSX) — this surface is the underlay (VLAN-backed DVS portgroups, host kernel interfaces).

| Tool | R/W | Risk | Operation |
|------|:---:|:----:|-----------|
| `list_dvs_portgroups` | R | low | Distributed portgroups: binding type, VLAN (id/trunk/pvlan), port count, uplink flag. Scope with `dvs_name`. |
| `create_dvs_portgroup` | W | medium | VLAN-tagged portgroup on a dvSwitch; `earlyBinding` or `ephemeral` (ephemeral attaches with vCenter down — self-hosted-VCSA use case). `confirm=False` previews. |
| `list_host_vmks` | R | low | VMkernel adapters per host: IP/netmask/dhcp, MTU, MAC, portgroup, netstack, selected services. `services` is `null` (not `[]`) when a host's service map can't be read. A host vCenter could not reach appears as a row with `reachable: false` and null facts rather than being dropped; check `hosts_unreachable` before treating the list as a complete estate inventory. |
| `add_host_vmk` | W | medium | Static-IP vmk on a DVS portgroup — no gateway, no services (throwaway test-vmk shape). `confirm=False` previews; returns the assigned device (`vmk2`). |
| `remove_host_vmk` | W | high | Fail-closed removal. Refuses on service selection / non-default netstack / default route / unverifiable state; `force_unprotected=True` overrides all but the only-management-vmk absolute. |
| `set_vmk_service` | W | medium | Tag/untag a host service (nicType: vmotion, management, vsan, vSphereProvisioning, …) on an existing vmk — completes `add_host_vmk` (adapters are created serviceless). Idempotent; `confirm=False` previews. Fail-closed on unreadable service map; refuses (no override) to untag `management` from the only management-enabled vmk. |
| `vmk_ping` | R | medium | DF-bit-capable ping sourced from a vmk via esxcli-over-API (no SSH). `df=True size=1572` proves a ≥1600 overlay floor; `size=8972` proves full jumbo. Oversized DF'd packets report `fault` structurally, not as an error. |

> **Typical response tokens**: `list_*` ~60–400 (one compact row per portgroup/vmk, paginated at 200/100); `create`/`add`/`remove` ~40–120 (preview or result record); `vmk_ping` ~80–200 (request + per-summary stats or the esxcli fault text).

## Cluster Management

| Operation | Command | Confirmation | vCenter | ESXi |
|-----------|---------|:------------:|:-------:|:----:|
| Cluster Info | `cluster info <name>` | — | ✅ | ❌ |
| Create Cluster | `cluster create <name> [--ha] [--drs]` | — | ✅ | ❌ |
| Delete Cluster | `cluster delete <name>` | Double | ✅ | ❌ |
| Add Host | `cluster add-host <cluster> --host <host>` | Double | ✅ | ❌ |
| Remove Host | `cluster remove-host <cluster> --host <host>` | Double | ✅ | ❌ |
| Configure HA/DRS | `cluster configure <name> [--ha/--no-ha] [--drs/--no-drs]` | Double | ✅ | ❌ |
| List DRS Rules | `cluster drs-rules <name>` | — | ✅ | ❌ |
| Enable/Disable DRS Rule | `cluster drs-rule-set <name> --rule <r> --enable\|--disable` | Double | ✅ | ❌ |
| Create DRS Rule | `cluster drs-rule-create <name> --rule <r> --type affinity\|antiAffinity --vm <v1> --vm <v2>` | Double | ✅ | ❌ |
| Delete DRS Rule | `cluster drs-rule-delete <name> --rule <r>` | Double | ✅ | ❌ |

> `remove-host` requires the host to be in **maintenance mode** first; the host is moved out of the cluster into the datacenter's host folder as a standalone host (`Folder.MoveIntoFolder_Task`).
>
> **DRS rules**: `drs-rule-create` handles VM-VM affinity/anti-affinity only (≥2 distinct VMs, all cluster members); `drs-rule-delete` refuses VM-Host and other rule types (they can carry licensing/compliance placement constraints — manage those in the vSphere UI) and records the full definition for recreate. All three writes are idempotent (matching state = no-write noop) and support `--dry-run`.

## Alarm Management

| Operation | Command | Confirmation | vCenter | ESXi |
|-----------|---------|:------------:|:-------:|:----:|
| List Triggered Alarms | `alarm list [--target <t>]` | — | ✅ | ❌ |
| Acknowledge Alarm | `alarm acknowledge <entity> <alarm>` | — | ✅ | ❌ |
| Clear (Reset) Alarms | `alarm reset <entity> <alarm>` | Double | ✅ | ❌ |

> **Blast radius**: vSphere has no per-alarm clear API. `alarm reset` / `reset_vcenter_alarm` uses `AlarmManager.ClearTriggeredAlarms`, which clears **all** triggered alarms matching the named alarm's entity type (host/VM/all) and current status (red/yellow) — not just the named one. The named alarm is looked up first (typos fail fast), and the result's `scope` field reports exactly what was cleared. Cleared alarms re-trigger automatically if their underlying condition persists.

## Scheduled Scanning & Notifications

| Feature | Details |
|---------|---------|
| Daemon | APScheduler-based, configurable interval (default 15 min) |
| Multi-target Scan | Sequentially scan all configured vCenter/ESXi targets |
| Scan Content | Alarms + Events + Host logs (hostd, vmkernel, vpxd) |
| Log Analysis | Regex pattern matching: error, fail, critical, panic, timeout |
| Webhook | Slack, Discord, or any HTTP endpoint |

## Safety Features

| Feature | Details |
|---------|---------|
| Plan → Confirm → Execute → Log | CLI workflow: show current state, confirm changes, execute, audit log |
| Double Confirmation (**CLI only**) | CLI destructive commands (power-off, delete, reconfigure, snapshot-revert/delete, clean-slate, guest-exec, guest-upload, cluster delete/remove-host, alarm clear) require 2 sequential prompts and take no bypass flag. **The MCP tools have no confirmation step at all** — see [What gates a write](#what-gates-a-write) |
| Rejection Logging | Declined CLI confirmations are recorded in the audit trail for security review |
| Audit Trail | All operations logged to `~/.vmware/audit.db` (SQLite WAL, via vmware-policy) with before/after state |
| Input Validation | VM name length/format, CPU (1-128), memory (128-1048576 MB), disk (1-65536 GB) validated before execution |
| Password Protection | `.env` file loading, never in command line or shell history; file permission check at startup |
| SSL Self-signed Support | `verify_ssl: false` — **only** for ESXi hosts with self-signed certificates in isolated lab/home environments. Production environments should use CA-signed certificates with full TLS verification enabled. |
| Task Waiting | All async operations wait for completion and report result |
| State Validation | Pre-operation checks (VM exists, power state correct) |

## Version Compatibility

| vSphere Version | Support | Notes |
|----------------|---------|-------|
| 8.0 / 8.0U1-U3 | ✅ Full | `CreateSnapshot_Task` deprecated → use `CreateSnapshotEx_Task` |
| 7.0 / 7.0U1-U3 | ✅ Full | All APIs supported |
| 6.7 | ✅ Compatible | Backward-compatible, tested |
| 6.5 | ✅ Compatible | Backward-compatible, tested |

> pyVmomi auto-negotiates the API version during SOAP handshake — no manual configuration needed.
