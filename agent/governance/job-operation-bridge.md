# Job / Operation 绑定治理

## 结论

- operation 真源：`agent/extensions/<extension-id>/agent/modules/<agent_ref>/module.json -> operations`
- job 绑定真源：`operations.*.jobBindings`
- 运行期解析结果：`resolvedExecutionPlan`
- 命令投影：由 `resolvedExecutionPlan.commandSpec` / `materializedCommand` 派生

## 约束

- job 只声明调度窗口、重试与保留策略，不反向承载模块 operation 真源。
- README、脚本帮助与局部说明页只能引用上述真源，不维护第二份 operation 快照。
