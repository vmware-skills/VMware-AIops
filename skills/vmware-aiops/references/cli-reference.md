# CLI Reference

```bash
# Diagnostics
vmware-aiops doctor [--skip-auth]

# MCP Config Generator
vmware-aiops mcp-config generate --agent <goose|cursor|claude-code|continue|vscode-copilot|localcowork|mcp-agent>
vmware-aiops mcp-config list

# VM Operations
vmware-aiops vm power-on <vm-name>
vmware-aiops vm power-off <vm-name> [--force]
vmware-aiops vm create <name> [--cpu <n>] [--memory <mb>] [--disk <gb>]
vmware-aiops vm delete <vm-name>
vmware-aiops vm reconfigure <vm-name> [--cpu <n>] [--memory <mb>]
vmware-aiops vm snapshot-create <vm-name> --name <snap-name> [--description <text>] [--memory]
vmware-aiops vm snapshot-list <vm-name>
vmware-aiops vm snapshot-revert <vm-name> --name <snap-name>
vmware-aiops vm snapshot-delete <vm-name> --name <snap-name> [--remove-children]
vmware-aiops vm clone <vm-name> --new-name <name> [--to-host <host>] [--to-datastore <ds>] [--power-on]
vmware-aiops vm migrate <vm-name> --to-host <host> [--to-datastore <ds>]
vmware-aiops vm set-ttl <vm-name> --minutes <n>
vmware-aiops vm cancel-ttl <vm-name>
vmware-aiops vm list-ttl
vmware-aiops vm clean-slate <vm-name> [--snapshot baseline]

# Guest Operations (requires VMware Tools)
vmware-aiops vm guest-exec <vm-name> --cmd /bin/bash --args "-c 'ls -la /tmp'" --user root
vmware-aiops vm guest-upload <vm-name> --local ./script.sh --guest /tmp/script.sh --user root
vmware-aiops vm guest-download <vm-name> --guest /var/log/syslog --local ./syslog.txt --user root

# Plan → Apply (multi-step operations)
vmware-aiops plan list

# Deploy
vmware-aiops deploy ova <path> --name <vm-name> [--datastore <ds>] [--network <net>]
vmware-aiops deploy template <template-name> --name <vm-name> [--datastore <ds>]
vmware-aiops deploy linked-clone --source <vm> --snapshot <snap> --name <new-name>
vmware-aiops deploy iso <vm-name> --iso "[datastore] path/file.iso"
vmware-aiops deploy mark-template <vm-name>
vmware-aiops deploy batch-clone --source <vm> --count <n> [--prefix <prefix>]
vmware-aiops deploy batch <spec.yaml>

# Cluster
vmware-aiops cluster info <name>
vmware-aiops cluster create <name> [--ha] [--drs] [--drs-behavior fullyAutomated|partiallyAutomated|manual] [--datacenter <dc>]
vmware-aiops cluster delete <name>
vmware-aiops cluster add-host <cluster> --host <hostname>
vmware-aiops cluster remove-host <cluster> --host <hostname>   # host must be in maintenance mode; moved to datacenter host folder as standalone
vmware-aiops cluster configure <name> [--ha/--no-ha] [--drs/--no-drs] [--drs-behavior <behavior>]
vmware-aiops cluster drs-rules <name>                                                # list VM-VM + VM-Host DRS rules
vmware-aiops cluster drs-rule-set <name> --rule <name> --enable|--disable [--dry-run] # idempotent; double confirm
vmware-aiops cluster drs-rule-create <name> --rule <name> --type affinity|antiAffinity --vm <vm1> --vm <vm2> [--disabled] [--dry-run]
vmware-aiops cluster drs-rule-delete <name> --rule <name> [--dry-run]                 # VM-VM only; double confirm

# Alarm Management
vmware-aiops alarm list [--target <name>]
vmware-aiops alarm acknowledge <entity_name> <alarm_name> [--target <name>]
vmware-aiops alarm reset <entity_name> <alarm_name> [--target <name>]
# NOTE: 'alarm reset' clears ALL triggered alarms matching the named alarm's
# entity type (host/VM/all) and current status (red/yellow) — vSphere has no
# per-alarm clear API. The CLI double confirmation applies; output reports the
# scope. The reset_vcenter_alarm MCP tool has no confirmation — it clears on the
# first call.

# Datastore
vmware-aiops datastore browse <ds-name> [--path <subdir>]
vmware-aiops datastore scan-images [--target <name>]

# Scanning & Daemon
vmware-aiops scan now [--target <name>]
vmware-aiops daemon start
vmware-aiops daemon stop
vmware-aiops daemon status

# Moved to companion skills:
# vmware-monitor inventory vms/hosts/datastores/clusters, health alarms/events, vm info
# vmware-storage iscsi-enable/status/add-target/remove-target, rescan, vsan health/capacity
# vmware-vks list-namespaces, create-tkc, scale-tkc, etc.
```
