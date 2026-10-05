# 受管扩展目录

`agent/extensions/` 是平台基座的受管扩展接入目录。扩展可按统一合同承接公共通道或具体业务能力，自行维护模块、对象、Python 实现、依赖与说明；基座提供装配、调度、诊断和验收机制。本页说明接入边界，并导航到仓内实际可用扩展。

## 接入与边界

- 默认运行面为 `base + agent_platform`。仓内存在扩展不代表默认启用，运行组合由所选 profile 或仓内合同 service 决定。
- 扩展根固定为 `agent/extensions/<extension-id>/`。扩展贡献的 registry、surface、对象和实现保留在自身 owner 根目录内。
- 默认扩展 service 启用 `agent_platform`、自身及递归 required dependencies，并加载这些 owner 的合同 manifest 目录；optional dependencies 不进入默认启用集合。
- 组合 profile 通过 [组合登记](../../config/control_plane/repo_combination_profiles.json) 精确声明启用集合和共享部署输入，不改变各扩展对象的 owner。
- 模块资产与测试按 [模块治理](../governance/module-governance.md) 维护；扩展最小结构、自动发现、manifest 字段和 callable 边界以 [扩展挂载指南](../../docs/architecture/explicit-extension-packages.md) 为准。
- 有外部 Python runtime 依赖时，以 `requirements.lock` 和 `offline_wheelhouse/` 提供匹配的离线真源，部署时同步 wheelhouse 并准备 venv。

## 仓内扩展

下表依据 [扩展索引](index.json) 的受管登记与有效扩展目录自动发现结果生成。具体价值、配置与验收按各扩展 README 阅读；正式运行入口和组合分别见 [profile registry](../../config/control_plane/profile_registry.tsv) 与 [组合登记](../../config/control_plane/repo_combination_profiles.json)。

<!-- BEGIN managed-extension-index -->

| 扩展 ID | 名称 | 包内说明 |
|---------|------|----------|

当前仓库没有可列出的受管扩展。

<!-- END managed-extension-index -->

## 真源与操作

- [index.json](index.json)：显式登记 owner、扩展根、service、manifest 目录和 Python root。
- [profile registry](../../config/control_plane/profile_registry.tsv)：把正式 profile 映射到唯一 service；有效自动发现结果补充可选 profile。
- `control-plane config profiles --format json`：查看可选 profile 与无效目录、登记冲突的诊断。
- `--control-plane-profile <id>` 或仓内合同 service 的 `--config-path`：显式选择运行组合。
- [Agent 治理目录](../README.md)：治理规则与唯一真源。
- [Stack 升级手册](../../docs/operations/stack-upgrade-runbook.md)：组合锁定、升级和回滚。
