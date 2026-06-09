# Group Recovery 治理

当 `dependencyPolicy.retryMode=group_owned` 时，组内恢复语义的正式真源固定为：

- `agent/extensions/<extension-id>/agent/control_plane/groups/<group_ref>.json -> recoveryPolicy`
- `python/openclaw/control_plane/registry/binding_topology.py -> resolvedRecoveryPolicy / resolvedRecoveryStep`

## 约束

- 不在单个 retry job、脚本注释或 README 中重复维护 recovery 主定义。
- recovery 是扩展包内共享 group 对象的一部分，不落到根级 `agent/control_plane/groups/`。
