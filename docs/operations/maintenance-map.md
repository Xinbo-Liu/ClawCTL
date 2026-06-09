# OpenClaw 维护事实总览

本页由 `control-plane facts overview` 的只读事实汇总生成，用于定位配置真源、生成文档、运行服务与证据路径。

## 统一入口

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane facts overview
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane facts overview --format json
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane facts overview --all-profiles --format json
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane facts overview --control-plane-profile <profile-id> --format markdown
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane facts overview --env-file deploy/.env --format json
```

## 当前控制面

| 项目                 | 值                                                         |
|--------------------|-----------------------------------------------------------|
| profile            | agent_platform                                            |
| config             | config/control_plane/profiles/agent_platform.service.json |
| enabled extensions | agent_platform                                            |
| known extensions   | agent_platform                                            |

## Registry 输入

| 类别                               | 数量 | 路径                                                             |
|----------------------------------|----|----------------------------------------------------------------|
| agent_groups_dirs                | 0  | -                                                              |
| agent_modules_dirs               | 0  | -                                                              |
| dispatch_provider_registry_paths | 1  | agent/control_plane/registries/dispatch_provider_adapters.json |
| dispatch_target_registry_paths   | 0  | -                                                              |
| jobs_dirs                        | 1  | config/control_plane/jobs                                      |
| models_dirs                      | 1  | config/control_plane/models                                    |
| runtime_adapter_registry_paths   | 1  | agent/control_plane/runtime/runtime_adapters.json              |
| targets_dirs                     | 1  | config/control_plane/targets                                   |

## 默认 profile 与 extension profile

| profile                  | config                                                    | enabled extensions | registry inputs                                                                                                  | evidence paths |
|--------------------------|-----------------------------------------------------------|--------------------|------------------------------------------------------------------------------------------------------------------|----------------|
| base                     | config/control_plane/service.json                         | -                  | jobs_dirs=1, models_dirs=1, targets_dirs=1                                                                       | 19             |
| agent_platform (default) | config/control_plane/profiles/agent_platform.service.json | agent_platform     | dispatch_provider_registry_paths=1, jobs_dirs=1, models_dirs=1, runtime_adapter_registry_paths=1, targets_dirs=1 | 19             |

## 真源 / 派生 / Evidence 改动顺序

- 配置、注册表、脚本清单和 governance surface 是真源；先改真源，再让 Python loader / renderer 消费。
- 生成文档只由对应 renderer 重写，不把派生 Markdown 作为独立维护面。
- 运行态 evidence 只由部署、full test、scheduler 或 evidence export 入口产生，不写入仓库真源。

## 正式验证路径

| 阶段                                                                                             | 命令                                                                                                                                                                                                                                                                                                                                            |
|------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 正式 Docker / 控制面容器门禁<br>正式门禁<br>用于提交前、发版前和目标机交付前的正式 release pass；固定要求 Docker daemon 与控制面容器执行介质。 | bash ./scripts/testing/check_repo_test_readiness.sh<br>bash ./scripts/setup/prepare_control_plane_medium.sh<br>bash ./scripts/testing/run_repo_unittest.sh<br>bash ./scripts/doctor/run_repo_release_gate.sh<br>bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane facts overview --all-profiles --format json --no-local-probe |
| Windows 宿主机诊断回归<br>诊断补充<br>仅用于无 Docker 或本地快速定位时的宿主机诊断补充；不得替代正式 Docker / 控制面容器门禁。               | python -B -m openclaw.testing.repo_host suite repo-check -q<br>python -B -m openclaw.doctor.platform.docstring_governance --mode report<br>bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane facts overview --format json --no-local-probe                                                                                     |

## 关键真源

| 真源                                      | 路径                                                    | 格式   |
|-----------------------------------------|-------------------------------------------------------|------|
| governance.repo_contracts               | config/governance/support/repo_contracts.json         | json |
| control_plane.profile_registry          | config/control_plane/profile_registry.tsv             | tsv  |
| control_plane.repo_combination_profiles | config/control_plane/repo_combination_profiles.json   | json |
| agent.extensions.index                  | agent/extensions/index.json                           | json |
| runtime.paths                           | config/runtime/paths.json                             | json |
| runtime.service_registry                | config/runtime/service_registry.json                  | json |
| runtime.testing_manifest                | config/runtime/testing_manifest.json                  | json |
| runtime.runtime_contract                | config/runtime/openclaw.runtime_contract.json         | json |
| deploy_env.schema                       | config/deploy_env/schema.json                         | json |
| control_plane.object_families           | config/control_plane/object_families.json             | json |
| governance.docs_registry                | config/governance/docs/docs_registry.json             | json |
| governance.script_catalog_surface       | config/governance/docs/script_catalog_surface.json    | json |
| governance.summary_manifest             | config/governance/release/summary_manifest.json       | json |
| governance.absent_surfaces              | config/governance/validation/absent_surfaces.json     | json |
| governance.local_workspace_policy       | config/governance/support/local_workspace_policy.json | json |
| governance.verification_tiers           | config/governance/support/verification_tiers.json     | json |

## 生成文档

| 真源                                                                                | 生成项                         | 目标                                                                                  |
|-----------------------------------------------------------------------------------|-----------------------------|-------------------------------------------------------------------------------------|
| agent/control_plane/job_artifact_policy_surface.json                              | artifact_policy_doc         | agent/README.md                                                                     |
| config/control_plane/extensions.d/agent_platform.dispatch_operations_surface.json | dispatch_operations_doc     | docs/operations/dispatch-targets.md                                                 |
| config/control_plane/object_families.json                                         | object_family_doc           | agent/README.md                                                                     |
| config/deploy_env/schema.json                                                     | deployment_inputs_doc       | docs/getting-started/deployment-inputs.md                                           |
| config/governance/docs/acceptance_surface.json                                    | acceptance_doc              | docs/operations/runtime-service-reference.md                                        |
| config/governance/docs/dispatch_observability_surface.json                        | dispatch_observability_doc  | docs/operations/dispatch-targets.md                                                 |
| config/governance/docs/dispatch_operations_surface.json                           | dispatch_operations_doc     | docs/operations/dispatch-targets.md                                                 |
| config/governance/docs/flow_summary_surface.json                                  | flow_summary_doc            | docs/operations/troubleshooting.md                                                  |
| config/governance/docs/full_test_surface.json                                     | full_test_doc               | docs/operations/runtime-service-reference.md                                        |
| config/governance/docs/getting_started_surface.json                               | environment_setup_doc       | docs/getting-started/environment-setup.md                                           |
| config/governance/docs/getting_started_surface.json                               | quickstart_doc              | docs/getting-started/quickstart.md                                                  |
| config/governance/docs/image_governance_surface.json                              | image_governance_doc        | docs/getting-started/image-preparation.md                                           |
| config/governance/docs/path_entrypoints.json                                      | path_entrypoint_doc         | docs/getting-started/deployment-inputs.md                                           |
| config/governance/docs/run_failure_surface.json                                   | run_failure_doc             | docs/operations/troubleshooting.md                                                  |
| config/governance/docs/script_catalog_surface.json                                | scripts_index_doc           | scripts/README.md                                                                   |
| config/governance/docs/setup_failures.json                                        | setup_failure_doc           | docs/operations/troubleshooting.md                                                  |
| config/governance/docs/setup_followups.json                                       | setup_followup_doc          | docs/getting-started/quickstart.md                                                  |
| config/governance/entrypoints/setup_entrypoints.json                              | setup_entrypoint_doc        | docs/getting-started/quickstart.md                                                  |
| config/governance/flows/default_deployment_flow.json                              | deployment_doc              | docs/getting-started/quickstart.md                                                  |
| config/governance/flows/default_deployment_flow.json                              | quickstart_docs             | ['docs/getting-started/quickstart.md', 'docs/getting-started/environment-setup.md'] |
| config/governance/flows/default_deployment_flow.json                              | setup_entrypoint_doc        | docs/getting-started/quickstart.md                                                  |
| config/governance/flows/full_test_group_registry.json                             | full_test_doc               | docs/operations/runtime-service-reference.md                                        |
| config/runtime/openclaw.runtime_contract.json                                     | runtime_contract_doc        | docs/operations/runtime-service-reference.md                                        |
| config/runtime/paths.json                                                         | path_group_reference_doc    | docs/architecture/path-governance.md                                                |
| config/runtime/source_strategy.json                                               | runtime_source_strategy_doc | docs/operations/runtime-service-reference.md                                        |
| config/services/internal_api.json                                                 | runtime_contract_doc        | docs/architecture/control-plane-baseline.md                                         |
| config/services/runtime_mounts.json                                               | runtime_mount_registry_doc  | docs/architecture/control-plane-baseline.md                                         |
| config/setup_flow/control_plane_medium.json                                       | control_plane_medium_doc    | docs/getting-started/environment-setup.md                                           |

## 脚本分组

| 分组            | 文件数 | 职责                                                                           |
|---------------|-----|------------------------------------------------------------------------------|
| setup         | 47  | 用于初始化仓库运行态与宿主机准备动作。                                                          |
| images        | 14  | 用于镜像治理与浏览器运行能力校验。                                                            |
| runtime       | 18  | 用于运行时入口与控制面一致性治理。                                                            |
| agent_runtime | 1   | 用于受管显式扩展包 agent 模块启动入口与正式运行入口。                                               |
| control_plane | 1   | 用于控制平面的受控触发入口。                                                               |
| gateway       | 4   | 承载 official gateway 的专属治理脚本与影子验证入口。                                          |
| doctor        | 29  | 用于运行态治理与体检。                                                                  |
| testing       | 3   | 用于仓库级测试运行器、本地回归与局部实现校验；不属于生产值守或发布门禁默认入口。                                     |
| docs          | 12  | 用于 docs_registry、文档入口、职责边界、导航结构、任务页模板、页面预算、实现对齐、局部文档身份与对象闭环检查。               |
| lib           | 23  | 供其他脚本复用的公共库；不作为人工入口；控制面命令统一经 scripts/runtime/run_openclaw_python_tool.sh 暴露。 |

## 运行服务

| target       | service                          | container                        |
|--------------|----------------------------------|----------------------------------|
| gateway      | openclaw-official-gateway        | openclaw-official-gateway        |
| ingress      | openclaw-private-ingress         | openclaw-private-ingress         |
| internal-api | openclaw-internal-api            | openclaw-internal-api            |
| scheduler    | openclaw-control-plane-scheduler | openclaw-control-plane-scheduler |

## 证据路径

| 对象族                | 条目                                            | 路径                                                                                               |
|--------------------|-----------------------------------------------|--------------------------------------------------------------------------------------------------|
| acceptance_state   | deployment_acceptance                         | state/openclaw/control_plane/setup/deployment_acceptance.json                                    |
| acceptance_state   | ingress_boundary_evidence                     | state/openclaw/control_plane/setup/ingress_boundary_evidence.json                                |
| flow_summary_state | one_click_deploy_latest_summary_json          | state/openclaw/control_plane/setup/one_click_deploy.latest.summary.json                          |
| flow_summary_state | one_click_deploy_latest_summary_markdown      | state/openclaw/control_plane/setup/one_click_deploy.latest.summary.md                            |
| flow_summary_state | one_click_test_full_latest_summary_json       | state/openclaw/control_plane/setup/one_click_test_full.latest.summary.json                       |
| flow_summary_state | one_click_test_full_latest_summary_markdown   | state/openclaw/control_plane/setup/one_click_test_full.latest.summary.md                         |
| runtime_evidence   | runtime_acceptance                            | state/openclaw/control_plane/release/evidence/runtime-acceptance.json                            |
| runtime_evidence   | control_plane_run_ledger                      | state/openclaw/control_plane/release/evidence/control-plane-run-ledger.json                      |
| runtime_evidence   | control_plane_agent_access_log                | state/openclaw/control_plane/release/evidence/control-plane-agent-access-log.json                |
| runtime_evidence   | control_plane_agent_group_access              | state/openclaw/control_plane/release/evidence/control-plane-agent-group-access.json              |
| runtime_evidence   | control_plane_agent_group_acceptance_bindings | state/openclaw/control_plane/release/evidence/control-plane-agent-group-acceptance-bindings.json |
| runtime_evidence   | control_plane_agent_group_release_gates       | state/openclaw/control_plane/release/evidence/control-plane-agent-group-release-gates.json       |
| runtime_evidence   | control_plane_job_artifact_policies           | state/openclaw/control_plane/release/evidence/control-plane-job-artifact-policies.json           |
| runtime_evidence   | official_cli_control_plane                    | state/openclaw/control_plane/release/evidence/official-cli-summary.control-plane.json            |
| runtime_evidence   | dispatch_runtime_check                        | state/openclaw/control_plane/release/evidence/dispatch-runtime-check.json                        |
| runtime_evidence   | shadow_verify_summary_json                    | state/openclaw/control_plane/release/evidence/shadow-verify-summary.json                         |
| runtime_evidence   | shadow_verify_summary_md                      | state/openclaw/control_plane/release/evidence/shadow-verify-summary.md                           |
| runtime_evidence   | shadow_verify_compare_json                    | state/openclaw/control_plane/release/evidence/shadow-verify-compare.json                         |
| runtime_evidence   | shadow_verify_compare_md                      | state/openclaw/control_plane/release/evidence/shadow-verify-compare.md                           |

## 本地现场

生成文档不读取 `deploy/.env` 或运行态 state。需要查看当前机器现场时执行 facts overview，并显式追加 `--env-file deploy/.env`。
