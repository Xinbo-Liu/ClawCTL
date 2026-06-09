# 运行态统一入口参考

## 本页解决什么问题

本页覆盖三类运行态任务：运行状态查看、deployment acceptance / runtime acceptance 默认顺序、以及最终交付前的证据归档。

详细对象解释、路径合同、dispatch 观察与恢复说明统一查看 `../architecture/control-plane-baseline.md`、`dispatch-targets.md` 与仓库根路径 `agent/README.md`。

## 适用范围

- 本页定义 **运行态入口、deployment acceptance 默认顺序，以及 runtime acceptance 证据归档**。
- runtime 服务名、容器名与 target 边界由 service registry、testing manifest 与 runtime entrypoints 共同定义。
- 当前 compose 运行治理固定显式声明为 user + cap_drop ALL + no-new-privileges + read_only + init: true + cgroup: private + pull_policy: never；镜像拉取统一前置到 one-click 的镜像阶段。
- acceptance / runtime evidence / health / recovery / dispatch observability 的固定对象路径统一回 `config/control_plane/object_families.json` 与 `agent/README.md`；本页只维护运行入口与顺序。

- 需要部署主链时回到 `../getting-started/quickstart.md`。
- 需要统一排障时回到 `troubleshooting.md`。

## 最短路径

这组命令用于默认部署后的运行态核对；若 full test 尚未通过，最后一步证据导出会失败。

```bash
bash ./scripts/runtime/show_runtime_service_status.sh
sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env
sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy
bash ./scripts/runtime/export_runtime_acceptance_evidence.sh
```

- `show_runtime_service_status.sh` 用于确认 `gateway / ingress / internal-api / scheduler` 是否在线。
- `apply_ingress_boundary_rules.sh` 用于物化 host_firewall 来源限制并写出 root 侧 evidence。
- `check_ingress_boundary_evidence.sh --require-nginx-policy` 用于确认 private ingress 边界证据已成立。
- `export_runtime_acceptance_evidence.sh` 用于在 full test 完成后导出当前机器真实运行证据。

## 常用动作

| 场景                   | 默认入口                                                                                                          | 下一步                                                                                                                                                                       |
|----------------------|---------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 统一查看服务状态             | `bash ./scripts/runtime/show_runtime_service_status.sh`                                                       | 确认启用的 runtime target 是否在线、healthy。                                                                                                                                        |
| 查看容器日志               | `bash ./scripts/runtime/show_runtime_container_logs.sh --target gateway`                                      | 需要排查单个 runtime target 的最近日志或持续跟随。                                                                                                                                         |
| 统一执行服务动作             | `bash ./scripts/runtime/run_runtime_service_action.sh restart --target gateway`                               | 需要对单个或多个 runtime target 执行 start / stop / restart / up。                                                                                                                   |
| 官方 Gateway 运行态深查     | `bash ./scripts/gateway/run_gateway_status_deep.sh`                                                           | 需要查看 official gateway status --deep、probe、安全审计等治理结果。                                                                                                                      |
| private ingress 边界证据 | `sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy` | 需要确认 private ingress 只有 80/443 暴露、内部服务不发布宿主机端口，Nginx 来源 allowlist 渲染结果、只读控制面 API Gateway token 校验与代理，且来源限制规则由宿主机防火墙或外部 ACL 语义证明时。                                         |
| 导出运行验收证据             | `bash ./scripts/runtime/export_runtime_acceptance_evidence.sh`                                                | 需要手工把 acceptance / dispatch runtime / official CLI / shadow verify 结果导出到 <current-host-state-root>/control_plane/release/evidence；one_click_deploy 默认在 full test 通过后自动执行。 |
| 交付前工作树清洁与导出          | `bash ./scripts/setup/export_clean_delivery_bundle.sh --bundle runtime-core --clean`                          | 需要导出最终仓库交付包，并阻断默认可清理目标或派生物混入 zip。                                                                                                                                         |

## runtime target / service / container 对照

| target       | compose service                    | docker container                   |
|--------------|------------------------------------|------------------------------------|
| gateway      | `openclaw-official-gateway`        | `openclaw-official-gateway`        |
| ingress      | `openclaw-private-ingress`         | `openclaw-private-ingress`         |
| internal-api | `openclaw-internal-api`            | `openclaw-internal-api`            |
| scheduler    | `openclaw-control-plane-scheduler` | `openclaw-control-plane-scheduler` |

