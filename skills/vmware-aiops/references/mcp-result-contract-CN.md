# 公共结果契约（未发布迁移）

依赖 Policy 1.26.0。失败的 MCP 调用在 `structuredContent.vmware_error` 中提供
字符串 error 和字符串或 null 的 hint；保留原文本及结构化业务字段。
成功值、工具参数/默认值、Policy/RBAC、审计和确认行为不变。

支持 offset 的工具把 next_offset 作为下一次 offset，null 时停止。
本阶段保留旧字段名、标记语义、hint 和可选字段的出现规则；无 offset 的工具和
cursor API 保留各自读取机制，不静默重定义 has_more/truncated，也不复制行数据别名。

先发布 Policy，再刷新本仓锁文件、验证打包依赖，之后才可合并/发布。
源码覆盖测试不等于发布。
