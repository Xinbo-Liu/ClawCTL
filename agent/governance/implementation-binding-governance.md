# Implementation 绑定治理

## 结论

- implementation 绑定真源：`agent/extensions/<extension-id>/agent/modules/<agent_ref>/module.json -> logic.implementationRef`
- 运行期解析结果：`resolvedImplementationRef`、`resolvedRuntime`、`resolvedRuntimeAdapter`
- 任何额外 agent 快照与 `agent/control_plane/jobs/*.json` 都不得再声明 implementation 绑定。
