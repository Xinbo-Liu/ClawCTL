# Dispatch Targets

该页面描述 dispatch target 的通用治理入口。业务扩展的 target 清单、默认绑定与发送边界由启用的 service profile 决定。

## 边界

- `agent_platform` 是中性平台扩展，只提供 provider adapter registry、runtime paths、object families 与 dispatch operations surface。
- `agent_platform` 不装入业务 dispatch target registry，也不提供默认 target。
- 业务扩展通过自身 manifest 的 `registry.dispatchTargetRegistryPaths` 启用 dispatch target registry。
- UI、脚本和人工命令都应以当前 service profile 为准读取 `dispatchTargetRegistryPaths`；只启用 `agent_platform` 时，不应解析出业务 target。

## Profile 入口

- `deploy/site.env` 的 profile 入口是 `OPENCLAW_CONTROL_PLANE_PROFILE=<profile-id>`；`OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH` 由 `one_click_config.sh` 写入 `deploy/.env`，不作为人工填写项。
- 平台 profile：`config/control_plane/profiles/agent_platform.service.json`
- 业务 profile：`agent/extensions/<extension-id>/config/control_plane/profiles/<extension-id>.service.json`
- 受控组合 profile：`config/control_plane/profiles/<combination-profile-id>.service.json`
- 业务 target registry：`agent/extensions/<extension-id>/agent/control_plane/registries/dispatch_targets.json`，由 `<extension-id>.registry.dispatchTargetRegistryPaths` 启用。
- 组合 profile 是否启用 dispatch target registry，以 `config/control_plane/repo_combination_profiles.json` 与各扩展 manifest 的 registry 声明为准。
- provider registry：`agent/control_plane/registries/dispatch_provider_adapters.json`，由组合 service 中启用的 `agent_platform` 提供。
- 运行时路径与对象族：`config/control_plane/extensions.d/agent_platform.runtime_paths.json`、`config/control_plane/extensions.d/agent_platform.object_families.json`。

## 通用命令

- 列出平台治理入口：`bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops show-index --control-plane-profile agent_platform`
- 汇总业务候选 target：`bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops collect-targets --gate-env-file deploy/.env --control-plane-profile <profile-id>`
- 执行单个 target preflight：`bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops run-target-operation --operation preflight --target <target_id> --env-file deploy/.env --control-plane-profile <profile-id> --ensure-running strict`
- 执行单个 target dry-run：`bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops run-target-operation --operation send --target <target_id> --env-file deploy/.env --control-plane-profile <profile-id> --ensure-running strict -- --dry-run true`
- 查看单个 target acceptance：`bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch observability show-target-acceptance --target <target_id> --gate-env-file deploy/.env --fail-on-fail --json`
- 查看治理对象：`bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch observability objects --control-plane-profile <profile-id>`
- 查看 batch acceptance：`bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch observability show-batch-acceptance --batch <batch_id> --gate-env-file deploy/.env --json --fail-on-warn`
- 查看健康总览：`bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch observability show-health-overview --gate-env-file deploy/.env --json --fail-on-fail`

## 固定规则

- 人工只改 `deploy/site.env`、启用扩展内部 `agent/extensions/<extension-id>/deploy/extension.env` 与 `deploy/targets.d/<target_id>.env`。
- `deploy/targets.d/*.env.example` 由 `one_click_config.sh` 按当前 active profile 本地生成，不进入仓库；真实 Webhook 与签名密钥只写入部署环境的 `*.env`。
- 扩展内 `dispatch_targets.json` 与共享 provider registry 是只读合同真源。
- `boundary.dispatchLane`、`boundary.payloadScope` 与 `boundary.publishLatestDefault` 是运行态职责边界；业务正式播报是唯一推进 dispatch latest 的目标，监控和联调只保留目标级运行记录。
- 仓内 extension 若复用主仓库 dispatch 合同，必须通过正式 profile、有效自动发现 profile 或仓内合同 service 的显式 `--config-path` 接入，不进入主仓库默认运维入口。
