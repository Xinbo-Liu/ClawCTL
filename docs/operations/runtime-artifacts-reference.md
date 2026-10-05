# 运行产物参考

本页说明默认 `agent_platform` 声明的运行产物位置、生产者、验收用途与作业产物策略。

内容依据对象族合同和 job artifactPolicy 生成。生成时不读取 `.env` 或运行态 state；实际部署是否通过验收，以运行证据和验收结果为准。

## 阅读与定位

- 下表路径使用默认宿主机视角；部署机的状态根目录可配置。使用对象解析命令获取现场路径，避免把默认路径当作每台机器的绝对地址。
- 人工维护配置真源；运行产物由生产者生成。缺失、陈旧或失败的 evidence 应先修复生产阶段，再重新验收。
- [运行与验收入口](runtime-service-reference.md)、[维护地图](maintenance-map.md)、[排障分流](troubleshooting.md)。

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane objects json
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane objects entry-path --family <family-id> --entry <entry-id>
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane artifacts json
```

## 对象族与固定产物

### deployment acceptance 对象族

说明 deployment acceptance state 与 private ingress 边界证据的路径、生产入口和验收用途。

| 对象                      | 默认宿主机路径                                                    | 生产者                                                                                                                       | 用途                                                                                                                                                                 |
|---------------------------|-------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| deployment_acceptance     | state/openclaw/control_plane/setup/deployment_acceptance.json     | bash ./scripts/setup/one_click_test_full.sh                                                                                  | 默认全量 full test 完成后写出的 deployment acceptance 状态；只表达 eligible / accepted 与 required_checks。                                                          |
| ingress_boundary_evidence | state/openclaw/control_plane/setup/ingress_boundary_evidence.json | sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env && bash ./scripts/setup/one_click_deploy.sh | 固定记录 compose/runtime 端口暴露事实、Nginx allowlist 闭合结果与来源限制语义校验结果；host_firewall 基础证据由 root 写出，Nginx policy 可由部署用户本地校验后合并。 |

### 部署 / full test latest 摘要对象族

说明 one_click_deploy 与 one_click_test_full 的 latest 摘要路径，用于查看最近一次执行结果和定位部署或验收故障。

| 对象                                        | 默认宿主机路径                                                             | 生产者                                      | 用途                                                                                                               |
|---------------------------------------------|----------------------------------------------------------------------------|---------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| one_click_deploy_latest_summary_json        | state/openclaw/control_plane/setup/one_click_deploy.latest.summary.json    | bash ./scripts/setup/one_click_deploy.sh    | 无论 success / failed，one_click_deploy 最近一次机器摘要都会镜像到固定 latest 路径，便于排障页与外层脚本稳定读取。 |
| one_click_deploy_latest_summary_markdown    | state/openclaw/control_plane/setup/one_click_deploy.latest.summary.md      | bash ./scripts/setup/one_click_deploy.sh    | one_click_deploy 最近一次人工可读摘要的固定 latest 镜像。                                                          |
| one_click_test_full_latest_summary_json     | state/openclaw/control_plane/setup/one_click_test_full.latest.summary.json | bash ./scripts/setup/one_click_test_full.sh | full test 最近一次机器摘要固定路径；检查项明细与下一步动作以它为唯一 latest 真源。                                 |
| one_click_test_full_latest_summary_markdown | state/openclaw/control_plane/setup/one_click_test_full.latest.summary.md   | bash ./scripts/setup/one_click_test_full.sh | full test 最近一次人工阅读版摘要固定路径。                                                                         |

### runtime evidence 对象族

说明导出到 control-plane state release/evidence/ 的运行验收、run ledger、group 发布证据与 job artifact policy 摘要，用于核对运行结果和归档交付证据。

| 对象                                    | 默认宿主机路径                                                                             | 生产者                                                            | 用途                                                                                                                                          |
|-----------------------------------------|--------------------------------------------------------------------------------------------|-------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| runtime_acceptance                      | state/openclaw/control_plane/release/evidence/runtime-acceptance.json                      | bash ./scripts/runtime/export_runtime_acceptance_evidence.sh      | 聚合 deployment acceptance、official CLI 摘要、scheduler runtime 与 run ledger 后写出的运行验收证明。                                         |
| control_plane_run_ledger                | state/openclaw/control_plane/release/evidence/control-plane-run-ledger.json                | bash ./scripts/runtime/export_runtime_acceptance_evidence.sh      | 逐 job 汇总 latest run / result / artifacts manifest，并作为 runtime acceptance 的结构化真源。                                                |
| control_plane_agent_access_log          | state/openclaw/control_plane/release/evidence/control-plane-agent-access-log.json          | control-plane export-agent-group-evidence / scheduler auto export | 导出最近 agent 调用访问日志摘要，作为 group 级发布证据与调用审计归档。                                                                        |
| control_plane_agent_group_access        | state/openclaw/control_plane/release/evidence/control-plane-agent-group-access.json        | control-plane export-agent-group-evidence / scheduler auto export | 导出按 group 聚合的 recent access、timeline、member waterfall 与 failure hotspots，作为 group_access_view 正式证据。                          |
| control_plane_agent_group_release_gates | state/openclaw/control_plane/release/evidence/control-plane-agent-group-release-gates.json | control-plane export-agent-group-evidence / scheduler auto export | 导出按 group 计算的发布门禁、required evidence 与 rollback contract 摘要，作为组发布治理的正式归档。                                          |
| control_plane_job_artifact_policies     | state/openclaw/control_plane/release/evidence/control-plane-job-artifact-policies.json     | bash ./scripts/runtime/export_runtime_acceptance_evidence.sh      | 逐 job 固定记录 runArtifactRoot、latestAlias、retentionDays 与 scheduler run manifest 模式，供 run ledger、scheduler 文档与活动脚本统一引用。 |
| official_cli_control_plane              | state/openclaw/control_plane/release/evidence/official-cli-summary.control-plane.json      | bash ./scripts/runtime/export_runtime_acceptance_evidence.sh      | 官方 Gateway 容器内 doctor / security audit / models probe 的聚合摘要。                                                                       |
| dispatch_runtime_check                  | state/openclaw/control_plane/release/evidence/dispatch-runtime-check.json                  | bash ./scripts/runtime/export_runtime_acceptance_evidence.sh      | 由 scheduler 承载的 delivery_adapter 执行 dispatcher preflight/status 后写出的结构化运行摘要。                                                |
| shadow_verify_summary_json              | state/openclaw/control_plane/release/evidence/shadow-verify-summary.json                   | bash ./scripts/runtime/export_runtime_acceptance_evidence.sh      | 候选实例 shadow verify 的结构化摘要；只有 shadow verify 已运行时才会同步。                                                                    |
| shadow_verify_summary_md                | state/openclaw/control_plane/release/evidence/shadow-verify-summary.md                     | bash ./scripts/runtime/export_runtime_acceptance_evidence.sh      | 候选实例 shadow verify 的 Markdown 摘要。                                                                                                     |
| shadow_verify_compare_json              | state/openclaw/control_plane/release/evidence/shadow-verify-compare.json                   | bash ./scripts/runtime/export_runtime_acceptance_evidence.sh      | 当前 runtime 与 candidate runtime 的结构化对比摘要。                                                                                          |
| shadow_verify_compare_md                | state/openclaw/control_plane/release/evidence/shadow-verify-compare.md                     | bash ./scripts/runtime/export_runtime_acceptance_evidence.sh      | 当前 runtime 与 candidate runtime 的 Markdown 对比摘要。                                                                                      |

### 投递运行态对象族

统一说明投递运行摘要、目标注册表、每次运行及重试队列的路径，供平台观测与排障使用。

| 对象                          | 默认宿主机路径                                                   | 生产者                                        | 用途                                                                               |
|-------------------------------|------------------------------------------------------------------|-----------------------------------------------|------------------------------------------------------------------------------------|
| dispatch_runtime_summary_json | state/openclaw/control_plane/setup/dispatch_runtime_summary.json | setup env render-dispatch-runtime / bootstrap | 记录当前目标注册表、provider 绑定、默认目标组与投递运行状态的结构化摘要。          |
| dispatch_targets_json         | state/openclaw/control_plane/dispatch/targets.json               | setup env render-dispatch-runtime / bootstrap | scheduler 侧的投递目标快照，供运行执行与排障使用。                                 |
| dispatch_out_dir              | state/openclaw/control_plane/dispatch_out                        | delivery_adapter / dispatcher                 | dispatch.json、status.json、ledger.jsonl、queue 与 queue_done 等投递产物的根目录。 |
| dispatch_runs_dir             | state/openclaw/control_plane/dispatch_out/runs                   | delivery_adapter / dispatcher                 | 按运行分隔的投递输出目录，保存每次运行的审计产物。                                 |
| dispatch_queue_dir            | state/openclaw/control_plane/dispatch_out/queue                  | dispatcher queue manager                      | 宿主机侧等待重试的投递队列目录。                                                   |
| dispatch_queue_done_dir       | state/openclaw/control_plane/dispatch_out/queue_done             | dispatcher queue manager                      | 已完成、过期或失败的重试队列归档目录。                                             |

### 投递治理审计对象族

统一说明平台共享的目标验收、批次验收、轮换顺序、治理检查与生命周期变更审计产物。

| 对象                                        | 默认宿主机路径                                                                   | 生产者                                                                                                                                                                  | 用途                                             |
|---------------------------------------------|----------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------|
| dispatch_target_acceptance_audit_dir        | state/openclaw/control_plane/dispatch_out/audit/target_acceptance                | bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch observability show-target-acceptance --target <target_id> --gate-env-file deploy/.env --write-audit         | 按投递目标保存验收结果的默认审计目录。           |
| dispatch_target_batch_acceptance_audit_dir  | state/openclaw/control_plane/dispatch_out/audit/dispatch_target_batch_acceptance | bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch observability show-batch-acceptance --batch <batch_id> --gate-env-file deploy/.env --write-audit            | 按批次保存验收结果与发布门禁结果的默认审计目录。 |
| dispatch_target_rotation_sequence_audit_dir | state/openclaw/control_plane/dispatch_out/audit/target_rotation_sequence         | bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops verify-rotation-sequence --gate-env-file deploy/.env --control-plane-profile <profile_id> --write-audit | 保存轮换顺序验证结果的默认审计目录。             |
| dispatch_governance_audit_dir               | state/openclaw/control_plane/logs/dispatch-governance-audit                      | bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops verify-rotation-sequence --gate-env-file deploy/.env --control-plane-profile <profile_id> --write-audit | 保存平台级投递治理检查结果的默认审计目录。       |
| dispatch_target_lifecycle_audit_dir         | state/openclaw/control_plane/setup/audit/dispatch_target_lifecycle               | bash ./scripts/setup/update_dispatch_target_lifecycle.sh --write-audit                                                                                                  | 保存目标停用与退役等生命周期变更的默认审计目录。 |

## 作业产物策略

策略来自默认平台已注册 job 的 `artifactPolicy` 和输入输出声明；路径由所选 profile 的 runtime paths 解析。

默认 scheduler run 根目录：`state/openclaw/control_plane_scheduler/runs`。

每次运行的 `run.json`、`result.json`、`artifacts.json` 与 `stdout.log` 位于 scheduler run 目录。run ledger 的接受结果由实际执行和产物检查产生，不能仅凭进程退出码判断验收通过。

| job | 产物根入口 | latest alias | 保留天数 | run manifest 模式 |
|-----|------------|--------------|----------|-------------------|

默认平台没有已注册 job。查询业务扩展的产物策略时，先选择对应 profile。

## 扩展与现场查询

扩展的运行产物由其对象族、runtime paths 和 job 合同声明。先选择扩展 profile；查询同名对象族时，在对象命令中显式传 `--extension <id>`。业务输出的含义与使用方式见扩展 README。

```bash
OPENCLAW_CONTROL_PLANE_PROFILE=<profile-id> bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane objects json
OPENCLAW_CONTROL_PLANE_PROFILE=<profile-id> bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane artifacts json
```