## 运行镜像来源与 source strategy

runtime contract 与 source strategy 的正式事实统一记录在本节。

| object                 | canonical source            | selected env                      | selected pin                     |
|------------------------|-----------------------------|-----------------------------------|----------------------------------|
| `official_gateway`     | `ghcr.io/openclaw/openclaw` | `OPENCLAW_OFFICIAL_GATEWAY_IMAGE` | `config/image_pins/openclaw.env` |
| `control_plane_python` | `docker.io/library/python`  | `OPENCLAW_CONTROL_PLANE_IMAGE`    | `config/image_pins/runtime.env`  |
| `runtime_python`       | `docker.io/library/python`  | `OPENCLAW_RUNTIME_PYTHON_IMAGE`   | `config/image_pins/runtime.env`  |
| `nginx_runtime`        | `docker.io/library/nginx`   | `NGINX_IMAGE`                     | `config/image_pins/runtime.env`  |

- host readiness、部署镜像准备与运行时 compose 只接受当前 selected source；selected source 由 `config/governance/support/repo_contracts.json` 注册的 runtime contract / source strategy 与 image pin 共同定义，并与 `docs/getting-started/deployment-inputs.md` 保持一致。
- canonical source 负责供应链规范表达；acceleration source 只负责区域加速；selected source 才是当前仓库实际默认值。

### runtime contract 固定事实

- GitHub latest release API：`https://api.github.com/repos/openclaw/openclaw/releases/latest`
- official release image repo：`ghcr.io/openclaw/openclaw`
- default official gateway image repo：`ghcr.io/openclaw/openclaw`
- 允许的 Gateway candidate repos：`ghcr.io/openclaw/openclaw`、`ghcr.nju.edu.cn/openclaw/openclaw`

### acceleration source

- runtime Python acceleration repo：`docker.m.daocloud.io/library/python`
- Nginx acceleration repo：`docker.m.daocloud.io/library/nginx`

### source strategy 治理规则

1. canonical source 表示默认规范来源/官方来源；不得因为镜像代理、区域可访问性或缓存站而改写。
2. acceleration source 只表示网络适配或区域加速入口；可按环境选用，但不能单独宣称为官方供应链真源。
3. selected runtime source 表示当前仓库默认 pin 或部署输入默认值实际选中的来源；运行事实以它为准，但治理说明必须同时回指 canonical / acceleration 分层。
4. Gateway 官方镜像的 canonical repo、默认 promote repo 与候选仓库集合统一以 runtime contract 为真源；source strategy 负责分层说明、profile 与切换顺序，并直接引用这套 repo 判定。
5. 模型/API provider 入口、模型名与扩展业务变量由 profile、model registry 与 deploy env schema 治理，不纳入 runtime image source strategy；本策略只描述 Gateway、Python 与 Nginx 等运行镜像来源。
6. control plane Python / runtime Python / Nginx 中国网络默认 pin 固定为 Daocloud tag@digest；canonical source 使用 docker.io 表达供应链规范，Docker daemon registry-mirrors 仅作为补充传输加速。
7. control plane Python / runtime Python / Nginx 宿主机预检只验证本地缓存与 Docker 传输链路；精确 tag@digest artifact 可用性统一在 check_deployment_image_readiness.sh、pull_images.sh 与部署镜像归档链路闭合。
8. 宿主机静态预检、Gateway 供应链事实与 registry manifest 探针固定走 bash + jq + curl 静态链路；这些入口不准备 host 控制面执行介质，进入 host 控制面命令前必须显式执行 prepare_control_plane_medium.sh。
9. Gateway 宿主机预检必须复用统一供应链事实，明确区分 selected source 不可达、selected source digest 与 pin 不一致、以及候选链路可用三种状态。
10. pull_images.sh 默认按 PULL_GATEWAY_CANDIDATE_MODE=auto-switch 在 CN profile 下选择等值 Gateway candidate；该选择只写当前 deploy/.env 与 state/image_pull/gateway_source_selection.json，不改写 config/image_pins/openclaw.env。

