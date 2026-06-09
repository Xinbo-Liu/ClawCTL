# Group 治理

## 固定规则

1. group 成员归属由 `agent/extensions/<extension-id>/agent/control_plane/groups/*.json` 的显式拓扑定义派生。
2. group 入口 / 出口 / 成员顺序由派生后的主链 job 顺序解析。
3. group recovery 真源为 group `recoveryPolicy`。
4. 主仓库默认运行面不提供业务 group；仓内 managed explicit extension 可以自带 group，但必须显式装配。
