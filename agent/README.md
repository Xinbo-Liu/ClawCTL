# Agent 统一治理目录

`agent/` 是 agent plane 的正式治理入口。该目录维护治理规则、共享 control-plane 对象真源，以及仓内 extension authoring 所需的治理合同。

## 目录边界

- `agent/governance/`：治理规则与仓内 extension authoring 合同。
- `agent/control_plane/`：共享 registry、runtime 与 object policy 真源。

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

1. `governance/baseline.md`
2. `governance/directory-standard.md`
3. `governance/source-of-truth-matrix.md`
4. `governance/repository-surface-governance.md`
5. `governance/module-governance.md`
6. `governance/group-governance.md`
7. `governance/group-membership-governance.md`
8. `governance/group-topology-governance.md`
9. `governance/group-recovery-governance.md`
10. `governance/job-operation-bridge.md`
11. `governance/job-contract-governance.md`
12. `governance/implementation-binding-governance.md`
13. `governance/lifecycle-governance.md`