<a id="manual-post-deploy-checks"></a>
## 首次部署后的人工补充核对

完成 `quickstart.md` 默认步骤后，需要人工确认运行态关键产物与官方 Gateway 深查入口时，统一看本节。

### 最短人工核对顺序

```bash
bash ./scripts/runtime/show_runtime_service_status.sh
bash ./scripts/doctor/check_internal_api_runtime.sh
bash ./scripts/doctor/check_control_plane_runtime.sh
sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy
bash ./scripts/doctor/check_openclaw_official_runtime_contract.sh
```

### 关键运行产物

- `openclaw.json`
- `cron/jobs.json`
- `workspace-router_local_ro/{AGENTS.md,SOUL.md,TOOLS.md,IDENTITY.md,USER.md,HEARTBEAT.md,BOOTSTRAP.md,MEMORY.md}`
- `workspace-<agent_id>/{AGENTS.md,SOUL.md,TOOLS.md,IDENTITY.md,USER.md,HEARTBEAT.md,BOOTSTRAP.md,MEMORY.md}`
- `agents/main/agent/{AGENTS.md,SOUL.md,TOOLS.md,IDENTITY.md,USER.md,HEARTBEAT.md,BOOTSTRAP.md,MEMORY.md}`
- `agents/<agent_id>/agent/{AGENTS.md,SOUL.md,TOOLS.md,IDENTITY.md,USER.md,HEARTBEAT.md,BOOTSTRAP.md,MEMORY.md}`
- `agents/{main,<agent_id>}/sessions/{sessions.json,<default-session>.jsonl}`
- `exec-approvals.json`
- `runtime.gateway.env`
- `runtime.scheduler.env`
- `runtime.internal-api.env`
- `nginx.gateway.conf`

### 需要同时核对的点

- `check_openclaw_official_runtime_contract.sh`、`check_runtime_evidence_prereqs.sh` 与 `export_runtime_acceptance_evidence.sh` 默认继承 `deploy/.env` 的 active control-plane profile；需要显式覆盖时设置 `OPENCLAW_CONTROL_PLANE_PROFILE`。
- `OPENCLAW_TLS_CN` 渲染为唯一访问主机名；
- `<current-host-state-root>/gateway/openclaw.json`、`cron/jobs.json`、`workspace-router_local_ro/`、`agents/*/agent/`、`agents/*/sessions/sessions.json` 与 `workspace-<agent_id>/` 核心文件由当前 active control-plane profile 投影出 Gateway UI 默认路由 agent、业务 agents、agent 文件面、聊天默认会话与定时任务目录；
- `<current-host-state-root>/gateway/nginx.gateway.conf` 已按当前 HSTS、访问主机名、来源 CIDR allowlist、只读控制面 API Gateway token 校验与代理重渲染；
- official Gateway token auth 配置存在；
- 访问端浏览器使用 `https://<OPENCLAW_TLS_CN>/` 打开 Control UI，WebSocket URL 使用 `wss://<OPENCLAW_TLS_CN>`，并填入 `OPENCLAW_GATEWAY_TOKEN`；
- private ingress 是唯一宿主机入口，official Gateway 是 Gateway UI 的认证入口；
- `ingress_boundary_evidence.json` 已写出，且 compose/runtime 只允许 private ingress 发布 80/443，Nginx allowlist、只读控制面 API Gateway token 校验、代理与基础设施边界证据均已闭合；
- `doctor`、`security audit --deep --json` 与 `models probe` 的聚合检查通过。

### 首次访问 Control UI

浏览器首连按固定顺序判断：证书警告先按 `troubleshooting.md#control-ui-certificate-warning` 确认访问端信任链与主机名；`origin not allowed` 表示访问端没有使用 `OPENCLAW_TLS_CN` 或 WebSocket URL 主机名不一致；`gateway token missing` / `unauthorized` 表示未填 `OPENCLAW_GATEWAY_TOKEN` 的真实值；`disconnected (1008): pairing required` 表示 token 已通过、需要批准新设备。SSH 隧道访问仍必须打开 `https://<OPENCLAW_TLS_CN>/`，不能使用 `http://localhost:443/`。浏览器本地凭据丢失或更换浏览器时，重新读取当前 `OPENCLAW_GATEWAY_TOKEN` 并批准新设备，不重新部署、不重新生成 token。首连异常处理顺序统一回到 `troubleshooting.md#control-ui-certificate-warning`、`troubleshooting.md#control-ui-origin-not-allowed`、`troubleshooting.md#control-ui-gateway-token-missing`、`troubleshooting.md#control-ui-token-recovery` 与 `troubleshooting.md#control-ui-first-pairing`。

