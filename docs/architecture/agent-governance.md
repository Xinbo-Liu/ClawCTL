# Agent 治理

本页解释 agent plane 如何连接平台配置、共享对象、扩展和治理规则。具体作者合同以 [Agent 治理目录](../../agent/README.md) 为准。

## 结构

1. `config/control_plane/` 负责 base service、正式 profile 与正式 extension 入口。
2. `agent/control_plane/` 是共享 registry、runtime 与 object policy 真源。
3. `agent/governance/` 负责平台治理规则与仓内 extension authoring 合同。
4. `config/workspace_templates/` 负责工作区模板基座与模板本体。
5. `python/openclaw/control_plane/` 与 `scripts/` 负责装配、执行、校验与对外命令。

## 正式口径

- `config/control_plane/profiles/agent_platform.service.json` 是唯一正式默认运行 profile。
- 默认运行面不启用业务扩展；仓内受管扩展通过显式选择接入。
- 仓内 extension 只能通过正式 profile、有效自动发现 profile 或仓内合同 service 的显式 `--config-path` 进入运行面。
- 扩展包共享对象通过 `activation.enabledExtensionIds` 声明归属，且该字段必须只包含自身 extension id；通过 `--extension <id>` 解决 owner-aware surface 的歧义读取。
- 显式扩展包的标准挂载方式、目录结构与默认模型/渠道绑定规则统一见 [explicit-extension-packages.md](explicit-extension-packages.md)。

## 回链

- [治理基线](../../agent/governance/baseline.md)：默认运行选择和扩展作者边界。
- [真源矩阵](../../agent/governance/source-of-truth-matrix.md)：各类配置、合同和派生视图的唯一来源。
- [仓库结构治理](../../agent/governance/repository-surface-governance.md)：实现、模块和测试的归属。
- [模块治理](../../agent/governance/module-governance.md)：模块资产、运行入口和测试真源。
- [Group 治理](../../agent/governance/group-governance.md)：成员、拓扑、重试与恢复接受。
