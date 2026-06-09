# ClawCTL 基座控制面

当前初始基座版本：`0.1.0`（source-available / 非商业源码可见）。

## 项目定位

本仓库交付一套 OpenClaw 平台运行面：通过 OpenClaw 官方 Gateway 承接外部认证与 runtime 接入，通过 private HTTPS ingress 暴露唯一外部入口，通过 Python control plane 提供调度、诊断、运行证据与 agent 扩展装配能力。

这个仓库不是单一业务应用包。默认运行面保持平台化，不内置业务链路；业务能力通过仓内显式 extension 按受控 profile 或仓内合同 service 接入。

## 默认运行面

默认部署只覆盖平台服务对象：

- `openclaw-private-ingress`：唯一宿主机 HTTPS 入口，承接外部访问边界。
- `openclaw-official-gateway`：官方 Gateway 运行态，承接 OpenClaw runtime 接入与 Gateway token 认证。
- `openclaw-internal-api`：内部只读控制面 API，向 ingress 暴露受控摘要与状态查询。
- `openclaw-control-plane-scheduler`：Python control plane 调度器，驱动运行证据、dispatch、diagnostics 与 recovery 表面。

默认运行 profile 是 `config/control_plane/profiles/agent_platform.service.json`。该 profile 只启用平台扩展 `config/control_plane/extensions.d/agent_platform.json`，不携带业务 job、module、group、model 或 target。

## 分层模型

- `base control-plane`：由 `config/control_plane/service.json` 定义，提供内核配置、schema、注册表加载、路径合同与通用控制面能力。
- `agent_platform`：默认平台 profile 与平台 extension，提供通用 runtime、registry、object policy、dispatch provider 与治理表面。
- 仓内显式 extension：位于 `agent/extensions/<extension-id>/`，自带 service profile、manifest、模块、jobs、groups、models、targets、运行路径和局部文档；目录存在本身不进入默认运行面。
- 部署与运行层：由 private HTTPS ingress、official Gateway、internal-api、scheduler 与 runtime evidence 共同形成可部署、可验收、可值守的交付面。

仓内 extension 只能通过仓库内正式 profile、有效自动发现 profile，或指向仓内标准 extension service 的显式 `--config-path` 接入。仓外非标准 manifest 目录不属于兼容入口。

## 阅读路径

- 外部评审先看项目目标态、支持边界与合规材料：[`VISION.md`](VISION.md)、[`docs/architecture/supported-deployment-boundary.md`](docs/architecture/supported-deployment-boundary.md)、[`LICENSE`](LICENSE)、[`COMMERCIAL_LICENSE.md`](COMMERCIAL_LICENSE.md)、[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
- 交付接手先看平台结构、主路径与文档总导航：[`docs/README.md`](docs/README.md)、[`docs/architecture/control-plane-baseline.md`](docs/architecture/control-plane-baseline.md)、[`docs/architecture/platform-main-path.md`](docs/architecture/platform-main-path.md)。
- 维护接手先看配置真源、生成文档、脚本分组、运行服务与证据路径：[`docs/operations/maintenance-map.md`](docs/operations/maintenance-map.md)。
- 部署执行从正式部署路径进入：[`docs/getting-started/quickstart.md`](docs/getting-started/quickstart.md)。
- 运行验收、值守与排障从 operations 入口进入：[`docs/operations/runtime-service-reference.md`](docs/operations/runtime-service-reference.md)、[`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md)、[`docs/operations/security-boundary.md`](docs/operations/security-boundary.md)。
- 扩展开发与 agent plane 治理从 agent 入口进入：[`agent/README.md`](agent/README.md)、[`agent/extensions/README.md`](agent/extensions/README.md)、[`docs/architecture/explicit-extension-packages.md`](docs/architecture/explicit-extension-packages.md)。
- 脚本定位只在确定场景后进入：[`scripts/README.md`](scripts/README.md)。

## 交付与合规入口

- 干净交付包导出：`bash ./scripts/setup/export_clean_delivery_bundle.sh --bundle runtime-core --clean`
- 非商业源码可见许可文本：[`LICENSE`](LICENSE)
- 商业授权说明：[`COMMERCIAL_LICENSE.md`](COMMERCIAL_LICENSE.md)
- 第三方组件与许可声明：[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)

发布前静态门禁入口为 `bash ./scripts/doctor/run_repo_release_gate.sh`。验证层级以 `config/governance/support/verification_tiers.json` 为真源；正式 release pass 固定走 Docker / 控制面容器门禁，Windows 宿主机诊断回归只作为本地定位补充。仓库级 Python 回归、静态治理和通用 control-plane 命令入口以 [`docs/architecture/supported-deployment-boundary.md`](docs/architecture/supported-deployment-boundary.md) 中的支持边界为准。

## 维护边界

- 本页只说明项目定位、默认运行面、阅读路径、交付与合规入口，不展开部署步骤、验收细节或局部实现说明。
- `docs/` 承载项目级正式文档；`agent/` 承载 agent plane 治理与仓内 extension authoring 合同；`scripts/` 提供脚本索引。
- 派生文档只从对应配置、注册表或生成链更新，不把生成结果作为单独维护面。
- 本页保持平台去业务化；具体业务扩展的说明进入 `agent/extensions/README.md` 与各扩展包自己的 README。
