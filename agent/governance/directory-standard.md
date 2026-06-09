# 目录标准

## 正式目录

### 1. `agent/extensions/<extension-id>/` 是 formal module 的唯一承载根

每个受管显式扩展包都必须落在该根下，并在包内自带模块、共享领域代码与 control-plane 对象；正式接入可以来自 `agent/extensions/index.json` 显式登记，也可以来自通过严格校验的自动发现 profile。

### 2. `agent/extensions/<extension-id>/agent/modules/<module_ref>/` 是正式模块目录

每个正式模块都必须在这里维护主清单、局部文档、权限、工具与薄启动器。

### 3. `agent/extensions/<extension-id>/python/<python-package>/modules/<module_ref>/` 是模块私有 Python 真源

模块私有 Python 不得建立第二套并列根目录，也不得落在根级 `python/openclaw/modules/`。

### 4. 共享领域目录固定落在扩展包内 Python 真源

- Python 共享领域逻辑：`agent/extensions/<extension-id>/python/<python-package>/domains/<domain_ref>/`

## 目录职责表

| 目录                                                                              | 正式职责                  | 禁止内容                    |
|---------------------------------------------------------------------------------|-----------------------|-------------------------|
| `agent/extensions/<extension-id>/agent/modules/<module_ref>/`                   | 模块主清单、局部文档、权限、工具、薄启动器 | 其他模块私有真源、平台共享实现         |
| `agent/extensions/<extension-id>/python/<python-package>/modules/<module_ref>/` | 模块私有 Python 真源        | 其他模块私有逻辑、平台共享桥接         |
| `agent/extensions/<extension-id>/agent/control_plane/`                          | 扩展包共享调度与运行对象          | base control-plane 基座定义 |
| `agent/extensions/<extension-id>/python/<python-package>/domains/`              | 领域共享代码与共享验证资产         | 单模块独占实现                 |
| `config/workspace_templates/<workspace_ref>/`                                   | 工作区模板、会话提示、路由说明       | 模块主清单、模块私有实现            |
| `docs/`                                                                         | 项目级正式文档               | 把局部扩展包实现复制成第二份正式真源      |

## 约束

- 主仓库 formal module 只允许落在受管显式扩展包内；根级 `agent/modules/`、`agent/domains/`、`python/openclaw/modules/`、`python/openclaw/domains/` 只作为禁区校验对象保留命名。
- 主仓库默认运行面只包含 `base + agent_platform`；扩展包目录存在不代表自动进入默认运行面。
