# 生命周期治理

## 变更面与必须同步项

| 类型       | 典型动作                                                          | 必须同步更新                                                                                                                                                                          |
|----------|---------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 模块变更     | contract、operation、implementation、skills、permissions、tools 变更 | `agent/extensions/<extension-id>/agent/modules/<agent_ref>/`、对应的 `agent/extensions/<extension-id>/python/<python-package>/modules/<agent_ref>/`、相关 control-plane 注册对象、局部 README |
| group 变更 | 成员调整、主链顺序、recovery 规则、发布门禁变更                                  | `agent/extensions/<extension-id>/agent/control_plane/groups/*.json`、相关模块 README                                                                                                 |
| 调度变更     | job 时机、窗口、保留策略变更，以及 group 拓扑绑定变更                              | `agent/extensions/<extension-id>/agent/control_plane/jobs/*.json`、相关 group JSON                                                                                                 |
| 架构变更     | 真源边界或治理规则变更                                                   | `agent/governance/*.md`、`docs/architecture/agent-*.md`                                                                                                                          |

## 原则

- 根级 formal module 生命周期不属于主仓库治理面；所有模块生命周期都以扩展包内真源为准。
- `module_scaffold`、`module_lifecycle`、doctor / guard 必须与 managed explicit extension 布局同步演进。
- README、AGENTS、脚本帮助只能引用真源，不得维护第二份口径。
