# architecture 目录说明

`architecture/` 定义正式实现的结构、职责、边界与固定结论。

## 本页解决什么问题

- 判断项目的唯一正式实现是什么。
- 判断哪些能力属于正式支持边界。
- 判断 base 与 `agent_platform` 各自扮演什么角色。
- 判断仓内 extension 如何通过正式装配链进入运行面。
- 判断目标态边界应去哪里看：[../../VISION.md](../../VISION.md)。

## 合同页入口

- 基线：[control-plane-baseline.md](control-plane-baseline.md)
- 平台主路径：[platform-main-path.md](platform-main-path.md)
- 支持边界：[supported-deployment-boundary.md](supported-deployment-boundary.md)

## 二级专题页

- 路径治理：[path-governance.md](path-governance.md)
- Python 包布局：[python-package-layout.md](python-package-layout.md)
- Agent 与权限：[agents-and-permissions.md](agents-and-permissions.md)
- Agent 治理：[agent-governance.md](agent-governance.md)
- Agent 生命周期治理：[agent-lifecycle-governance.md](agent-lifecycle-governance.md)
- Agent 模块治理：[agent-module-governance.md](agent-module-governance.md)
- Agent Group 治理：[agent-group-governance.md](agent-group-governance.md)
- 显式扩展包挂载与编排：[explicit-extension-packages.md](explicit-extension-packages.md)
- 基座与扩展同步升级：[../operations/stack-upgrade-runbook.md](../operations/stack-upgrade-runbook.md)

## 下一步

- 需要项目级正式基线：回 [control-plane-baseline.md](control-plane-baseline.md)。
- 需要确认支持边界：回 [supported-deployment-boundary.md](supported-deployment-boundary.md)。
- 需要查看 agent plane 结构：从 [agent-governance.md](agent-governance.md)、[agent-module-governance.md](agent-module-governance.md) 与 [agent-group-governance.md](agent-group-governance.md) 进入。
- 需要编写或挂载显式业务扩展包：看 [explicit-extension-packages.md](explicit-extension-packages.md)。
- 需要升级基座与扩展组合：看 [../operations/stack-upgrade-runbook.md](../operations/stack-upgrade-runbook.md)。
- 需要查看目标态边界：回 [../../VISION.md](../../VISION.md)。
