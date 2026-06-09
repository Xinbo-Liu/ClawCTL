# Domain 治理

## 分层结论

领域共享资产只能在受管显式扩展包内声明；主仓库不提供根级 domain 共享层。

| 层级                                                                                                                                                | 正式职责                | 典型内容                           | 禁止内容            |
|---------------------------------------------------------------------------------------------------------------------------------------------------|---------------------|--------------------------------|-----------------|
| 模块层 `agent/extensions/<extension-id>/agent/modules/<agent_ref>/` + `agent/extensions/<extension-id>/python/<python-package>/modules/<agent_ref>/` | 单模块私有真源             | 主清单、局部能力边界、模块私有 Python、薄启动器    | 其他模块共享资产        |
| domain 层 `agent/extensions/<extension-id>/python/<python-package>/domains/<domain_ref>/`                                                          | 同领域多模块共享资产          | 共享模型、共享视图、共享验证函数、共享编排支撑、共享排障说明 | 平台通用资产、单模块私有资产  |
| 平台层 `python/openclaw/control_plane/`、`scripts/`、`config/`、`docs/`                                                                                 | 平台级装配、调度、发布、审计与统一入口 | registry 派生、统一命令入口、平台政策        | 领域私有实现、领域私有文档真源 |

## 准入规则

- 只有跨模块共享且归属于同一个扩展包的资产，才允许进入扩展包内的 domain 层。
- 单模块独占逻辑必须留在模块目录或模块私有 Python 目录。
- domain 层对象必须通过扩展包的 `activation.enabledExtensionIds` 与 service config 显式装配。
