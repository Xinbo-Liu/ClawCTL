# Agent 统一治理目录

`agent/` 是 agent plane 的正式治理入口。该目录维护治理规则、共享 control-plane 对象真源，以及仓内 extension authoring 所需的治理合同。

## 目录边界

- `agent/governance/`：治理规则与仓内 extension authoring 合同。
- `agent/control_plane/`：共享 registry、runtime 与 object policy 真源。
- [extensions/README.md](extensions/README.md)：受管扩展索引、业务说明和接入合同。

运行对象、产物策略和路径由 [运行产物参考](../docs/operations/runtime-artifacts-reference.md) 承接；本页只导航作者合同和治理规则。

## 正式入口

- base service：`config/control_plane/service.json`
- 正式默认运行 profile：`config/control_plane/profiles/agent_platform.service.json`
- 正式平台扩展：`config/control_plane/extensions.d/agent_platform.json`

共享 job、group、model、target 对象统一通过 `activation.enabledExtensionIds` 声明归属。主仓库提供共享 registry / runtime / object policy 真源；formal module、group、domain 与业务对象由仓内 extension 按正式 profile、有效自动发现 profile 或仓内合同 service 的显式 `--config-path` 接入。

## 口径

- `config/control_plane/service.json` 只承担 kernel / base 基线。
- `config/control_plane/profiles/agent_platform.service.json` 承担正式部署、运行验收、交付导出与运维脚本的默认运行入口。
- `agent/control_plane/` 是共享 agent-plane 真源，只保存跨扩展复用的 registry / runtime / object policy。
- 主仓库的共享资产集中在 registry / runtime / object policy；formal module、group 与 domain 由仓内 extension 提供。
- 单个 profile 通过 `extensions.manifestsDirs` 支持跨多个仓内合同 manifest 目录组合多个 extension；扩展包共享对象的 `activation.enabledExtensionIds` 必须且只能等于自身 extension id。
- extension manifest 运行时来源固定为当前仓库内的 `config/control_plane/extensions.d/agent_platform.json` 或 `agent/extensions/<extension-id>/config/control_plane/extensions.d/<extension-id>.json`；任何可见且带 `id` 的 manifest 都要通过严格合同校验，不接受任意仓外 manifest 目录。
- 读取 extension owner surface 时，`control-plane objects`、`dispatch ops`、`dispatch observability`、`control-plane recovery`、`control-plane routes` 与 `control-plane diagnostics` 在同名冲突时必须显式传 `--extension <id>`。
- 显式扩展包的标准目录、组合 service、默认模型与 target binding 规则见 [../docs/architecture/explicit-extension-packages.md](../docs/architecture/explicit-extension-packages.md)。

## 阅读顺序

1. [治理基线](governance/baseline.md)、[目录标准](governance/directory-standard.md)、[真源矩阵](governance/source-of-truth-matrix.md)：确认默认运行面和作者边界。
2. [仓库结构治理](governance/repository-surface-governance.md)、[Python 面治理](governance/python-surface-governance.md)、[领域治理](governance/domain-governance.md)：选择实现的归属位置。
3. [模块治理](governance/module-governance.md)：组织模块清单、能力边界、入口与测试。
4. [Group 治理](governance/group-governance.md)、[成员归属](governance/group-membership-governance.md)、[主链拓扑](governance/group-topology-governance.md)、[恢复真源](governance/group-recovery-governance.md)：管理组合运行与补偿。
5. [Job / Operation 绑定](governance/job-operation-bridge.md)、[Job 合同](governance/job-contract-governance.md)、[Implementation 绑定](governance/implementation-binding-governance.md)：核对运行对象如何从模块派生。
6. [生命周期治理](governance/lifecycle-governance.md)：确认真源同步项；项目级阶段顺序见 [生命周期架构](../docs/architecture/agent-lifecycle-governance.md)。

共享对象职责见 [control_plane/README.md](control_plane/README.md)，身份与权限概念见 [智能体身份与权限](../docs/architecture/agents-and-permissions.md)。
