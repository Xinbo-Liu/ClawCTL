# Agent Group 治理

## 固定边界

- 仓内 extension 如需引入 group，真源固定为 `agent/extensions/<extension-id>/agent/control_plane/groups/*.json`。
- group 说明、成员拓扑、发布门禁与控制平面对齐信息固定收敛到扩展包内 group JSON 及相关模块 README，不建立根级 group 文档目录。
- group recovery 真源固定为 `recoveryPolicy`，不在单个 retry job 或脚本中重复维护。

## 正式口径

- 主仓库 formal group 通过仓内 extension 提供；接入 group 时必须通过正式 extension 装配共享对象。
- group 成员顺序、依赖关系、recovery policy 都以解析后的 registry 结果为准。
- 仓内托管的 managed explicit extension 可以自带 group，但不自动进入默认运行面。

## 回链

- group 真源规则：`agent/governance/group-governance.md`
- membership：`agent/governance/group-membership-governance.md`
- topology：`agent/governance/group-topology-governance.md`
- recovery：`agent/governance/group-recovery-governance.md`
