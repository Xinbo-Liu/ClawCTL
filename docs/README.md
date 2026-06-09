# OpenClaw 项目文档导航

`docs/` 维护项目级正式文档，负责把平台结构、部署主线、运行验收、排障与安全边界分流到对应页面。项目概览说明交付对象与边界；本页只做文档导航和职责分工。

## 项目级阅读入口

| 任务或主题       | 入口                                                                                             |
|-------------|------------------------------------------------------------------------------------------------|
| 项目结构与运行分层 | [architecture/control-plane-baseline.md](architecture/control-plane-baseline.md)               |
| 平台部署主路径   | [architecture/platform-main-path.md](architecture/platform-main-path.md)                       |
| 支持范围与不支持范围 | [architecture/supported-deployment-boundary.md](architecture/supported-deployment-boundary.md) |
| 正式部署主线    | [getting-started/quickstart.md](getting-started/quickstart.md)                                 |
| 维护事实总览    | [operations/maintenance-map.md](operations/maintenance-map.md)                                 |
| 运行验收和值守入口 | [operations/runtime-service-reference.md](operations/runtime-service-reference.md)             |
| 安全边界      | [operations/security-boundary.md](operations/security-boundary.md)                             |
| Agent 治理目录 | [../agent/README.md](../agent/README.md)                                                       |

## 目录职责

- `architecture/`：项目结构、运行分层、支持边界、路径治理与 extension 装配约束。
- `getting-started/`：部署输入、宿主机前提、部署步骤与部署任务入口。
- `operations/`：运行验收、值守、排障、dispatch 治理与安全边界任务入口。
- `../agent/`：agent plane 治理规则、共享对象合同与仓内 extension authoring 入口。

显式扩展包的正式挂载方式见 [architecture/explicit-extension-packages.md](architecture/explicit-extension-packages.md)。

## 维护边界

- `docs/` 只维护项目级正式文档，不复制模块局部实现说明或仓库外 extension 内容。
- 主仓库正式文档覆盖 kernel、`agent_platform`、部署主链、运行验收与通用 extension 机制；仓内扩展通过正式 profile、有效自动发现 profile 或仓内合同 service 的显式 `--config-path` 接入。
- 目标态边界统一记录在 [../VISION.md](../VISION.md)。
- 结构、边界与入口说明统一以正式页面为准。
