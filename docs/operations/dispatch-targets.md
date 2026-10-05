# Dispatch Targets

本页描述 dispatch target 的注册、接入、轮换和生命周期边界。业务 target 清单、默认绑定与发送职责由
当前 service profile 启用的扩展真源决定；平台层不内置业务 target。

## 边界

- `agent_platform` 只提供 provider adapter registry 机制、中性 synthetic provider、runtime paths、
  object families 与 dispatch operations surface。
- 公共 provider 扩展提供通道 adapter；业务命令、业务流程治理和 outbox 语义保留在业务扩展。
- 业务扩展通过 manifest 的 `registry.dispatchTargetRegistryPaths` 启用 target registry。
- UI、脚本和人工命令都必须按当前 service profile 解析注册表；只启用 `agent_platform` 时不应出现
  业务 target。

## Profile 与真源

- 人工 profile 入口：`OPENCLAW_CONTROL_PLANE_PROFILE=<profile-id>`。
- `OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH` 由 `one_click_config.sh` 写入 `deploy/.env`，不手工填写。
- 平台 profile：`config/control_plane/profiles/agent_platform.service.json`。
- 业务 profile：`agent/extensions/<extension-id>/config/control_plane/profiles/<extension-id>.service.json`。
- 业务 target registry：`agent/extensions/<extension-id>/agent/control_plane/registries/dispatch_targets.json`。
- provider registry：由 `agent_platform` 和当前 profile 启用的公共 provider 扩展提供。
- 人工只修改 `deploy/site.env`、`agent/extensions/<extension-id>/deploy/extension.env` 与
  `deploy/targets.d/<target_id>.env`；注册表和生成后的 `deploy/.env` 均为只读运行合同。

## 通用查询

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops show-index \
  --control-plane-profile <profile-id>
bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops collect-targets \
  --gate-env-file deploy/.env --control-plane-profile <profile-id>
bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch observability objects \
  --control-plane-profile <profile-id>
```

<a id="dispatch-target-default-dry-run"></a>
## 接入与 Dry-run

完成目标输入后，重新生成运行 env、重建 scheduler，并通过受控 target operation 做 preflight、send
dry-run 和 retry dry-run。dry-run 只验证解析、合同与 payload，不发送消息，也不消费补偿队列。

```bash
bash ./scripts/setup/one_click_config.sh
bash ./scripts/runtime/run_runtime_service_action.sh up --target scheduler --force-recreate
bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops run-target-operation \
  --operation preflight --target <target_id> --env-file deploy/.env \
  --control-plane-profile <profile-id> --ensure-running strict
bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops run-target-operation \
  --operation send --target <target_id> --env-file deploy/.env \
  --control-plane-profile <profile-id> --ensure-running strict -- --dry-run true
bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops run-target-operation \
  --operation retry --target <target_id> --env-file deploy/.env \
  --control-plane-profile <profile-id> --ensure-running strict -- --dry-run true
```

dry-run 返回 0 只表示预演通过。真实发送前仍须核对目标级 acceptance、批次 acceptance 与业务手册中的
人工闸门。

<a id="dispatch-target-rotation-min-acceptance"></a>
## 轮换后的最小验收

endpoint 或 secret 轮换后，至少执行：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops run-target-operation \
  --operation preflight --target <target_id> --env-file deploy/.env \
  --control-plane-profile <profile-id> --ensure-running strict
bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch observability show-target-acceptance \
  --target <target_id> --gate-env-file deploy/.env --fail-on-fail --json --write-audit
bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops verify-rotation-sequence \
  --batch <batch_id> --gate-env-file deploy/.env --control-plane-profile <profile-id> \
  --json --fail-on-warn --write-audit
```

preflight 必须返回 0，目标 acceptance 不得为 fail，对应批次必须满足当前发布策略。单目标结果不能替代
批次治理结果，批次结果也不能替代单目标投递证明。

<a id="dispatch-target-lifecycle"></a>
## 生命周期

- `active`：目标参与当前配置解析；是否默认发送由 `enabledDefault` 与受控 env 共同决定。
- `disabled`：仅适用于非正式目标，保留注册表、批次和审计关系，但停止默认发送。
- `decommissioned`：仅适用于非正式目标，清空默认启用和 verification batch 关系，默认不可直接回退。
- 唯一 `formal_broadcast/required/publishLatestDefault=true` 目标必须保持 `lifecycleState=active` 且
  `enabledDefault=true`。注册表校验和生命周期脚本都会拒绝停用或退役该目标。
- 正式目标维护使用 scheduler maintenance；不得通过环境覆盖或生命周期变更移除正式职责。

非正式目标变更先 dry-run，再 apply：

```bash
bash ./scripts/setup/update_dispatch_target_lifecycle.sh \
  --target <target_id> --state disabled --write-audit
bash ./scripts/setup/update_dispatch_target_lifecycle.sh \
  --target <target_id> --state disabled --apply --write-audit
```

<a id="dispatch-recovery-order"></a>
## 恢复顺序

1. 冻结 scheduler，并采集当前 status、不可变目标记录和调度账本。
2. 先执行不访问网络的状态重建，核对当前 `businessRunId` 和正式发布目标。
3. 未知送达只在接收端确认可见后写人工证明，不自动重试。
4. 确需补投时使用受控 `--run-job-once`，精确引用原 job、scheduler run 与 `businessRunId`。
5. 真实发送遵循业务扩展手册声明的目标顺序和人工闸门。

共享 outcome 状态、五个接受维度和恢复合同见
[`delivery-recovery.md`](delivery-recovery.md)；具体目标与接收端核对项由业务扩展手册维护。

## 固定规则

- `boundary.dispatchLane`、`boundary.payloadScope`、`boundary.completionRole` 与
  `boundary.publishLatestDefault` 是运行职责真源。
- 正式目标和已启用运维目标为 `required`；联调目标为 `advisory`，异常只产生 `watch`。
- 所有已启用 `externalDispatch` job 必须消费 `resolvedDeliveryContract`，并写入调度器分配的 delivery
  outcome path。退出码、任一目标成功或 latest 文件存在都不能单独构成业务成功。
- 正式目标不得启用相似度静默。跨 `businessRunId` 内容相似仍必须发送；同一
  `businessRunId::targetId` 只有引用有效 `sent` 证明时才允许 `noop`。
- `latest.json` 只允许指向当前正式发布目标的已接受不可变记录，它是别名，不是独立成功证明。
- 仓内扩展必须通过正式 profile、有效自动发现 profile 或仓内合同 service 的显式 `--config-path`
  接入，不进入主仓库默认业务入口。
