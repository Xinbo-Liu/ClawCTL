# 安全边界说明

本页定义 OpenClaw 主仓库正式支持面的安全边界。默认正式支持面由 `base` kernel 与 `agent_platform` 纯平台 profile 构成；受控组合 profile 通过仓内合同 manifest 目录显式启用白名单声明的受管扩展。业务模块、示例链路与私有对象只能通过已登记 profile、有效自动发现 profile 或仓内合同 service 接入。

## 网络拓扑

private HTTPS ingress 承载唯一宿主机入口。普通 UI 与 Gateway 健康路径转发到官方 Gateway；只读控制面 API 路径先在 ingress 校验 Gateway token，再注入内部 token 后转发到 internal-api。

- `openclaw-private-ingress`：private HTTPS ingress，对外只承担单一入口，并负责来源 allowlist、TLS/HSTS、Gateway 反向代理与只读控制面 API 代理。
- `openclaw-official-gateway`：唯一允许接受外部认证流量的 Gateway。
- `openclaw-internal-api`：内部 API、状态汇总与 run ledger 入口，只允许位于内部网络；外部可见面仅限 private ingress 代理的只读控制面路由。
- `openclaw-control-plane-scheduler`：统一控制面 scheduler，只允许位于内部网络。

## 固定规则

1. 外部流量只能先进入 `openclaw-private-ingress`；Gateway UI 路径转发到 `openclaw-official-gateway`，只读控制面 API 路径转发到 `openclaw-internal-api`。
2. `openclaw-internal-api` 与 `openclaw-control-plane-scheduler` 不得直接暴露到宿主机外部地址。
3. `OPENCLAW_GATEWAY_TOKEN`、`OPENCLAW_INTERNAL_API_TOKEN`、容器 `UID:GID`、内部网络边界与 ingress 来源限制必须在 `deploy/.env` 中显式存在；token 由 `one_click_config.sh` 生成或保留已有 `deploy/.env` 值，不作为 `deploy/site.env` 人工填写项。
4. 仓内 extension 只能通过正式 profile、有效自动发现 profile 或仓内合同 service 的显式 `--config-path` 接入，不得扩大宿主机暴露面，也不得绕过 Gateway 或内部网络边界。
5. 主仓库默认不内置任何调度作业；平台健康以运行时治理面、registry 与运行服务状态为准。

## Ingress 来源边界

private HTTPS ingress 的来源限制由三层共同闭合：

- Nginx 本地 allowlist：`nginx.gateway.conf` 必须按 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 渲染 `allow` 列表并保持 `deny all`。
- 基础设施边界：`host_firewall` 模式必须由 `apply_ingress_boundary_rules.sh` 物化 DOCKER-USER 规则；`external_acl` 模式必须提供结构化 ACL 证据。
- Gateway 认证：Gateway UI 请求通过来源限制后，必须经过官方 Gateway token 与 pairing 流程。
- 只读控制面 API：`/v1/control-plane/*` 与 `/v1/config/summary` 只经 private ingress 暴露；Nginx 先校验 `OPENCLAW_GATEWAY_TOKEN`，通过后再注入 `OPENCLAW_INTERNAL_API_TOKEN`，生成的 `nginx.gateway.conf` 按密钥文件处理。

配置生成后优先执行 `sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env`，写出与当前 env 对齐的 root 侧基础 evidence。部署阶段渲染 `nginx.gateway.conf` 后，部署用户会复用该基础 evidence 并对当前 Nginx allowlist 做本地校验；只有基础 evidence 缺失、env 漂移或本地 Nginx 校验失败时，才需要 root 侧补跑 `sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy`。该检查同时验证 Nginx allowlist、compose/runtime 端口暴露事实与基础设施边界证据，并把 root 侧 evidence 定向回收给部署用户读取。

公网访问不改变本机 ingress 合同。公网客户端必须先经过上游 ACL、NAT、VPN、堡垒机或反向代理；`OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 只填写目标机实际看到的私网或 loopback 来源 CIDR。公网客户 IP 白名单由上游策略和 `external_acl` evidence 证明，不写入 OpenClaw 本机 allowlist。

## Ingress 权限与 capability 边界

- `openclaw-private-ingress` 保持 `cap_drop: ALL`，仅保留运行所需的最小 capability；不要通过增加 `CAP_DAC_OVERRIDE` 绕过宿主机文件权限。
- Nginx 配置、证书与日志目录的可读写边界由 ACL 合同承担；`fix_permissions.sh`、`bootstrap.sh` 与 `gen_cert.sh` 会恢复这些 ACL。
- 若 ingress 日志出现 `nginx.gateway.conf`、证书或 `/var/log/nginx/error.log` 权限错误，先以固定部署用户执行 `bash ./scripts/setup/fix_permissions.sh`，再重启 ingress；不要递归放宽整个 `state/openclaw`。
- root 侧 ingress evidence 只允许调整 evidence 输出目录和文件 owner，不能顺带重置 gateway state root 的 ACL mask。

## 固定运行路径

- host state root：`state/openclaw`
- gateway host state dir：`state/openclaw/gateway`
- gateway container state root：`/home/node/.openclaw`

这些路径用于运行态证据、渲染产物与只读挂载边界说明；业务扩展如需补充状态目录，按各自 extension 合同接入。

## 配置边界

- kernel / base：`config/control_plane/service.json`
- 正式默认运行 profile：`config/control_plane/profiles/agent_platform.service.json`
- 受控组合 profile 白名单：`config/control_plane/repo_combination_profiles.json`
- 受控组合 profile service：`config/control_plane/profiles/<combination-profile-id>.service.json`
- 主仓库正式 extension：`config/control_plane/extensions.d/agent_platform.json`
- 仓内 extension：通过正式 profile、有效自动发现 profile 或仓内合同 service 的显式 `--config-path` 接入

## 运行边界

- dispatch target registry、provider registry、runtime adapters、runtime paths 与 object families 属于平台正式资产。
- diagnostics、dispatch、recovery 与 router surface 统一以 `agent_platform` runtime 为准。
- 平台可以导出通用 dispatch 治理与审计状态，但默认不提供内置 agent / module / job 治理链路。