## deployment acceptance 与 runtime acceptance

本节是当前仓库对“先形成 deployment acceptance，再导出 runtime acceptance 证据”的唯一固定口径。

<a id="deployment-acceptance-default-flow"></a>
### deployment acceptance 默认顺序

```bash
bash ./scripts/setup/one_click_deploy.sh
bash ./scripts/runtime/run_openclaw_python_tool.sh runtime acceptance acceptance-summary
```

- 若当前 profile / extension 声明 `required_run_ledger_jobs`，`one_click_deploy.sh` 会在 full test 前自动执行 `run_control_plane_run_all_once.sh` 生成当前机器真实 run ledger；发送动作按当前 target 配置执行。当前 target 配置不允许发送时，使用 `--skip-acceptance` 仅启动服务，并把 deployment acceptance / runtime acceptance evidence 未闭合作为显式交接状态。

使用 `--skip-acceptance` 或 `--prepare-only` 后，先确认 runtime 服务已启动，再按 run ledger 状态闭合 deployment acceptance 与 runtime evidence：

- required run ledger jobs 缺失或失败时，使用 `post_deploy_acceptance` 执行 required jobs、full test 与 runtime evidence；发送动作按当前 target 配置执行。
- required run ledger jobs 已 accepted，仅 full test 或 runtime evidence 未闭合时，使用 `post_deploy_full_acceptance` 执行 full test 与 runtime evidence，且跳过 run_all_once。

```bash
bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance
bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance
bash ./scripts/runtime/run_openclaw_python_tool.sh runtime acceptance acceptance-summary
```

需要单独查看结构化 full test 摘要时，执行 `bash ./scripts/setup/one_click_test_full.sh --json`；该命令不替代部署恢复入口。

需要候选实例对照时，只在导出前额外执行：

```bash
bash ./scripts/gateway/run_shadow_upgrade_verify.sh --require-candidate-runtime
bash ./scripts/runtime/export_runtime_acceptance_evidence.sh
```

- `run_shadow_upgrade_verify.sh` 的摘要渲染固定通过控制面容器执行；Docker daemon 或控制面镜像未就绪时脚本直接失败。

<a id="deployment-acceptance-pass-criteria"></a>
### deployment acceptance 通过标准（不含 runtime evidence）

- `gateway / ingress / internal-api / scheduler` 全部在线且 healthy。
- full test 默认 required checks 没有 blocking FAIL。
- required run ledger jobs 可采集且 execution / artifact evidence 均通过；执行失败、job 缺失、artifact root 缺失或声明输出没有可接受 evidence 都会阻断 deployment acceptance。
- `deployment_acceptance.json` 同时满足 `eligible=true` 与 `accepted=true`。
- run ledger、dispatch runtime、shadow verify 与 official CLI 深查属于 runtime acceptance 证据范围，不并入 deployment acceptance state 本身。

<a id="deployment-acceptance-artifacts"></a>
### deployment acceptance 与 runtime acceptance 证据产物

