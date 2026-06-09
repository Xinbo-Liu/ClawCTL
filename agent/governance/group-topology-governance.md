# Group 组内拓扑治理标准

本文档定义 group 主链拓扑的正式归属。

## 结论

- group 主链拓扑真源：group `schedulePolicy / dependencyPolicy`
- group 主链 job 顺序基准：`dependencyPolicy.orderedJobRefs`
- 运行期解析结果：`resolvedOrder`、`resolvedDependsOn`
- group 内主链 job 不得手写 `order` 与组内 `dependsOn`

## 规则

1. 进入某个 group 的 job，必须通过 group `schedulePolicy.jobRefs` 绑定；
2. 主链 job 顺序必须由 group 显式拓扑解析；
3. `orderBase / orderStep` 必须存在；
4. 主链 job 的组内依赖由 group 统一派生；
5. 补偿 job 不得混入主链顺序。
