# 外部投递恢复与验收

本文档固定平台共享的 `externalDispatch` 接受、恢复和人工核验边界。目标注册、生命周期和通用
运维入口见 [`dispatch-targets.md`](dispatch-targets.md)。业务扩展必须在自身 `docs/operations/`
下维护目标 ID、业务运行 ID、发送顺序与接收端核对项；共享文档不承载具体业务对象。

## 接受维度

一次投递执行必须分别记录：

| 维度                | 含义                                           |
|---------------------|------------------------------------------------|
| `processAccepted`   | 子进程退出与技术执行被接受                     |
| `contractAccepted`  | outcome 身份、状态、目标角色和恢复引用符合合同 |
| `artifactAccepted`  | manifest 指定的证据通过路径、哈希与新鲜度校验  |
| `executionAccepted` | 技术执行、投递合同和证据的联合执行结论         |
| `acceptedByLedger`  | 调度账本最终接受本次运行                       |

退出码、任一目标成功、结构化 stdout 或 `latest` 文件存在均不能替代上述维度。

Outcome 的 `operation` 只允许 `send`、`retry` 和 `operator_verify`；`status` 只允许 `sent`、
`noop`、`retry_pending`、`rate_limited`、`failed`、`blocked` 和 `dry_run`。`noop` 必须引用同一
`businessRunId::targetId` 的有效 `sent` 证明；没有证明的重复执行属于合同违规。`required` 目标
全部接受后业务运行才可完成；`advisory` 目标异常只产生关注状态。

## 无发送状态重建

状态重建只能读取不可变 run、ledger 和证据，再生成 mutable status、completion、latest alias、
pipeline health 与 routing projection。重建命令必须具备以下性质：

- 禁止网络访问和 provider 调用；
- 不生成补偿队列之外的发送动作；
- 无有效 `sent` 证明时保持未完成；
- pipeline 与 projection 必须携带同一 `pipelineSnapshotId`。

具体重建入口由业务扩展手册声明，并必须通过受控 target operation 调用，不能新增绕过调度器的发送入口。

## 未知送达与人工证明

传输超时、连接重置等无法确认 provider 是否收件的结果必须保持 `blocked`，不得自动重试。仅当操作员已在
接收端确认消息可见时，才能写不可变人工证明。证明必须同时引用原 scheduler run、
`businessRunId`、`targetId`、内容 SHA-256、操作员身份与原因。

人工证明按 `sent` 闭环，但永久保留 `dispatch_manual_verified`，聚合状态保持关注。任一身份、哈希或
原失败分类不匹配都必须拒绝。

## 受控单 job 恢复

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane scheduler-runtime \
  --control-plane-profile <profile-id> \
  --run-job-once <job-id> \
  --business-run-id <businessRunId> \
  --operator-reason '<approved-reason>' \
  --recovery-of-run-id <originalSchedulerRunId> \
  --maintenance-override
```

该入口只跳过 cron 时间判断，仍检查 enabled、依赖、cycle/job lock、目标合同、outcome、证据与账本。
maintenance override 只允许单 job，并写入操作审计。恢复只有在 origin job/run/businessRunId 精确匹配、
required 目标已接受且证据校验通过时才能关闭原运行；空队列或无关后续访问不能改变原运行结论。

`group_owned` job 禁止 scheduler 自动重试；`stage_owned` job 只重试
`failureClassPolicy.retryableClasses` 明确声明的分类。未知或终态分类不得进入自动重试窗口。

## 发布与回滚边界

1. 先启用 scheduler maintenance，备份 mutable 状态；不可变 runs 与 ledger 不移动、不改写。
2. 在隔离环境完成 schema、registry、scheduler、dispatcher、pipeline 和 one-click 验证。
3. 部署后先运行 preflight 与无发送状态重建，确认没有外发。
4. 真实发送必须遵循业务扩展手册中的人工闸门和目标顺序。
5. 发送前可以恢复 mutable 备份；发送后不得删除证据或无证明重发，只允许重新重建状态或前向修复。

所有终端记录和报告只保留 HTTP 状态、脱敏业务码与失败分类，不记录 endpoint、secret 或完整 provider 响应。