| 路径                                                                                                  | 写出者                                     | 作用                                                                                                                                                       |
|-----------------------------------------------------------------------------------------------------|-----------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| `<current-host-state-root>/control_plane/setup/deployment_acceptance.json`                          | `one_click_test_full.sh`                | 默认全量 full 验证完成后写出的 deployment acceptance state，只表达 eligible / accepted 与 required_checks，不内嵌 runtime evidence。                                           |
| `<current-host-state-root>/control_plane/setup/ingress_boundary_evidence.json`                      | `check_ingress_boundary_evidence.sh`    | private ingress 边界证据摘要；记录 compose/runtime 端口暴露事实、Nginx allowlist，以及宿主机防火墙或外部 ACL 证据文件的检查结果。                                                              |
| `<current-host-state-root>/control_plane/release/evidence/runtime-acceptance.json`                  | `export_runtime_acceptance_evidence.sh` | 把 deployment acceptance state、control-plane scheduler runtime、run ledger 与官方 CLI 摘要聚合后的运行验收证明。                                                           |
| `<current-host-state-root>/control_plane/release/evidence/control-plane-run-ledger.json`            | `export_runtime_acceptance_evidence.sh` | 控制平面 run ledger 验收摘要；逐 job 回收 run / result / artifacts manifest，并以 artifactRoot、evidenceSources、observedEntries 与 schedulerEntries 判断 artifact evidence。 |
| `<current-host-state-root>/control_plane/release/evidence/control-plane-job-artifact-policies.json` | `export_runtime_acceptance_evidence.sh` | 控制平面 job artifact policy 摘要；逐 job 固定记录 runArtifactRoot、latestAlias、retentionDays 与 scheduler run manifest 模式。                                            |
| `<current-host-state-root>/control_plane/release/evidence/official-cli-summary.control-plane.json`  | `export_runtime_acceptance_evidence.sh` | 官方 Gateway 容器内 openclaw doctor / security audit / models probe 的聚合摘要。                                                                                    |
| `<current-host-state-root>/control_plane/release/evidence/shadow-verify-summary.json`               | `export_runtime_acceptance_evidence.sh` | 候选 Gateway 隔离实例的影子验证摘要；在 shadow verify 产物存在时由导出脚本同步写入。                                                                                                   |
| `<current-host-state-root>/control_plane/release/evidence/shadow-verify-summary.md`                 | `export_runtime_acceptance_evidence.sh` | shadow-verify-summary.json 的人工阅读版摘要。                                                                                                                     |
| `<current-host-state-root>/control_plane/release/evidence/shadow-verify-compare.json`               | `export_runtime_acceptance_evidence.sh` | 影子验证候选控制面的隔离实例计划与 active/candidate 差异摘要；用于证明候选实例与正式运行面没有共用状态目录、容器名或网络。                                                                                   |
| `<current-host-state-root>/control_plane/release/evidence/shadow-verify-compare.md`                 | `export_runtime_acceptance_evidence.sh` | shadow-verify-compare.json 的人工阅读版摘要。                                                                                                                     |

- runtime evidence 统一写入 `<current-host-state-root>/control_plane/release/evidence/`，该目录属于 control-plane state 的 owner-only 运行验收面。
- runtime acceptance 以最新有效运行事实为准；control-plane run ledger 同时使用 executionAccepted / effectiveExecutionAccepted 与 artifactAccepted / artifactEffectiveAccepted 判断 required job 闭合，artifact manifest 固定记录 artifactRoot、evidenceSources、observedEntries 与 schedulerEntries。
- secrets / 私钥 / 可写运行态状态不得导出到 runtime evidence；敏感物料继续留在受控 state / certs / env 面。

### required checks

- `compose_ps`
- `gateway_https_root`
- `gateway_healthz`
- `ingress_boundary_evidence`
- `internal_api_runtime`
- `control_plane_registry`
- `control_plane_runtime`
- `official_openclaw_cli`

group 级发布门禁若需要对齐 deployment acceptance required checks，统一通过 `bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane evidence agent-group-acceptance-bindings` 与 `/v1/control-plane/agent-group-acceptance-bindings` 查看正式映射摘要。

## 失败分流

| 当前现象                                        | 先跳哪里                                                      |
|---------------------------------------------|-----------------------------------------------------------|
| 服务不在线、健康异常、日志异常                             | `troubleshooting.md#runtime-与-ingress-问题`                 |
| ingress 边界证据不通过                             | `troubleshooting.md#runtime-与-ingress-问题`                 |
| full test 未闭合或 acceptance state 不通过         | `troubleshooting.md#full-test-与-deployment-acceptance-问题` |
| runtime evidence / clean release 导出失败       | `troubleshooting.md#验收归档与交付导出问题`                          |
| 需要对象路径、run ledger、dispatch observability 长表 | `agent/README.md`、`dispatch-targets.md`                   |

## 下一步

- 排障总入口：`troubleshooting.md`
- dispatch target 首次接入：`dispatch-targets.md`
- deployment 主链回看：`../getting-started/quickstart.md`
