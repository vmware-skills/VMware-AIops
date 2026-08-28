<!-- mcp-name: io.github.vmware-skills/vmware-aiops -->
# VMware AIops

> **作者**: Wei Zhou, VMware by Broadcom — wei-wz.zhou@broadcom.com
> 本项目由 VMware 工程师维护的社区项目，非 VMware 官方产品。
> VMware 官方开发者工具请访问 [developer.broadcom.com](https://developer.broadcom.com)。

[English](README.md) | 中文

AI 驱动的 VMware vCenter/ESXi VM 生命周期管理与部署工具 — 60 个工具。

> **配套技能**负责其他领域：
>
> | 技能 | 范围 | 安装 |
> |------|------|------|
> | **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | 只读：资源清单、健康检查、告警、事件、指标 | `uv tool install vmware-monitor` |
> | **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | 数据存储、iSCSI、vSAN 管理 | `uv tool install vmware-storage` |
> | **[vmware-vks](https://github.com/vmware-skills/VMware-VKS)** | Tanzu 命名空间、TKC 集群生命周期 | `uv tool install vmware-vks` |
>
> **只需要只读监控？** 使用 [VMware-Monitor](https://github.com/vmware-skills/VMware-Monitor) — 代码库中零破坏性函数。

[![ClawHub](https://img.shields.io/badge/ClawHub-vmware--aiops-orange)](https://clawhub.ai/skills/vmware-aiops)
[![Skills.sh](https://img.shields.io/badge/Skills.sh-Install-blue)](https://skills.sh/vmware-skills/VMware-AIops)
[![Claude Code Marketplace](https://img.shields.io/badge/Claude_Code-Marketplace-blueviolet)](https://github.com/vmware-skills/VMware-AIops)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## ⚡ 快速调查报告（只读）

总览 → 调查 → 处置，一次对话完成。五个有主见的只读报告在**服务端聚合并关联**数据、返回高信号结果（绝不灌 raw inventory），让你在动手改之前先判断*该看哪里*。每个都支持 `--html` 生成**自包含离线 HTML 快照**（零外链；下钻细节用原生 `<details>` 折叠，零 JavaScript）。全部委托 [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor) 库、用 AIops 自己的 vCenter 连接。

| 问题 | 命令 | 关联什么 |
|------|------|---------|
| **"现在该关注什么？"**（全 vCenter） | `vmware-aiops attention` | 所有 vCenter 合并成一个全局排序异常榜；连不上的目标优雅降级 |
| **"有什么着火了？"**（全集群） | `vmware-aiops summary` | 每集群主机 + VM 电源 + 实时 CPU/内存 + 告警 → Top-N 异常榜 + 每集群状态 |
| **"这个 VM 周围在发生什么？"** | `vmware-aiops investigate vm <名>` | VM 状态 + 主机 + 集群 + 背后数据存储 + 快照 + 告警 + 性能 + 合并事件时间线 |
| **"这个主机周围在发生什么？"** | `vmware-aiops investigate host <名>` | 主机状态 + 集群 + 其上 VM + 挂载数据存储 + 告警 + 性能 + 关联时间线 |
| **"这个数据存储周围在发生什么？"** | `vmware-aiops investigate datastore <名>` | 容量/剩余 + 挂载主机 + 落其上的 VM + 告警 + 关联时间线 |

```bash
vmware-aiops attention                            # 现在该关注什么，全 vCenter
vmware-aiops investigate vm web-01 --hours 72     # 一个 VM 的全貌，然后处置
vmware-aiops investigate vm web-01 --html         # → 离线快照写入 ~/vmware-health/
```

MCP 下对应工具为 `cluster_health_summary`、`cross_vcenter_attention`、`vm_investigation_bundle`、`host_investigation_bundle`、`datastore_investigation_bundle`。（需安装 `vmware-monitor`。）

### 快速安装（推荐）

支持 Claude Code、Cursor、Codex、Gemini CLI、Trae 等 30+ AI 工具：

```bash
# 通过 Skills.sh 安装
npx skills add vmware-skills/VMware-AIops

# 通过 ClawHub 安装
clawhub install @zw008/vmware-aiops
```

### PyPI 安装（无需访问 GitHub）

```bash
# 通过 uv 安装（推荐）
uv tool install vmware-aiops

# 或通过 pip 安装
pip install vmware-aiops

# 国内镜像加速
pip install vmware-aiops -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 离线 / 气隙环境安装（从源码）

本项目使用现代 PEP 517 构建系统（hatchling），因此**故意不提供 `setup.py`**——这是
预期行为，而非缺失文件。如果你克隆源码后遇到 `ERROR: File "setup.py" or "setup.cfg"
not found ... editable mode currently requires a setuptools-based build`，说明你的
`pip` 版本早于 21.3，无法对非 setuptools 后端执行*可编辑*（`-e`）安装。可编辑模式只是
开发便利，运行本工具并不需要——任选其一：

```bash
# 从源码树安装——普通（非可编辑）安装会构建 wheel：
pip install .              # 不要用  pip install -e .

# ……或先升级 pip，可编辑安装同样可用：
pip install --upgrade pip && pip install -e .
```

对于**完全气隙的主机**，在联网机器上构建 wheel 再拷贝过去——目标主机无需任何网络：

```bash
# 在联网机器上，把本包及其依赖收集为 wheel：
pip wheel . -w dist        # → dist/*.whl   （或用 uv build，仅构建本包）

# 把 dist/ 拷贝到气隙主机，然后离线安装：
pip install --no-index --find-links dist vmware-aiops
```

---

## 功能总览

### 本技能覆盖的功能

| 分类 | 工具 | 数量 |
|------|------|:----:|
| **VM 生命周期** | 开关机、TTL 自动删除、Clean Slate | 6 |
| **部署** | OVA、模板、链接克隆、批量克隆/部署 | 8 |
| **Guest Ops** | 执行命令、上传/下载文件、批量制备 | 5 |
| **Plan/Apply** | 多步骤编排与回滚 | 4 |
| **集群** | 创建、删除、HA/DRS 配置、添加/移除主机 | 6 |
| **数据存储** | 浏览文件、扫描镜像 | 2 |
| **网络** | dvSwitch 端口组 列出/创建、主机 VMkernel 列出/添加/移除、DF 位 MTU 路径 ping | 6 |

### CLI vs MCP：如何选择

| 场景 | 推荐模式 | 原因 |
|------|:-------:|------|
| **本地/小模型**（Ollama、Qwen <32B） | **CLI** | 上下文占用 ~2K tokens vs MCP ~10K；小模型难以处理 60 个工具的 schema |
| **Token 敏感场景** | **CLI** | SKILL.md + Bash = 最小开销 |
| **云端大模型**（Claude、GPT-4o） | 均可 | MCP 提供结构化 JSON 输入输出 |
| **自动化管道 / Agent 链式调用** | **MCP** | 类型安全参数，结构化输出，无需 Shell 解析 |
| **监控 / 存储 / K8s** | 配套技能 | 见 [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)、[vmware-storage](https://github.com/vmware-skills/VMware-Storage)、[vmware-vks](https://github.com/vmware-skills/VMware-VKS) |

> **经验法则**：追求成本和兼容性选 CLI，追求结构化自动化选 MCP。

### 架构

```
用户 (自然语言)
  ↓
AI CLI 工具 (Claude Code / Gemini / Codex / Aider / Continue / Trae / Kimi)
  ↓ 读取 SKILL.md / AGENTS.md / rules 指令
  ↓
vmware-aiops CLI
  ↓ pyVmomi (vSphere SOAP API)
  ↓
vCenter Server ──→ ESXi 集群 ──→ VM
    或
ESXi 独立主机 ──→ VM
```

### 版本兼容性

| vSphere 版本 | 支持状态 | 说明 |
|-------------|---------|------|
| 8.0 / 8.0U1-U3 | ✅ 完全支持 | `CreateSnapshot_Task` 已弃用，推荐 `CreateSnapshotEx_Task` |
| 7.0 / 7.0U1-U3 | ✅ 完全支持 | 所有 API 正常工作 |
| 6.7 | ✅ 兼容 | 向后兼容，已测试 |
| 6.5 | ✅ 兼容 | 向后兼容，已测试 |

> pyVmomi 在 SOAP 握手阶段自动协商 API 版本，无需手动配置。同一套代码可同时管理 7.0 和 8.0 环境。

---

## 常用工作流

### 部署实验环境

1. 浏览数据存储查找 OVA 镜像 → `vmware-aiops datastore browse <ds> --pattern "*.ova"`
2. 从 OVA 部署 VM → `vmware-aiops deploy ova ./image.ova --name lab-vm --datastore ds1`
3. 在 VM 内安装软件 → `vmware-aiops vm guest-exec lab-vm --cmd /bin/bash --args "-c 'apt-get install -y nginx'" --user root`
4. 创建基线快照 → `vmware-aiops vm snapshot-create lab-vm --name baseline`
5. 设置 TTL 自动清理 → `vmware-aiops vm set-ttl lab-vm --minutes 480`

### 批量克隆测试

1. 创建计划：`vm_create_plan`，包含多个克隆 + 配置步骤
2. 审查计划（展示受影响的 VM、不可逆操作警告）
3. 执行：`vm_apply_plan` 顺序执行，失败即停止
4. 如失败：`vm_rollback_plan` 逆序撤销已执行步骤
5. 对所有克隆设置 TTL 自动清理

### 迁移 VM 到另一台主机

1. 通过 `vmware-monitor` 查看 VM 信息 → 确认电源状态和当前主机
2. 迁移：`vmware-aiops vm migrate my-vm --to-host esxi-02`
3. 验证迁移完成

---

## 虚拟机生命周期

| 操作 | 命令 | 确认 | vCenter | ESXi |
|------|------|:----:|:-------:|:----:|
| 开机 | `vm power-on <name>` | — | ✅ | ✅ |
| 优雅关机 | `vm power-off <name>` | 双重 | ✅ | ✅ |
| 强制关机 | `vm power-off <name> --force` | 双重 | ✅ | ✅ |
| 重置 | `vm reset <name>` | — | ✅ | ✅ |
| 挂起 | `vm suspend <name>` | — | ✅ | ✅ |
| 创建 | `vm create <name> --cpu --memory --disk` | — | ✅ | ✅ |
| 删除 | `vm delete <name>` | 双重 | ✅ | ✅ |
| 调整配置 | `vm reconfigure <name> --cpu --memory` | 双重 | ✅ | ✅ |
| 创建快照 | `vm snapshot-create <name> --name <snap>` | — | ✅ | ✅ |
| 列出快照 | `vm snapshot-list <name>` | — | ✅ | ✅ |
| 恢复快照 | `vm snapshot-revert <name> --name <snap>` | — | ✅ | ✅ |
| 删除快照 | `vm snapshot-delete <name> --name <snap> [--no-wait]` | — | ✅ | ✅ |
| 任务状态 | `vm task-status <task-id>` | — | ✅ | ✅ |
| 克隆 | `vm clone <name> --new-name <new>` | — | ✅ | ✅ |
| 迁移 | `vm migrate <name> --to-host <host>` | — | ✅ | ❌ |
| **设置 TTL** | `vm set-ttl <name> --minutes <n>` | — | ✅ | ✅ |
| **取消 TTL** | `vm cancel-ttl <name>` | — | ✅ | ✅ |
| **列出 TTL** | `vm list-ttl` | — | ✅ | ✅ |
| **Clean Slate** | `vm clean-slate <name> [--snapshot baseline]` | 双重 | ✅ | ✅ |
| **Guest 执行** | `vm guest-exec <name> --cmd /bin/bash --args "..."` | — | ✅ | ✅ |
| **Guest 执行（含输出）** | `vm guest-exec-output <name> --cmd "df -h"` | — | ✅ | ✅ |
| **Guest 上传** | `vm guest-upload <name> --local f.sh --guest /tmp/f.sh` | — | ✅ | ✅ |
| **Guest 下载** | `vm guest-download <name> --guest /var/log/syslog --local ./syslog` | — | ✅ | ✅ |

> Guest Operations 需要 VM 内运行 VMware Tools。`guest-exec-output` 自动检测 Linux/Windows shell 并捕获 stdout/stderr。

### Plan → Apply（多步操作编排）

当操作涉及 2+ 步骤或 2+ 台 VM 时，自动使用 plan/apply 工作流：

| 步骤 | 说明 |
|------|------|
| 1. **创建 Plan** | AI 调用 `vm_create_plan` — 校验操作、检查 vSphere 中目标是否存在、生成带回滚信息的 plan |
| 2. **审查** | AI 展示 plan 给用户：步骤、影响的 VM、不可逆操作警告 |
| 3. **执行** | `vm_apply_plan` 按顺序执行；某步失败立即停止 |
| 4. **回滚**（如失败） | 询问用户是否回滚，`vm_rollback_plan` 逆序撤销已执行步骤（不可逆操作跳过） |

Plan 存储在 `~/.vmware-aiops/plans/`，成功后自动删除，超过 24 小时自动清理。

## VM 部署与制备

| 操作 | 命令 | 速度 | vCenter | ESXi |
|------|------|:----:|:-------:|:----:|
| OVA 部署 | `deploy ova <path> --name <vm>` | 分钟级 | ✅ | ✅ |
| 模板部署 | `deploy template <tmpl> --name <vm>` | 分钟级 | ✅ | ✅ |
| 链接克隆 | `deploy linked-clone --source <vm> --snapshot <snap> --name <new>` | 秒级 | ✅ | ✅ |
| 挂载 ISO | `deploy iso <vm> --iso "[ds] path/to.iso"` | 即时 | ✅ | ✅ |
| 转为模板 | `deploy mark-template <vm>` | 即时 | ✅ | ✅ |
| 批量克隆 | `deploy batch-clone --source <vm> --count <n>` | 分钟级 | ✅ | ✅ |
| 批量部署 (YAML) | `deploy batch spec.yaml` | 自动 | ✅ | ✅ |

## 集群管理

| 操作 | 命令 | 确认 | vCenter | ESXi |
|------|------|:----:|:-------:|:----:|
| 集群信息 | `cluster info <name>` | — | ✅ | ❌ |
| 创建集群 | `cluster create <name> [--ha] [--drs]` | — | ✅ | ❌ |
| 删除集群 | `cluster delete <name>` | 双重 | ✅ | ❌ |
| 添加主机 | `cluster add-host <cluster> --host <host>` | 双重 | ✅ | ❌ |
| 移除主机 | `cluster remove-host <cluster> --host <host>` | 双重 | ✅ | ❌ |
| 配置 HA/DRS | `cluster configure <name> [--ha/--no-ha] [--drs/--no-drs]` | 双重 | ✅ | ❌ |

> `remove-host` 要求主机先进入**维护模式**；主机会被移出集群，作为独立主机放入数据中心的主机文件夹。

## 告警管理

| 操作 | 命令 | 确认 | vCenter | ESXi |
|------|------|:----:|:-------:|:----:|
| 列出已触发告警 | `alarm list [--target <t>]` | — | ✅ | ❌ |
| 确认告警 | `alarm acknowledge <entity> <alarm>` | — | ✅ | ❌ |
| 清除（重置）告警 | `alarm reset <entity> <alarm>` | 双重 | ✅ | ❌ |

> **影响范围**：vSphere 没有按单个告警清除的 API。`alarm reset` 使用 `AlarmManager.ClearTriggeredAlarms`，会清除与指定告警的实体类型（host/VM/all）和当前状态（red/yellow）匹配的**所有**已触发告警，而不仅是指定的那一条。命令会先查找指定告警（名称写错会立即失败，不会清除任何告警），输出中的 `scope` 字段报告实际清除范围。若底层问题仍存在，被清除的告警会自动重新触发。

## 数据存储浏览

| 功能 | vCenter | ESXi | 说明 |
|------|:-------:|:----:|------|
| 浏览文件 | ✅ | ✅ | 列出数据存储中任意路径的文件/文件夹 |
| 扫描镜像 | ✅ | ✅ | 发现所有数据存储中的 ISO、OVA、OVF、VMDK 文件 |

## 定时扫描与通知

| 功能 | 说明 |
|------|------|
| 守护进程 | 基于 APScheduler，可配置间隔（默认 15 分钟） |
| 多目标扫描 | 依次扫描所有配置的 vCenter/ESXi 目标 |
| 日志分析 | 正则匹配：error, fail, critical, panic, timeout, corrupt |
| 结构化日志 | JSONL 输出到 `~/.vmware-aiops/scan.log` |
| Webhook 通知 | 支持 Slack、Discord 或任意 HTTP 端点 |

## 安全特性

| 功能 | 说明 |
|------|------|
| 预演模式（Dry-Run） | 任何破坏性命令加 `--dry-run` 可预览 API 调用而不执行，便于信任验证 |
| Plan → Confirm → Execute → Log | 结构化工作流：展示当前状态、确认变更、执行、审计日志 |
| 双重确认 | 所有破坏性操作（关机、删除、配置变更、快照恢复/删除、克隆、迁移）需连续两次确认，无绕过参数 |
| 拒绝记录 | 用户拒绝的操作也会记录到审计日志，便于安全审计 |
| 审计日志 | 所有操作记录到 `~/.vmware-aiops/audit.log`（JSONL），包含操作前后状态 |
| 输入校验 | VM 名称长度/格式、CPU（1-128）、内存（128-1048576 MB）、磁盘（1-65536 GB）参数校验 |
| 密码保护 | 通过 `.env` 加载密码并检查文件权限（warn if not 600），不出现在 shell 历史 |
| 配置文件内容 | `config.yaml` 仅存储主机名、端口和 `.env` 引用路径，**不含密码或 Token** |
| SSL 自签名 | 仅用于 ESXi 自签名证书的隔离实验环境；生产环境应使用 CA 签名证书 |
| Prompt 注入防护 | vSphere 事件消息和主机日志在输出前进行截断、控制字符清理和边界标记包裹 |
| Webhook 数据范围 | **默认禁用**。启用后仅向用户自配置的 URL 发送告警摘要，payload 不含凭据、IP 或 PII |
| 最小权限 | 推荐使用专用 vCenter 服务账户，仅授予所需最小权限。仅需监控时使用 [VMware-Monitor](https://github.com/vmware-skills/VMware-Monitor) |
| 任务等待 | 所有异步操作等待完成并报告结果 |

### vCenter vs ESXi 对比

| 功能 | vCenter | ESXi 独立模式 |
|------|:-------:|:----:|
| vMotion 迁移 | ✅ | ❌ |
| 跨主机克隆 | ✅ | ❌ |
| 集群管理 | ✅ | ❌ |
| 所有 VM 生命周期操作 | ✅ | ✅ |
| OVA/模板/链接克隆部署 | ✅ | ✅ |
| 数据存储浏览和扫描 | ✅ | ✅ |
| 快照 | ✅ | ✅ |
| Guest 操作 | ✅ | ✅ |

> 资源清单、告警、事件、传感器、主机服务、扫描已迁移至 [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)。

---

## 故障排除

### "VM not found" 错误
vSphere 中 VM 名称区分大小写。请使用 `vmware-monitor inventory vms` 获取准确名称。

### Guest exec 返回空输出
使用 `vm_guest_exec_output` 而非 `vm_guest_exec` — 前者自动捕获 stdout/stderr。基础版 `vm_guest_exec` 仅返回退出码。

### 部署 OVA 超时
大型 OVA 文件（>10GB）可能超过默认 120 秒超时。上传通过 HTTP NFC lease 进行 — 确保运行 vmware-aiops 的机器与 ESXi 之间网络稳定。

### Plan 执行中途失败
运行 `vmware-aiops plan list` 查看失败的 plan 状态。询问用户是否使用 `vm_rollback_plan` 回滚。不可逆步骤（delete_vm）在回滚时会跳过。

### 连接被拒 / SSL 错误
1. 验证目标可达：`vmware-aiops doctor`
2. 自签名证书：在 config.yaml 中设置 `disableSslCertValidation: true`（仅限实验环境）

---

## 支持的 AI 平台

| 平台 | 状态 | 配置文件 | AI 模型 |
|------|------|---------|---------|
| **Claude Code** | ✅ 原生技能 | `skills/vmware-aiops/SKILL.md` | Anthropic Claude |
| **Gemini CLI** | ✅ 上下文文件 + MCP | `skills/vmware-aiops/SKILL.md` | Google Gemini |
| **OpenAI Codex CLI** | ✅ AGENTS.md | `skills/vmware-aiops/SKILL.md` | OpenAI GPT |
| **Aider** | ✅ 约定文件 | `skills/vmware-aiops/SKILL.md` | 任意（云端 + 本地） |
| **Continue CLI** | ✅ 规则文件 | `skills/vmware-aiops/SKILL.md` | 任意（云端 + 本地） |
| **Trae IDE** | ✅ Rules | `skills/vmware-aiops/SKILL.md` | Claude/DeepSeek/GPT-4o/Doubao |
| **Kimi Code CLI** | ✅ Skill | `skills/vmware-aiops/SKILL.md` | Moonshot Kimi |
| **MCP Server** | ✅ MCP 协议 | `vmware_aiops/mcp_server/` | 任意 MCP 客户端 |
| **Python CLI** | ✅ 独立运行 | N/A | N/A |

### MCP Server 集成（本地 Agent）

vmware-aiops MCP Server 可接入**任何 MCP 兼容的 Agent 或工具**。配置模板见 [`examples/mcp-configs/`](examples/mcp-configs/)。

| Agent / 工具 | 本地模型支持 | 配置模板 | 集成指南 |
|-------------|:----------:|---------|---------|
| **[Goose](https://github.com/block/goose)** | ✅ Ollama, LM Studio | [`goose.json`](examples/mcp-configs/goose.json) | [指南](docs/integrations/goose.md) |
| **[LocalCowork](https://github.com/Liquid4All/localcowork)** | ✅ 完全离线 | [`localcowork.json`](examples/mcp-configs/localcowork.json) | [指南](docs/integrations/localcowork.md) |
| **[mcp-agent](https://github.com/lastmile-ai/mcp-agent)** | ✅ Ollama, vLLM | [`mcp-agent.yaml`](examples/mcp-configs/mcp-agent.yaml) | [指南](docs/integrations/mcp-agent.md) |
| **VS Code Copilot** | — | [`vscode-copilot.json`](examples/mcp-configs/vscode-copilot.json) | [指南](docs/integrations/vscode-copilot.md) |
| **Cursor** | — | [`cursor.json`](examples/mcp-configs/cursor.json) | — |
| **Continue** | ✅ Ollama | [`continue.yaml`](examples/mcp-configs/continue.yaml) | [指南](docs/integrations/continue.md) |
| **Claude Code** | — | [`claude-code.json`](examples/mcp-configs/claude-code.json) | — |

**完全本地运行**（无需云端 API）：

```bash
# Aider + Ollama + vmware-aiops（通过 SKILL.md）
aider --conventions skills/vmware-aiops/SKILL.md --model ollama/qwen2.5-coder:32b
```

---

## 安装

### 第 0 步：前置条件

```bash
python3 --version   # 需要 Python 3.10+
node --version      # Gemini/Codex CLI 需要 Node.js 18+
```

### 第 1 步：安装 Python 后端

所有平台共用同一个 Python 后端：

```bash
git clone https://github.com/vmware-skills/VMware-AIops.git
cd VMware-AIops
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 第 2 步：配置

```bash
mkdir -p ~/.vmware-aiops
cp config.example.yaml ~/.vmware-aiops/config.yaml
# 编辑 config.yaml，填入你的 vCenter/ESXi 目标信息
```

通过 `.env` 文件设置密码（推荐）：

```bash
# 使用模板创建 .env 文件
cp .env.example ~/.vmware-aiops/.env

# 编辑并填入真实密码
# 然后锁定文件权限（仅所有者可读写）
chmod 600 ~/.vmware-aiops/.env
```

> **安全提示**：推荐使用 `.env` 文件而非命令行 `export`，避免密码出现在 shell 历史记录中。`.env` 文件必须设置 `chmod 600`（仅所有者可读写）。

密码环境变量命名规则：`VMWARE_{目标名大写}_PASSWORD`
- 连字符替换为下划线，全大写
- 目标 `home-esxi` → `VMWARE_HOME_ESXI_PASSWORD`
- 目标 `prod-vcenter` → `VMWARE_PROD_VCENTER_PASSWORD`

### 安全最佳实践

- **绝不**在脚本或配置文件中硬编码密码
- **绝不**通过命令行参数传递密码（`ps` 命令可见）
- **绝不**在输出或日志中显示密码
- **始终**使用 `~/.vmware-aiops/.env` 并设置 `chmod 600`
- **始终**通过 `config.yaml` 配置连接 — 凭据自动从 `.env` 加载
- **TLS**：默认启用。仅在使用自签名证书的隔离实验环境中才禁用
- **Webhook**：仅向您自己配置的 URL 发送通知，默认不向第三方服务发送数据
- **代码审查**：建议在生产部署前审查[源代码](https://github.com/vmware-skills/VMware-AIops)和提交历史
- **生产环境安全**：生产环境建议使用只读的 [VMware-Monitor](https://github.com/vmware-skills/VMware-Monitor)。AI Agent 可能误解上下文并执行非预期的破坏性操作 — 已有真实案例表明，缺乏隔离的 AI 驱动基础设施工具可能删除生产数据库和整个环境。VMware-Monitor 在代码级别消除此风险：代码库中不存在任何破坏性函数

### 第 3 步：连接 AI 工具

#### Claude Code（推荐）

```bash
# 方式一：由安装器放置 skill（推荐）
npx skills add vmware-skills/VMware-AIops
# 或
clawhub install @zw008/vmware-aiops

# 方式二：手工安装 skill
git clone https://github.com/vmware-skills/VMware-AIops.git
cd VMware-AIops
mkdir -p ~/.claude/skills/vmware-aiops
cp -r skills/vmware-aiops/. ~/.claude/skills/vmware-aiops/
```

若需工具调用（而非仅 skill 上下文），再注册 MCP Server：

```bash
claude mcp add vmware-aiops -- vmware-aiops mcp
```

> 生产环境建议改用只读的 [VMware-Monitor](https://github.com/vmware-skills/VMware-Monitor)（`npx skills add vmware-skills/VMware-Monitor`），代码级零破坏性。

#### Gemini CLI

```bash
npm install -g @google/gemini-cli
# Gemini CLI 启动时读取 GEMINI.md 作为项目上下文
cp skills/vmware-aiops/SKILL.md ./GEMINI.md
gemini
> 显示 ESXi 上所有虚拟机
```

若需工具调用（而非仅上下文），在 `~/.gemini/settings.json` 注册 MCP Server：

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

#### Codex CLI

```bash
npm i -g @openai/codex
mkdir -p ~/.codex/skills/vmware-aiops
cp skills/vmware-aiops/SKILL.md ~/.codex/skills/vmware-aiops/SKILL.md
cp skills/vmware-aiops/SKILL.md ./AGENTS.md
codex --enable skills
```

#### Aider（支持本地模型）

```bash
pip install aider-chat
# 云端
aider --conventions skills/vmware-aiops/SKILL.md
# 本地 Ollama
aider --conventions skills/vmware-aiops/SKILL.md --model ollama/qwen2.5-coder:32b
```

#### Trae IDE

将规则文件复制到项目的 `.trae/rules/` 目录：

```bash
mkdir -p .trae/rules
cp skills/vmware-aiops/SKILL.md .trae/rules/project_rules.md
```

Trae IDE 的 Builder Mode 会在启动时自动读取 `.trae/rules/` 下的 Markdown 文件。

> 注意：也可以在 Trae IDE 中安装 Claude Code VS Code 扩展，直接使用 `.claude/skills/` 格式。

#### Kimi Code CLI

```bash
# 复制技能文件到 Kimi skills 目录
mkdir -p ~/.kimi/skills/vmware-aiops
cp skills/vmware-aiops/SKILL.md ~/.kimi/skills/vmware-aiops/SKILL.md
```

#### MCP 服务器（Smithery / Glama / Claude Desktop）

MCP 服务器通过 [Model Context Protocol](https://modelcontextprotocol.io) 将 VMware 操作暴露为工具，兼容所有 MCP 客户端（Claude Desktop、Cursor 等）。

**v1.5.15+ 推荐方式**：完成 `uv tool install vmware-aiops` 后，**一条命令启动 MCP**：

```bash
# 推荐 — 单命令，无网络依赖
vmware-aiops mcp

# 指定配置路径
VMWARE_AIOPS_CONFIG=/path/to/config.yaml vmware-aiops mcp
```

**Claude Desktop 配置** (`claude_desktop_config.json`)：
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
<summary>备选方案：uvx（不安装）或 legacy 入口</summary>

```bash
# 不想安装，临时运行（每次需要联网 resolve 依赖）
uvx --from vmware-aiops vmware-aiops mcp

# 旧 entry point（仍可用，向后兼容）
vmware-aiops-mcp
```

> **公司 TLS 代理网络下？** uvx 可能报 `invalid peer certificate: UnknownIssuer`。
> 推荐使用上面的 `vmware-aiops mcp`（无需联网），或 `export UV_NATIVE_TLS=true`。

</details>


---

#### 独立 CLI（无需 AI）

```bash
source .venv/bin/activate
vmware-aiops vm power-on my-vm --target home-esxi
vmware-aiops deploy ova ./ubuntu.ova --name my-vm --target home-esxi
vmware-aiops datastore browse datastore1 --target home-esxi
```

---

## 国内云端模型

| 模型 | 说明 | 配合工具 |
|------|------|---------|
| DeepSeek | 性价比高，编程能力强 | Aider / Continue |
| 通义千问 Qwen | 阿里云，有免费额度 | Aider / Continue |
| 豆包 Doubao | 字节跳动 | Aider / Trae IDE |

```bash
# DeepSeek
export DEEPSEEK_API_KEY="your-key"
aider --conventions skills/vmware-aiops/SKILL.md --model deepseek/deepseek-coder

# 通义千问
export DASHSCOPE_API_KEY="your-key"
aider --conventions skills/vmware-aiops/SKILL.md --model qwen/qwen-coder-plus
```

---

## 本地模型（Aider + Ollama）

完全离线运行，无需云端 API，完全隐私：

```bash
brew install ollama              # macOS
ollama pull qwen2.5-coder:32b   # 下载模型（~20GB）
ollama serve                     # 启动服务

aider --conventions skills/vmware-aiops/SKILL.md --model ollama/qwen2.5-coder:32b
```

---

## CLI 命令参考

```bash
# 环境诊断
vmware-aiops doctor                   # 检查环境、配置、连通性
vmware-aiops doctor --skip-auth       # 跳过 vSphere 认证检查（更快）

# MCP 配置生成
vmware-aiops mcp-config generate --agent goose        # 生成 Goose 配置
vmware-aiops mcp-config generate --agent claude-code  # 生成 Claude Code 配置
vmware-aiops mcp-config list                          # 列出所有支持的 Agent

# 虚拟机操作
vmware-aiops vm power-on|power-off|reset|suspend <vm-name>
vmware-aiops vm create <name> --cpu 4 --memory 8192 --disk 100
vmware-aiops vm delete <name> --confirm
vmware-aiops vm reconfigure <name> --cpu 4 --memory 8192
vmware-aiops vm snapshot-create|snapshot-list|snapshot-revert|snapshot-delete <name>
vmware-aiops vm snapshot-delete <name> --name <snap> --no-wait   # 异步发起，立即返回 task id
vmware-aiops vm task-status <task-id>                            # 轮询异步任务状态
vmware-aiops vm clone <name> --new-name <new>
vmware-aiops vm migrate <name> --to-host <host>
vmware-aiops vm set-ttl <name> --minutes 60     # 60 分钟后自动删除
vmware-aiops vm cancel-ttl <name>              # 取消 TTL
vmware-aiops vm list-ttl                       # 查看所有 TTL
vmware-aiops vm clean-slate <name> --snapshot baseline  # 恢复基线快照（双重确认）

# Guest Operations（需要 VMware Tools）
vmware-aiops vm guest-exec my-vm --cmd /bin/bash --args "-c 'whoami'" --user root
vmware-aiops vm guest-upload my-vm --local ./script.sh --guest /tmp/script.sh --user root
vmware-aiops vm guest-download my-vm --guest /var/log/syslog --local ./syslog.txt --user root

# Plan → Apply（多步操作编排）
vmware-aiops plan list                                # 查看待执行/失败的 plan

# 部署
vmware-aiops deploy ova ./ubuntu.ova --name my-vm --datastore ds1      # 从 OVA 部署
vmware-aiops deploy template golden-ubuntu --name new-vm               # 从模板部署
vmware-aiops deploy linked-clone --source base-vm --snapshot clean --name test-vm  # 链接克隆（秒级）
vmware-aiops deploy iso my-vm --iso "[datastore1] iso/ubuntu-22.04.iso"  # 挂载 ISO
vmware-aiops deploy mark-template golden-vm                            # 转为模板
vmware-aiops deploy batch-clone --source base-vm --count 5 --prefix lab  # 批量克隆
vmware-aiops deploy batch deploy.yaml                                  # 从 YAML 批量部署

# 集群
vmware-aiops cluster info my-cluster                                   # 集群详情
vmware-aiops cluster create my-cluster --ha --drs                      # 创建集群
vmware-aiops cluster delete my-cluster                                 # 删除集群（双重确认）
vmware-aiops cluster add-host my-cluster --host esxi-03                # 添加主机（双重确认）
vmware-aiops cluster remove-host my-cluster --host esxi-03             # 移除主机（双重确认）
vmware-aiops cluster configure my-cluster --ha --drs                   # 配置 HA/DRS（双重确认）

# 告警管理
vmware-aiops alarm list                                                # 列出已触发告警
vmware-aiops alarm acknowledge esxi-01 "Host memory usage"             # 确认告警
vmware-aiops alarm reset esxi-01 "Host memory usage"                   # 清除告警（双重确认；清除所有匹配实体类型+状态的告警）

# 数据存储（浏览和扫描镜像保留在 aiops；iSCSI/vSAN 已迁移至 vmware-storage）
vmware-aiops datastore browse datastore1 --path "iso/"                 # 浏览数据存储
vmware-aiops datastore scan-images --target home-esxi                  # 扫描所有数据存储的镜像

# 扫描与守护进程
vmware-aiops scan now [--target <name>]
vmware-aiops daemon start|stop|status

# 配套技能负责的操作：
#   vmware-monitor: 资源清单、告警、事件、传感器
#   vmware-storage: 数据存储管理、iSCSI、vSAN
#   vmware-vks:     Tanzu/TKC 集群生命周期
```

---

## 项目结构

```
VMware-AIops/
├── skills/                        # Skills 索引（npx skills add）
│   └── vmware-aiops/
│       ├── SKILL.md               # 精简版技能（渐进式展开）
│       └── references/            # 按需加载的详细文档
│           ├── capabilities.md    # 完整功能表格
│           ├── cli-reference.md   # 完整 CLI 参考
│           └── setup-guide.md     # 安装、安全、AI 平台
├── vmware_aiops/                  # Python 后端
│   ├── config.py                  # 配置管理
│   ├── connection.py              # 多目标连接（pyVmomi）
│   ├── cli/                       # Typer CLI（双重确认）
│   ├── ops/                       # 运维操作
│   │   ├── inventory.py           # VM、主机、数据存储、集群
│   │   ├── health.py              # 告警、事件、传感器
│   │   ├── vm_lifecycle.py        # VM 生命周期管理
│   │   ├── vm_deploy.py           # OVA、模板、链接克隆、批量部署
│   │   └── datastore_browser.py   # 数据存储浏览、镜像发现
│   ├── scanner/                   # 日志扫描守护进程
│   ├── notify/                    # 通知（JSONL + Webhook）
│   └── mcp_server/                # MCP 服务器
│       ├── server.py              # MCP 工具定义
│       └── __main__.py            # 入口
├── examples/mcp-configs/          # MCP 客户端配置模板
├── tests/                         # 测试
├── smithery.yaml                  # Smithery 市场配置
├── config.example.yaml
└── pyproject.toml
```

## 相关项目

| Skill | 范围 | 工具数 | 安装 |
|-------|------|:-----:|------|
| **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** | VM 生命周期、部署、Guest Ops、集群、数据存储浏览、健康速览 | 49 | `uv tool install vmware-aiops` |
| **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | 只读监控、告警、事件、对象调查 bundle | 27 | `uv tool install vmware-monitor` |
| **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | 数据存储、iSCSI、vSAN | 11 | `uv tool install vmware-storage` |
| **[vmware-vks](https://github.com/vmware-skills/VMware-VKS)** | Tanzu 命名空间、TKC 集群生命周期 | 20 | `uv tool install vmware-vks` |
| **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX 网络：Segment、Gateway、NAT、路由、IPAM | 33 | `uv tool install vmware-nsx-mgmt` |
| **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW 策略/规则、安全组、Traceflow、IDS/IPS | 21 | `uv tool install vmware-nsx-security` |
| **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Operations 指标、告警、容量、异常检测 | 28 | `uv tool install vmware-aria` |
| **[vmware-avi](https://github.com/vmware-skills/VMware-AVI)** | AVI (NSX ALB) 负载均衡、AKO Kubernetes 操作 | 28 | `uv tool install vmware-avi` |
| **[vmware-harden](https://github.com/vmware-skills/VMware-Harden)** | 合规基线（CIS / vSphere SCG / 等保 / PCI-DSS）、Drift 检测 | 6 | `uv tool install vmware-harden` |

---

## 问题反馈与贡献

如果遇到任何报错或问题，请将错误信息、日志或截图发送至 **zhouwei008@gmail.com**。欢迎加入我们，一起维护和改进这个项目！

If you encounter any errors or issues, please send the error message, logs, or screenshots to **zhouwei008@gmail.com**. Contributions are welcome — feel free to join us in maintaining and improving this project!

## 许可证

MIT
