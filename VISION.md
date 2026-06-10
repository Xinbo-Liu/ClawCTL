# ClawCTL Vision / 项目目标态

ClawCTL 的目标是提供一个可审计、可部署、可扩展的 OpenClaw 基座控制面。它把 runtime ingress、Gateway 接入、Python control plane、scheduler、internal API、运行证据、发布治理和 managed extension 合同收口在一个 source-available, non-commercial repository 中。

ClawCTL aims to provide an auditable, deployable, and extensible base control plane for OpenClaw runtime operations. It keeps private ingress, Gateway integration, Python control-plane services, scheduler automation, internal API checks, runtime evidence, release governance, and managed extension contracts in one source-available, non-commercial repository.

## Product Position / 产品定位

ClawCTL is:

- a base control-plane repository for OpenClaw runtime deployment;
- a governance surface for release gates, clean delivery bundles, third-party notices, and runtime evidence;
- a managed extension platform that keeps business capabilities outside the base release surface;
- a deployment and operations toolkit for private OpenClaw runtime boundaries.

ClawCTL 是：

- OpenClaw runtime 部署基座控制面；
- 发布门禁、干净交付包、第三方声明和运行证据的治理面；
- 将业务能力隔离在基座发布面之外的 managed extension 平台；
- 面向私有 OpenClaw runtime 边界的部署与运维工具链。

## Supported Outcome / 支持的落地结果

一个合格的 ClawCTL 基座部署应具备以下结果：

- private HTTPS ingress 是唯一外部入口；
- official Gateway 承接 OpenClaw runtime 接入与 token auth；
- `agent_platform` profile 提供默认平台运行面；
- internal API 和 scheduler 可提供只读状态、调度、diagnostics、evidence 与 recovery 表面；
- clean delivery bundle 可从仓库真源导出；
- Docker release gate 与 strict stack verify 可证明发布面闭合；
- managed extension 可以按仓内合同显式加入，但不会污染默认基座。

A valid ClawCTL base deployment should provide:

- one private HTTPS ingress boundary;
- official Gateway access and token authentication;
- the `agent_platform` default runtime profile;
- internal API and scheduler surfaces for status, diagnostics, runtime evidence, and recovery;
- clean delivery bundle export from repository truth;
- Docker release gate and strict stack verification;
- managed extension onboarding without adding business-specific logic to the base release surface.

## Non-Goals / 非目标

ClawCTL does not aim to be:

- an OSI-approved open source project;
- a hosted SaaS product;
- a business agent suite;
- a prebuilt customer-specific workflow package;
- a repository for real credentials, production certificates, private env files, or generated runtime state.

ClawCTL 不以以下事项为目标：

- 成为 OSI 批准的开源项目；
- 提供托管 SaaS；
- 内置业务 agent 套件；
- 提供客户定制业务流程包；
- 保存真实凭据、生产证书、私有 env 文件或生成态运行数据。

## Release Boundary / 发布边界

The public base release surface contains only `base` and `agent_platform` capabilities. Business profiles, business agents, business jobs, business models, business listeners, customer scripts, generated credentials, certificate output, runtime state, and private env files must stay outside the base release.

公开基座发布面只包含 `base` 与 `agent_platform` 能力。业务 profile、业务 agent、业务 job、业务模型、业务 listener、客户脚本、生成凭据、证书输出、运行态 state 和私有 env 文件都不得进入基座发布面。

## Governance Principle / 治理原则

ClawCTL treats documentation, scripts, manifests, profiles, Compose files, and release bundles as one contract. Any change that affects deployment behavior, supported runtime shape, extension authoring, licensing, or release cleanliness must update the corresponding source of truth and pass the release gate before publication.

ClawCTL 将文档、脚本、manifest、profile、Compose 文件和发布包视为同一个合同。任何影响部署行为、支持运行面、扩展开发、许可口径或发布洁净度的修改，都必须更新对应真源，并在公开前通过发布门禁。
