# Agent Control Plane Shared Objects

`agent/control_plane/` 是共享 agent-plane 对象根，不是 base control-plane 基座目录。

## 目录边界

- `registries/`：共享 dispatch / provider registry 真源。
- `runtime/`：共享 runtime adapter 与运行装配资产。
- `job_artifact_policy_surface.json`：运行验收与 artifact policy 真源。
- jobs / groups / models / targets 仍属于共享对象类别，由仓内 extension 提供正式实例。

## 装配规则

- `config/control_plane/service.json` 是零业务、零 extension 的 base 入口。
- `config/control_plane/profiles/agent_platform.service.json` 是主仓库内正式默认运行 profile，对应 `config/control_plane/extensions.d/agent_platform.json`；受控组合 profile 通过 `config/control_plane/profile_registry.tsv` 与 `config/control_plane/repo_combination_profiles.json` 显式登记。
- 共享对象必须显式声明 `activation.enabledExtensionIds`，loader 只加载与启用 extension 命中的对象。
- 仓内扩展如需共享对象，只能通过普通 extension 机制进入。

## 约束

1. 不在本目录回写 base service 基座定义。
2. 不为 agent / implementation / contract 维护并列人工快照。
3. 不通过无校验目录扫描把扩展对象塞回 base；所有扩展都必须走 profile / extension 装配链路。
