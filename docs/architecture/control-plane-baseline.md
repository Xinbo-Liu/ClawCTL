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
6. 业务 target 通过业务 extension 提供的 dispatch target registry 装配进入，provider adapter 由平台 registry 提供。

## 正式平台资产

- 业务 dispatch registry：`agent/extensions/<extension-id>/agent/control_plane/registries/dispatch_targets.json`，由启用业务扩展的 manifest 加载。
- 共享 provider registry：`agent/control_plane/registries/dispatch_provider_adapters.json`
- 共享 runtime adapter：`agent/control_plane/runtime/runtime_adapters.json`
- 平台治理路径与对象族：`config/control_plane/extensions.d/agent_platform.runtime_paths.json`、`config/control_plane/extensions.d/agent_platform.object_families.json`

平台扩展暴露的 registry 入口：

- `registry.dispatchProviderRegistryPaths[0] -> @repo/agent/control_plane/registries/dispatch_provider_adapters.json`

业务扩展暴露的 registry 入口固定为 `<extension-id>.registry.dispatchTargetRegistryPaths[]`，路径必须解析在该扩展根目录内。

## 固定结论

1. base 不承载业务对象。
2. `agent_platform` 是默认运行入口。
3. `agent_platform` 只承载通用 runtime / governance surface；agent module、job、group、model 与 target 由仓内 extension 提供。
4. 主仓库中的 dispatch 平台治理保持去业务化；业务 target registry 只由业务 extension 启用。
5. 多 extension 组合统一通过 `extensions.manifestsDirs` 与 `activation.enabledExtensionIds` 装配。
