# Job 合同治理

## 结论

- 单 agent 合同真源：`agent/extensions/<extension-id>/agent/modules/<agent_ref>/module.json -> contract`
- 运行期解析结果：`resolvedContract / resolvedInputs / resolvedOutputs`
- `agent/control_plane/jobs/*.json` 与任何额外 agent 快照都不得再声明单 agent `inputs / outputs`。
