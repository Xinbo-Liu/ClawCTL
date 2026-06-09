# Agent 生命周期与变更治理

本文档定义 agent 与 group 在持续治理中的项目级生命周期规则。专题治理标准固定在 `agent/governance/lifecycle-governance.md`；本页负责固定项目级变更顺序与同步更新要求。

## 生命周期阶段

每个 agent 与 group 固定经历以下阶段：

1. **设计**：补齐治理合同、对象模型、模块边界与输入输出合同；
2. **注册**：写入正式注册表，完成 schema 校验与引用闭合；
3. **集成**：接入 runtime、scheduler、权限与工具装配；
4. **验收**：补齐冒烟、合同、运行与证据导出链路；
5. **发布**：进入正式支持面，并记录版本、owner 与发布口径；
6. **运行**：纳入统一监控、告警、审计与运维操作；
7. **变更**：执行受控升级、灰度、回滚或冻结；
8. **退役**：停止调度、冻结权限、归档文档与证据。

## 变更类型

agent 变更固定分为以下四类：

| 变更类型 | 示例                               | 必须同步更新                        |
|------|----------------------------------|-------------------------------|
| 结构变更 | 新增 agent、新增 group、成员重组、目录迁移      | `agent/` 文档、架构文档、注册表、group 合同 |
| 行为变更 | 输入输出合同、失败语义、调度依赖变化               | agent 文档、数据合同、验收链路            |
| 装配变更 | skill、tool、permission、runtime 变化 | 模块 manifest、权限说明、审计要求         |
| 运维变更 | 启停、灰度、回滚、告警阈值变化                  | operations 文档、运行证据与 runbook   |

## 文档更新要求

agent 治理变更至少同步以下页面或目录：

- 项目级基准涉及变化时：[`control-plane-baseline.md`](control-plane-baseline.md)
- agent 总体治理变化：[`agent-governance.md`](agent-governance.md)
- group 变化：[`agent-group-governance.md`](agent-group-governance.md)
- 模块边界变化：[`agent-module-governance.md`](agent-module-governance.md)
- 权限状态页：[`agents-and-permissions.md`](agents-and-permissions.md)
- agent 专题治理体系：`agent/README.md`


## 阶段执行要求

agent plane 变更必须同步清理与正式实现冲突的文件、链接与描述。

## 禁止事项

以下做法一律禁止：

- 先接线运行，后补治理文档；
- 把 group 事实写在脚本里而不是注册表里；
- 把 skill、tool、permission 的关键限制只写在局部 prompt 或局部说明里；
- 使用隐藏目录或未登记脚本作为正式运行真源；
- 一边声明 `agent/` 为统一目录，一边继续扩散第二套治理入口；
- 发布、回滚、退役没有证据输出。

## 关联页面

- agent 统一治理目录：`agent/README.md`
- 生命周期专题治理：`agent/governance/lifecycle-governance.md`
- 总体基线：[`agent-governance.md`](agent-governance.md)
- group 管理：[`agent-group-governance.md`](agent-group-governance.md)
- 模块治理：[`agent-module-governance.md`](agent-module-governance.md)
- 真源矩阵：`agent/governance/source-of-truth-matrix.md`
