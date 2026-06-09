# operations 目录说明

`operations/` 负责部署完成后的运行、验收、排障与治理入口分流。部署前的宿主机准备、配置生成、镜像准备与一键部署主链，统一回到 [`../getting-started/README.md`](../getting-started/README.md)。

## 本页解决什么问题

- 部署完成后先从哪一页开始。
- 不同运行场景应该跳向哪一页。
- 如何避免把生成页误当成一级操作入口。

## 任务入口

- deployment acceptance / runtime acceptance：[`runtime-service-reference.md`](runtime-service-reference.md)
- 维护事实总览：[`maintenance-map.md`](maintenance-map.md)
- 基座与扩展同步升级：[`stack-upgrade-runbook.md`](stack-upgrade-runbook.md)
- 故障分流入口：[`troubleshooting.md`](troubleshooting.md)
- dispatch 运维与 target 治理：[`dispatch-targets.md`](dispatch-targets.md)
- 安全边界说明：[`security-boundary.md`](security-boundary.md)

## 常用跳转

- 配置真源、生成文档、运行服务与证据路径：[`maintenance-map.md`](maintenance-map.md)
- Gateway runtime 合同、运行镜像来源与 acceptance 证据：[`runtime-service-reference.md`](runtime-service-reference.md)
- 控制平面对象、artifact、调度与 dispatch 运行态路径：`agent/README.md`
- 基座与扩展组合升级、回滚和 stack lock：[`stack-upgrade-runbook.md`](stack-upgrade-runbook.md)
- 部署阶段顺序与恢复执行语义：[`../getting-started/quickstart.md`](../getting-started/quickstart.md)
- 宿主机 readiness 与 private ingress 前提：[`../getting-started/environment-setup.md`](../getting-started/environment-setup.md)
- 支持边界与受支持部署路径：[`../architecture/supported-deployment-boundary.md`](../architecture/supported-deployment-boundary.md)

## 常见场景跳转

- 刚完成部署，先确认服务是否真的起来：进入 [`runtime-service-reference.md`](runtime-service-reference.md)
- 需要判断某段说明应改真源还是生成物：进入 [`maintenance-map.md`](maintenance-map.md)
- HTTPS 入口异常、Control UI 异常或部署后状态不对：进入 [`troubleshooting.md`](troubleshooting.md)
- 需要做 dispatch 晨检、恢复、接入或治理 target：进入 [`dispatch-targets.md`](dispatch-targets.md)
- 需要升级基座或扩展组合：进入 [`stack-upgrade-runbook.md`](stack-upgrade-runbook.md)
- 需要看对象路径、run ledger、dispatch 观察或调度治理：进入 `agent/README.md`
- 需要回到部署阶段顺序或 `--resume-from`：进入 [`../getting-started/quickstart.md`](../getting-started/quickstart.md)

## 下一步

- 要做统一运行验收：进入 [`runtime-service-reference.md`](runtime-service-reference.md)
- 要查维护事实总览：进入 [`maintenance-map.md`](maintenance-map.md)
- 要做故障分流：进入 [`troubleshooting.md`](troubleshooting.md)
- 要升级基座或扩展组合：进入 [`stack-upgrade-runbook.md`](stack-upgrade-runbook.md)
- 要回到部署主线：返回 [`../getting-started/quickstart.md`](../getting-started/quickstart.md)
