# 控制平面基线

本项目通过 **OpenClaw 官方 Gateway** 承接外部认证与 runtime 接入，通过 **private HTTPS ingress** 暴露外部入口，由 **Python control plane** 统一调度，并以 **target adapter** 作为外部分发适配层。agent 治理保持可插拔、可组装、可统一管理的平台对象结构。

## 运行分层

- `config/control_plane/service.json`：纯 kernel / base 基线。
- `config/control_plane/profiles/agent_platform.service.json`：正式默认运行 profile。
- `config/control_plane/extensions.d/agent_platform.json`：主仓库内的平台扩展。
- `agent/control_plane/`：共享 runtime / registry / object policy 目录。
- `agent/governance/`：当前平台治理规则与仓内 extension authoring 合同。
- `python/openclaw/lib/repo/control_plane_config_surface.py`、`scripts/lib/control_plane_config_paths.sh` 与 `bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane config <host-path|container-path|profile-id>`：控制面 profile 选择、显式 `--config-path`、环境变量优先级与 host/container 路径映射的统一解析面。

仓内 extension 通过正式 profile、有效自动发现 profile 或仓内合同 service 的显式 `--config-path` 接入。
Shell 侧固定复用该解析面或其薄包装结果，不在脚本层复制 profile/path 推导逻辑。

## 运行拓扑

1. private HTTPS ingress 承载唯一宿主机入口。
2. `/`、`/healthz` 与 `/readyz` 转发到 official Gateway。
3. `/v1/control-plane/*` 与 `/v1/config/summary` 作为只读控制面 API，经 private ingress 校验 Gateway token 并注入内部 token 后转发到 `internal-api`。
4. `internal-api` 与 `control-plane scheduler` 保持 internal bridge 内部服务，不发布宿主机端口。
5. control plane 解析 `agent_platform` 与共享对象根，驱动 runtime、dispatch、diagnostics、recovery 等通用表面。
6. 业务 target 通过业务 extension 提供的 dispatch target registry 装配进入；provider adapter 由当前 profile 启用的 provider registry 解析，`agent_platform` 提供中性 synthetic provider，公共 provider 扩展提供通道 provider。

## 正式平台资产

- 业务 dispatch registry：`agent/extensions/<extension-id>/agent/control_plane/registries/dispatch_targets.json`，由启用业务扩展的 manifest 加载。
- 平台 provider registry：`agent/control_plane/registries/dispatch_provider_adapters.json`，承载中性 synthetic provider 与 registry 机制。
- 公共 provider registry：`agent/extensions/<extension-id>/agent/control_plane/registries/dispatch_provider_adapters.json`，由声明 `registry.dispatchProviderRegistryPaths` 的公共 provider 扩展提供。
- 共享 runtime adapter：`agent/control_plane/runtime/runtime_adapters.json`
- 平台治理路径与对象族：`config/control_plane/extensions.d/agent_platform.runtime_paths.json`、`config/control_plane/extensions.d/agent_platform.object_families.json`

平台与公共 provider 扩展暴露的 provider registry 入口：

- `agent_platform.registry.dispatchProviderRegistryPaths[0] -> @repo/agent/control_plane/registries/dispatch_provider_adapters.json`
- `<extension-id>.registry.dispatchProviderRegistryPaths[] -> @extension/agent/control_plane/registries/dispatch_provider_adapters.json`

业务扩展暴露的 registry 入口固定为 `<extension-id>.registry.dispatchTargetRegistryPaths[]`，路径必须解析在该扩展根目录内。

## 固定结论

1. base 不承载业务对象。
2. `agent_platform` 是默认运行入口。
3. `agent_platform` 只承载通用 runtime / governance surface 与中性 provider registry 机制；agent module、job、group、model、target 与通道 provider 由仓内 extension 提供。
4. 主仓库中的 dispatch 平台治理保持去业务化；业务 target registry 只由业务 extension 启用，通道 provider 只由声明 provider registry 的公共 provider 扩展启用。
5. 多 extension 组合统一通过 `extensions.manifestsDirs` 与 `activation.enabledExtensionIds` 装配。

## External dispatch 接受合同

- 调度器为每个已启用 `externalDispatch` job 创建独立运行目录，并注入 outcome path、job ID 和 scheduler run ID；受控单 job 执行还注入期望 `businessRunId`。
- 扩展通过公共原子写入器生成符合 `config/control_plane/schemas/delivery_outcome.schema.json` 且 `schemaVersion=1` 的 delivery outcome。调度器分别计算 `processAccepted`、`contractAccepted`、`artifactAccepted`、`executionAccepted` 和 `acceptedByLedger`。
- outcome 缺失、损坏、身份错配、未知状态、路径越界、哈希错误或过期证据统一归类为 `target_contract_violation` 并 fail closed。子进程非零不能被业务 `sent` 覆盖；子进程为零也不能把 `failed` 或 `blocked` 包装成成功。
- delivery job 只接受 manifest 精确列出的证据。当前运行证据必须满足运行根边界、SHA-256 和 `mtime >= runStartedAt - 5s`；合法 same-run `noop` 可以引用更早的不可变 sent 证明，但该证明仍须通过身份、账本和 provider/人工语义校验。
- outcome 只保存 provider HTTP 状态、脱敏业务码和错误分类，不保存 endpoint、secret 或完整响应体。
- pipeline 是聚合状态唯一真源；router hints 等 projection 必须携带同一 `pipelineSnapshotId`，不匹配时消费者忽略投影并 fail closed 或从 pipeline 重算。
