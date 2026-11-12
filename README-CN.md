<!-- mcp-name: io.github.vmware-skills/vmware-aiops -->

# VMware-AIops

把虚拟机运维需求变成明确、可审计的操作。

[English](README.md) · 简体中文

社区维护 · MIT 开源 · 非 VMware/Broadcom 官方产品。

[![18 秒调查示意演示](https://raw.githubusercontent.com/vmware-skills/marketplace/main/assets/demos/vmware-skills-zh.gif)](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-zh.mp4)

使用合成数据的示意动画，非实时录屏。[观看家族 AI 配音介绍（约 2 分钟）](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-zh.mp4)

## 能解决什么问题？

- 部署与管理 VM、快照和 Guest 任务。
- 预览破坏性操作，检查任务结果。
- 通过专业配套模块完成调查。

## 快速开始

安装已发布 CLI，并查看可用命令：

```sh
uv tool install vmware-aiops==2.5.0
vmware-aiops --help
```

需要 uv 与 Python >=3.10。远程查询前，在本地配置目标与凭据。[配置指南](skills/vmware-aiops/references/setup-guide.md)。

Claude Code 及其他宿主安装方式见[家族门户](https://github.com/vmware-skills/marketplace/blob/main/README-CN.md)。

## 使用边界

包含敏感基础设施写操作、Guest 执行及文件传输。受账号权限、本地 Policy 和主机路径边界约束；确认参数不是独立人工审批。

## 详细文档

- [能力范围](skills/vmware-aiops/references/capabilities.md)
- [CLI 用法](skills/vmware-aiops/references/cli-reference.md)
- [Agent 使用约束](skills/vmware-aiops/references/agent-guardrails.md)
- [详细用法与架构](skills/vmware-aiops/references/detailed-guide-CN.md)
- [唯一技能指令正本](skills/vmware-aiops/SKILL.md)

属于 [VMware Skills 家族](https://github.com/vmware-skills/marketplace/blob/main/README-CN.md)：15 个 skill、14 个 MCP server 与一个共享 Policy 库。如果对你有用，欢迎 Star 家族门户。

[问题与反馈](https://github.com/vmware-skills/VMware-AIops/issues) · [MIT](LICENSE)
