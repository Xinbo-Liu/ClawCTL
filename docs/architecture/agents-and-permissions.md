# Agents And Permissions

## 权限模型

- 仓内 extension 的 agent 身份、运行入口与模块归属从 `agent/extensions/<extension-id>/agent/modules/<module_ref>/module.json` 派生。
- 权限策略真源是扩展包模块目录下的 `permissions.json` 与共享 control-plane 解析结果。
- 工作区模板只负责会话提示、工具边界与路由说明，不承载模块私有权限真源。

## 边界

- 主仓库只保证通用 agent / permission 模型与 platform runtime 入口。
- 仓内 extension 可以增加新的 agent、workspace template 或 surface，但必须走正式 extension 机制。
- 同名 owner-aware surface 必须显式传 `--extension <id>`。

## 回链

- 模块治理：[`agent-module-governance.md`](agent-module-governance.md)
- 真源矩阵：`agent/governance/source-of-truth-matrix.md`
