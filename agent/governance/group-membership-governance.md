# Group 成员治理

group 成员归属的正式真源固定为：

- `agent/extensions/<extension-id>/agent/control_plane/groups/<group_ref>.json -> schedulePolicy / dependencyPolicy / recoveryPolicy`

控制平面运行时从 group 调度拓扑解析：

- group 主链顺序
- 成员进入 / 退出条件
- access log 中的 `agentGroupRefs`

`agent/extensions/<extension-id>/agent/modules/<agent_ref>/module.json` 之外的任何 agent 快照或附属 JSON 都不得再维护 `groupRefs`。

## 规则

### 规则 1：group 成员归属由 group 调度拓扑进入 group

每个正式 group 成员归属都必须通过 `agent/extensions/<extension-id>/agent/control_plane/groups/*.json` 的调度拓扑显式进入某个 group。

### 规则 2：group 只声明拓扑与策略，不维护静态成员真源

`agent/extensions/<extension-id>/agent/control_plane/groups/*.json` 负责持有：

- 调度窗口
- 主链 job 顺序
- recoveryPolicy
- release / acceptance / dispatch 相关策略

## 禁止项

1. 在 `agent/extensions/<extension-id>/agent/modules/<agent_ref>/module.json` 重新新增 `governance.groupRefs`。
2. 在任何额外 agent 快照中重新新增 `governance.groupRefs`。
3. 在 job 中回写静态成员归属主定义。
