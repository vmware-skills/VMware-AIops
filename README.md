<!-- mcp-name: io.github.vmware-skills/vmware-aiops -->

# VMware-AIops

Turn VM lifecycle requests into explicit, auditable operations.

English · [简体中文](README-CN.md)

Community-maintained · MIT · Not an official VMware/Broadcom product.

[![18-second illustrative investigation demo](https://raw.githubusercontent.com/vmware-skills/marketplace/main/assets/demos/vmware-skills-en.gif)](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-en.mp4)

Synthetic-data animation, not a live recording. [Watch the narrated family introduction (~2 min)](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-en.mp4)

## Why use it?

- Deploy and manage VMs, snapshots and guest tasks.
- Preview destructive actions and inspect task results.
- Use dedicated companion modules for focused investigations.

## Get started

Install the published CLI and inspect its commands:

```sh
uv tool install vmware-aiops==2.5.0
vmware-aiops --help
```

Requires uv and Python >=3.10. Configure targets and credentials locally before remote queries. [Setup guide](skills/vmware-aiops/references/setup-guide.md).

For Claude Code and other host installation paths, see the [family portal](https://github.com/vmware-skills/marketplace).

## Know the boundaries

Includes sensitive infrastructure writes, guest execution and file transfers. Account permissions, local Policy and host-path boundaries apply; confirmation is not independent human approval.

## Go deeper

- [Capabilities](skills/vmware-aiops/references/capabilities.md)
- [CLI reference](skills/vmware-aiops/references/cli-reference.md)
- [Agent guardrails](skills/vmware-aiops/references/agent-guardrails.md)
- [Detailed usage and architecture](skills/vmware-aiops/references/detailed-guide.md)
- [Canonical skill instructions](skills/vmware-aiops/SKILL.md)

Part of [VMware Skills](https://github.com/vmware-skills/marketplace): 15 skills, 14 MCP servers and one shared Policy library. If it helps your work, star the family portal.

[Issues and feedback](https://github.com/vmware-skills/VMware-AIops/issues) · [MIT](LICENSE)
