# Implementation 绑定治理

## 结论

- implementation 绑定真源：`agent/extensions/<extension-id>/agent/modules/<agent_ref>/module.json -> logic.implementationRef`
- 运行期解析结果：`resolvedImplementationRef`、`resolvedRuntime`、`resolvedRuntimeAdapter`
- 扩展包内 `agent/control_plane/jobs/*.json` 与任何额外 agent 快照都不得重复声明由模块派生的 implementation 绑定。
