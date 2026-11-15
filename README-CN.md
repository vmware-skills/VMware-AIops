<!-- mcp-name: io.github.vmware-skills/vmware-aiops -->

# VMware-AIops

<a id="readme-overview"></a>

把 vCenter 和 ESXi 的虚拟机运维需求变成明确、可审计的操作。

[English](https://github.com/vmware-skills/VMware-AIops/blob/main/README.md) · 简体中文

社区维护 · MIT 开源 · 非 VMware/Broadcom 官方产品。

[![24 秒调查示意演示](https://raw.githubusercontent.com/vmware-skills/marketplace/main/assets/demos/vmware-skills-zh.gif)](https://github.com/vmware-skills/marketplace/releases/download/demos-2026-11-13-operations/vmware-skills-zh.mp4)

GIF 使用合成数据；视频包含脱敏的历史截图。[下载家族 AI 配音介绍（约 3 分钟）](https://github.com/vmware-skills/marketplace/releases/download/demos-2026-11-13-operations/vmware-skills-zh.mp4)

<a id="readme-capabilities-and-workflows"></a>

## 能解决什么问题？

- 部署与管理 VM、快照和 Guest 任务。
- 预览破坏性操作，检查任务结果。
- 通过专业配套模块完成调查。

<a id="readme-quick-start"></a>

## 快速开始

安装已发布 CLI，并查看可用命令：

```sh
uv tool install vmware-aiops==2.5.0
vmware-aiops --help
```

需要 uv 与 Python >=3.10。远程查询前，在本地配置目标与凭据。[配置指南](https://github.com/vmware-skills/VMware-AIops/blob/main/skills/vmware-aiops/references/setup-guide.md)。

Agent 宿主安装见本模块的[配置指南](https://github.com/vmware-skills/VMware-AIops/blob/main/skills/vmware-aiops/references/setup-guide.md)，以及 [Cursor、Codex、Gemini CLI、Cline 与本地模型示例](https://github.com/vmware-skills/marketplace/blob/main/docs/agent-hosts-CN.md)。

选模型可用[共享模型自测流程](https://github.com/vmware-skills/marketplace/blob/main/model-fit/RUNNER-CN.md)：统一任务、复核质量与安全，再按任务组选择最小已测通过配置；不代表生产质量保证。

<a id="readme-safety-and-audit"></a>

## 使用边界

包含敏感基础设施写操作、Guest 执行及文件传输。受账号权限、本地 Policy 和主机路径边界约束；确认参数不是独立人工审批。

<a id="readme-mcp-configuration"></a> <a id="readme-troubleshooting"></a> <a id="readme-reference"></a>

## 详细文档

- [能力范围](https://github.com/vmware-skills/VMware-AIops/blob/main/skills/vmware-aiops/references/capabilities.md)
- [CLI 用法](https://github.com/vmware-skills/VMware-AIops/blob/main/skills/vmware-aiops/references/cli-reference.md)
- [Agent 使用约束](https://github.com/vmware-skills/VMware-AIops/blob/main/skills/vmware-aiops/references/agent-guardrails.md)
- [详细用法与架构](https://github.com/vmware-skills/VMware-AIops/blob/main/skills/vmware-aiops/references/detailed-guide-CN.md)
- [MCP 启动配置](https://github.com/vmware-skills/VMware-AIops/blob/main/.mcp.json)
- [唯一技能指令正本](https://github.com/vmware-skills/VMware-AIops/blob/main/skills/vmware-aiops/SKILL.md)

<a id="readme-companion-skills"></a>

属于 [VMware Skills 家族](https://github.com/vmware-skills/marketplace/blob/main/README-CN.md)：15 个 skill、14 个 MCP server 与一个共享 Policy 库。如果对你有用，欢迎 Star 家族门户。

<a id="readme-license"></a>

[分享接入进度或首次结果](https://github.com/vmware-skills/marketplace/issues/new?template=setup-feedback.yml) · [报告问题](https://github.com/vmware-skills/VMware-AIops/issues) · [MIT](https://github.com/vmware-skills/VMware-AIops/blob/main/LICENSE)
