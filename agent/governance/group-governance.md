# Group 治理

## 固定规则

1. group 成员归属由 `agent/extensions/<extension-id>/agent/control_plane/groups/*.json` 的显式拓扑定义派生。
2. group 入口 / 出口 / 成员顺序由派生后的主链 job 顺序解析。
3. group recovery 真源为 group `recoveryPolicy`。
4. 主仓库默认运行面不提供业务 group；仓内 managed explicit extension 可以自带 group，但必须显式装配。
5. group 说明、成员拓扑、发布门禁与控制平面对齐信息收敛到扩展包内 group JSON 和相关模块 README，不建立根级 group 文档目录。成员顺序、依赖关系和 recovery policy 以显式装配后解析的 registry 为准。

## 运行接受与恢复

- `schemaVersion=2` group 的 recovery 必须通过 `recoveryPolicy.recoveryOfJobRef` 显式声明来源主 job。恢复 outcome 只有在 job、run、businessRunId 精确匹配，required 目标被合同接受且证据通过校验时，才能改变来源运行的 effective status。
- `group_owned` 主链 job 禁止 scheduler 自动重试，补偿由 group recovery steps 管理；`stage_owned` job 只重试 `failureClassPolicy.retryableClasses` 声明的分类。
- 账本行保持不可变；状态投影分别暴露 `originalStatus`、`effectiveStatus` 和 `recoveredBy`。空补偿队列或无关后续 agent access 不能关闭原投递失败。

## 专题合同

- [成员归属](group-membership-governance.md)：从 group 拓扑派生成员关系。
- [主链拓扑](group-topology-governance.md)：定义 job 顺序和依赖。
- [恢复真源](group-recovery-governance.md)：定义 recovery policy 的归属。
- [外部投递恢复](../../docs/operations/delivery-recovery.md)：接受维度、证据与人工核验。
