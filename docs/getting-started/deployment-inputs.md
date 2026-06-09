# 部署输入说明

本文用于补齐 `deploy/site.env`、启用扩展时的扩展内部 `agent/extensions/<extension-id>/deploy/extension.env`，并说明 `deploy/.env` 中自动生成字段的来源。默认路径是 `self_signed + host_firewall`；切换 `provided_files` 或 `external_acl` 时，仅填写对应条件字段。

正式默认运行配置不要求额外模型/API provider 密钥。

## 最小填写片段

以下片段适用于 **self_signed + host_firewall** 的首轮引导：

```text
OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=<目标机实际看到的访问端来源 CIDR>,<目标机本机 full test 来源 CIDR>
OPENCLAW_INGRESS_LISTEN_IP=<目标机 private ingress 绑定私网或 loopback IP>
OPENCLAW_TLS_CN=<访问端真实使用的唯一主机名>
OPENCLAW_TLS_MODE=self_signed
OPENCLAW_INGRESS_BOUNDARY_MODE=host_firewall
```

若切到 **provided_files + external_acl**，再补充：

```text
OPENCLAW_TLS_MODE=provided_files
OPENCLAW_TLS_CERT_SOURCE_PATH=<目标机可读取的证书路径>
OPENCLAW_TLS_KEY_SOURCE_PATH=<目标机可读取的私钥路径>
OPENCLAW_INGRESS_BOUNDARY_MODE=external_acl
OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH=<external_acl 结构化 JSON 证据路径>
```

## 推荐填写顺序

1. 先初始化 private ingress；需要指定访问端平台、目标机地址或主机名时使用第二条命令。

```bash
bash ./scripts/setup/init_private_ingress.sh
bash ./scripts/setup/init_private_ingress.sh --platform windows -- 192.168.50.10 openclaw.internal.example
```

2. 打开 `deploy/site.env`，按“第 2 步最小闭环”和下方字段说明补齐平台输入；启用扩展时，扩展字段只写入对应扩展内部 `agent/extensions/<extension-id>/deploy/extension.env`。

```bash
vim deploy/site.env
```

## 第 2 步最小闭环

1. `init_private_ingress.sh` 通常已先回填 `OPENCLAW_INGRESS_LISTEN_IP` / `OPENCLAW_TLS_CN`；第 2 步默认先复核，再决定是否覆盖。
2. 必填人工输入固定先补 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS`；它是 private ingress 来源限制的固定输入项。
3. 切到 `OPENCLAW_TLS_MODE=provided_files` 时，再补证书 / 私钥路径；切到 `OPENCLAW_INGRESS_BOUNDARY_MODE=external_acl` 时，再补结构化 ACL 证据文件路径。
4. `one_click_config.sh` 后先执行 setup env validate；host_firewall 模式要先用 `sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env` 物化来源限制，再用 root 侧 ingress boundary doctor 写出基础 evidence；不要等到 full test 才暴露来源限制语义错误。

## 跨 OS / 跨网络实例访问场景

- 适用条件：访问端与目标机分处不同 OS 实例、不同网络实例、VPN、NAT 或上游代理链路。
- `OPENCLAW_INGRESS_LISTEN_IP` 固定填写目标机对访问端可达的私网 IP；不要填写访问端地址、上游网关地址，也不要误写成 loopback。
- `OPENCLAW_TLS_CN` 在访问端 DNS / hosts 中必须解析到目标机 ingress 地址；不要解析到访问端地址。
- `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 优先收窄为目标机实际看到的访问端源 IP `/32`；若网络层把访问端流量翻译成网关地址，则填写该翻译后地址 `/32`；只有链路以网段呈现时，才填写对应链路网段；同时加入目标机本机 full test 来源，即 `OPENCLAW_INGRESS_LISTEN_IP/32` 或 IPv6 `/128`。
- 常见误填：访问端网卡地址或网段、本地 Wi-Fi 网段、Docker bridge 网段，以及遗漏目标机本机 full test 来源。

```text
OPENCLAW_INGRESS_LISTEN_IP=<目标机对访问端可达的私网 IP>
OPENCLAW_TLS_CN=<访问端用于访问目标机的唯一主机名>
OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=<目标机实际看到的访问端源 IP>/32,<OPENCLAW_INGRESS_LISTEN_IP>/32
```

## 公网来源经上游 ACL 接入

- OpenClaw 本机 ingress 只接受私网或 loopback 来源 CIDR，不把公网客户端 CIDR 写入 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS`。
- 公网访问必须先经过上游 ACL、NAT、VPN、堡垒机或反向代理；OpenClaw 只配置目标机实际看到的私网来源。
- 公网客户 IP 白名单写入上游 ACL 或安全组，并在 evidence JSON 中体现该上游策略；OpenClaw allowlist 只记录目标机可见的私网来源。
- 访问端验收时，先确认目标机日志或上游转发记录中的实际来源地址，再执行 `check_client_access_acceptance.sh`。

```text
OPENCLAW_INGRESS_BOUNDARY_MODE=external_acl
OPENCLAW_INGRESS_LISTEN_IP=<目标机私网IP>
OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=<目标机自检IP/32>,<上游设备私网IP/32或私网段>
OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH=<目标机可读取的external_acl证据JSON>
```

## 需要人工填写

### private ingress TLS 与访问主机名

- `OPENCLAW_INGRESS_LISTEN_IP`：private ingress 在目标机绑定的唯一私网或 loopback IP（填写位置：`deploy/site.env`）
- `OPENCLAW_TLS_CN`：唯一访问主机名、证书主机名与 Gateway Control UI origin（填写位置：`deploy/site.env`）
- `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS`：允许访问 private ingress 80/443 的来源网段（逗号分隔，仅接受私网或 loopback IPv4/IPv6 CIDR）（填写位置：`deploy/site.env`）

## 条件必填

### private ingress TLS 与访问主机名

- `OPENCLAW_TLS_CERT_SOURCE_PATH`：provided_files 模式的外部证书文件路径（`OPENCLAW_TLS_MODE=provided_files` 时必填）（填写位置：`deploy/site.env`）
- `OPENCLAW_TLS_KEY_SOURCE_PATH`：provided_files 模式的外部私钥文件路径（`OPENCLAW_TLS_MODE=provided_files` 时必填）（填写位置：`deploy/site.env`）
- `OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH`：external_acl 模式的结构化 ingress 边界证据文件路径（`OPENCLAW_INGRESS_BOUNDARY_MODE=external_acl` 时必填）（填写位置：`deploy/site.env`）

## 自动生成或自动推导

### Gateway 与内部服务访问令牌

- `OPENCLAW_GATEWAY_TOKEN`：Gateway token
- `OPENCLAW_INTERNAL_API_TOKEN`：Internal API token

### private ingress TLS 与访问主机名

- `CONTAINER_TZ`：Container timezone
- `OPENCLAW_INGRESS_BOUNDARY_MODE`：ingress 来源限制证据模式（`host_firewall` 或 `external_acl`）
- `OPENCLAW_INGRESS_HSTS_MAX_AGE`：Ingress HSTS max-age
- `OPENCLAW_TLS_MODE`：Ingress TLS mode（`self_signed` 或 `provided_files`）

### 运行镜像与基础运行时

- `HOST_STATE_ROOT`：Host state root (repo-relative or absolute)
- `NGINX_IMAGE`：Nginx image
- `OPENCLAW_OFFICIAL_GATEWAY_IMAGE`：Official OpenClaw gateway image
- `OPENCLAW_RUNTIME_GID`：Runtime bind group GID
- `OPENCLAW_RUNTIME_PYTHON_IMAGE`：Runtime Python image
- `OPENCLAW_RUNTIME_UID`：Runtime bind user UID

### 可选项

- `OPENCLAW_CONTROL_PLANE_PROFILE`：Control-plane service profile id（由 registry 映射为运行态配置路径）
- `OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH`：Container control-plane service/profile path（由 OPENCLAW_CONTROL_PLANE_PROFILE 生成）

## 关键字段说明

### `OPENCLAW_INGRESS_LISTEN_IP`

- 填写时机：第 1 步通常自动回填；第 2 步负责复核，必要时填写真实部署网卡地址。只有浏览器与目标服务位于同一操作系统实例时，才允许填写 loopback。
- 含义：这是目标机 private ingress 对宿主机 80/443 的唯一绑定地址；可以是 IPv4 或 IPv6；只有浏览器与目标服务位于同一操作系统实例时，才允许使用 loopback。
- 填写：
  - 优先执行 `bash ./scripts/setup/init_private_ingress.sh` 自动回填。
  - 若默认探测到的不是实际部署网卡，填写目标机真正承接浏览器访问的私网 IP。
  - 只有浏览器与目标服务位于同一操作系统实例时，才允许手工填写 `127.0.0.1`；跨机器、跨 OS / 跨网络实例、VPN 或上游代理场景都不属于该例外。
  - 跨 OS / 跨网络实例访问时，填写目标机对访问端可达的私网 IP；不要沿用访问端地址、上游网关地址，也不要误写成 loopback。
- 约束：只接受 RFC1918/loopback IPv4 或 ULA/loopback IPv6 字面量；不接受 hostname、`0.0.0.0`、`::` 或公网地址。
- 避免：
  - 把访问端 IP、域名、Docker bridge 地址或公网地址填进该字段。
  - 把访问端地址或上游网关地址当成目标机 ingress 绑定地址。
  - 把 `127.0.0.1` 用在跨机器、跨 OS / 跨网络实例或任何需要跨网络访问的场景。
- 验证：
  - 执行 `bash ./scripts/setup/init_private_ingress.sh` 或人工改写后，重新执行 `bash ./scripts/setup/one_click_config.sh`。
  - 再执行 `bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate --env-file deploy/.env`，并确认访问端解析结果落到该地址。
  - 若填写 loopback，则 `curl --resolve`、浏览器访问与名称解析核对都必须在同一操作系统实例内完成；跨 OS / 跨网络实例访问时必须解析到目标机私网地址。
- 命令示例：

```text
hostname -I
hostname -I | tr ' ' '\n' | grep -E '^(10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|192\.168\.)' | head -n 1
```

### `OPENCLAW_TLS_CN`

- 填写时机：第 1 步通常自动回填；第 2 步负责复核，必要时填写正式访问主机名。
- 含义：该字段同时用于浏览器访问名称、证书主机名与 Gateway Control UI origin。
- 填写：
  - 按内网 DNS、hosts 策略或正式证书计划确定唯一主机名，并保持与访问端解析口径一致。
  - 跨 OS / 跨网络实例访问时，在访问端 DNS/hosts 中把该名称固定解析到目标机 ingress 地址。
  - Windows 访问端若没有可用内网 DNS 且依赖 hosts 覆盖，不要只看 `Resolve-DnsName`；必须同时核对 hosts 文件内容，并用 `ping <OPENCLAW_TLS_CN>` 或浏览器首连确认实际解析结果。
- 约束：必须是 ASCII DNS 主机名；只允许字母、数字、点与短横线，不允许 IP、IPv4 dotted-quad 形态、通配符、下划线、空白、尾随点、空 label、超长 label 或非 DNS label 字符。
- 避免：
  - 把目标机 IP 当成主机名填写，或让该名称与证书、访问端解析结果不一致。
  - 把该名称解析到访问端地址或上游网关地址，导致浏览器没有进入目标机 ingress。
- 验证：
  - Linux 访问端执行 `getent hosts <OPENCLAW_TLS_CN>`；macOS 执行 `dscacheutil -q host -a name <OPENCLAW_TLS_CN>`。
  - Windows 有内网 DNS 时执行 `Resolve-DnsName -Name <OPENCLAW_TLS_CN>`；若依赖 hosts 覆盖，则同时查看 hosts 文件并执行 `ping <OPENCLAW_TLS_CN>`，不要只把 `Resolve-DnsName` 当成最终验证。
  - 以上结果都必须落到 `OPENCLAW_INGRESS_LISTEN_IP`。
  - provided_files 模式下，外部证书必须包含精确 `dNSName:<OPENCLAW_TLS_CN>` SAN。
  - 跨 OS / 跨网络实例访问时，访问端 DNS/hosts 核对结果必须直接落到目标机 ingress 地址。

### `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS`

- 填写时机：第 2 步必须人工确认；这是 private ingress 来源限制的固定输入项。
- 含义：该字段定义“谁可以访问 ingress”，描述的是流量来源面，而不是目标机绑定地址。
- 填写：
  - 直连办公网或堡垒机访问时，未确认目标机实际看到的来源地址就填写访问端所在网段。
  - 通过 VPN 访问时，填写 VPN 地址池或 VPN 出口 NAT 网段；不要机械填写用户电脑本地 Wi-Fi 网段。
  - 通过上游 ACL、安全组、负载均衡或代理访问时，填写目标机最终看到、且被上游放行到目标机的源地址网段。
  - 同时加入目标机本机执行 full test 时的来源地址：通常就是 `OPENCLAW_INGRESS_LISTEN_IP/32`（IPv4）或 `/128`（IPv6）。
  - 始终按最小必要来源面填写；没有业务必要时，不要把整个 10.0.0.0/8、192.168.0.0/16 或泛化虚拟交换机大网段直接写入。
  - 跨 OS / 跨网络实例访问时，优先收窄为目标机实际看到的访问端源 IP `/32`；若网络层只暴露链路网段或网关翻译地址，再填写该链路网段或翻译后 `/32`。
- 约束：只允许逗号分隔的私网或 loopback IPv4/IPv6 CIDR；系统会归一化网络前缀、拒绝重复项，并要求输入值按最小必要范围填写。
- 避免：
  - 遗漏目标机本机验收来源，导致 one_click_deploy 的 `/healthz` / `/readyz` 检查在目标机本机返回 403。
  - 把 Docker bridge 网段、容器网段或任意公网 CIDR 写进该字段。
  - VPN / NAT 场景只按终端本机网段填写，忽略目标机实际看到的源地址面。
  - 为了图省事直接放开整个办公私网、虚拟交换机大网段或超出当前业务所需的泛化私网段。
  - 字段值与 Nginx 渲染 allowlist、host_firewall 或 external_acl 证据中的 `source_cidrs` / `ip_families` 不一致。
  - 把访问端本地网卡整段、目标机所在大网段、本地 Wi-Fi 网段或未实际命中的 NAT 前地址写进该字段，而不是目标机实际看到的来源地址面。
- 验证：
  - 执行 `bash ./scripts/setup/one_click_config.sh` 后，先执行 `bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate --env-file deploy/.env`。
  - 配置校验会确认 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 包含目标机本机验收来源；缺失时不要继续到 full test。
  - 再立即执行 `sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env` 与 `sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env`，不要等到 full test 才发现来源限制口径错误。
  - 部署渲染 `nginx.gateway.conf` 后，部署用户会复用 root 侧基础 evidence，对当前 Nginx allowlist 做本地校验，并把 `nginx_policy` 合并回 evidence；只有基础 evidence 缺失、env 漂移或本地 Nginx 校验失败时，才补跑 `sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy`。
  - external_acl 模式下，证据文件中的 `source_cidrs` 必须与该字段归一化后集合一致。
  - 跨 OS / 跨网络实例访问时，先确认浏览器访问目标机时目标机实际看到的来源地址，再让 host_firewall 或 external_acl 证据与该地址面精确一致。
- 命令示例：

```text
ip route get <OPENCLAW_INGRESS_LISTEN_IP>
ip -o -4 addr show
Get-NetIPAddress -AddressFamily IPv4 | Format-Table InterfaceAlias,IPAddress,PrefixLength
ip route get <OPENCLAW_INGRESS_LISTEN_IP>  # 在 Linux 宿主机读取 src，优先把该 src 收窄成 /32
```

### `OPENCLAW_TLS_CERT_SOURCE_PATH`

- 填写时机：仅在 `OPENCLAW_TLS_MODE=provided_files` 时人工填写。
- 含义：切到 provided_files 模式后，ingress 会从该路径复制外部 PEM 证书或 fullchain。
- 填写：填写目标机可读取的证书绝对路径。
- 约束：必须是目标机可读取的 PEM X.509 证书或 fullchain 文件路径；文件不得位于或指向 `deploy/nginx/certs/` 输出目录。
- 避免：只切换 `OPENCLAW_TLS_MODE=provided_files`，却遗漏证书路径、提供 CN-only/wildcard-only 证书、过期证书，或提供未被目标机与访问端信任的签发链。
- 验证：执行 `bash ./scripts/setup/one_click_config.sh` 与 `bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate --env-file deploy/.env`；部署时 `gen_cert.sh` 还会继续校验证书与私钥匹配关系。

### `OPENCLAW_TLS_KEY_SOURCE_PATH`

- 填写时机：仅在 `OPENCLAW_TLS_MODE=provided_files` 时人工填写。
- 含义：切到 provided_files 模式后，ingress 会从该路径复制外部未加密 PEM 私钥。
- 填写：填写目标机可读取的私钥绝对路径。
- 约束：必须是目标机可读取的未加密 PEM 私钥文件路径；文件不得位于或指向 `deploy/nginx/certs/` 输出目录。
- 避免：只切换 `OPENCLAW_TLS_MODE=provided_files`，却遗漏私钥路径、提供加密私钥，或提供不匹配的私钥。
- 验证：执行 `bash ./scripts/setup/one_click_config.sh` 与 `bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate --env-file deploy/.env`；部署时 `gen_cert.sh` 还会继续校验证书与私钥匹配关系。

### `OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH`

- 填写时机：仅在 `OPENCLAW_INGRESS_BOUNDARY_MODE=external_acl` 时人工填写。
- 含义：该文件为 external_acl 模式提供结构化来源限制证据。
- 填写：填写目标机可读取的 JSON 证据文件路径；文件内容必须由网络侧真实 ACL 或安全组策略导出。
- 约束：必须是结构化 JSON 文件路径；文件内容至少包含 `schema_version`、`generated_at`、`source_cidrs`、`allowed_ports`、`default_deny`、`ip_families`、`enforcement_plane`，并且至少包含 `target_bind_ip` 或 `target_hostnames` 之一。
- 避免：
  - 只切换到 `external_acl` 模式，却未提供证据文件。
  - 证据文件缺失 `source_cidrs` / `allowed_ports` / `default_deny` / `ip_families` / `enforcement_plane` 等关键键。
  - 证据文件既没有 `target_bind_ip`，也没有 `target_hostnames`，导致无法证明当前 ACL 面向的就是当前部署目标。
- 验证：
  - 执行 `bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env`，确认 external_acl 证据与当前部署输入逐项一致；host_firewall 模式下改用 root 侧命令物化并核对宿主机规则。
  - 重点核对 `source_cidrs`、`allowed_ports`、`default_deny`、`ip_families`、`enforcement_plane`，以及 `target_bind_ip` / `target_hostnames` 中至少一项能够证明当前部署目标。

### `OPENCLAW_CONTROL_PLANE_PROFILE`

- 填写时机：默认使用 agent_platform；启用业务扩展或受控组合 profile 时填写对应 profile id。
- 含义：该字段选择 active control-plane service profile，并驱动扩展 deploy env schema、扩展内部 agent/extensions/<extension-id>/deploy/extension.env、dispatch target registry、模型输入与运行态路径合并。
- 填写：
  - 先执行 profile 列表命令，确认要启用的 profile id。
  - 默认保留 agent_platform。
  - 启用单个业务扩展时，填写 profile 列表中登记或有效发现的扩展 profile id。
  - 启用仓内受控组合 profile 时，填写 profile_registry.tsv 中登记的组合 profile id。
  - 组合 profile 中 OLLAMA_BASE_URL 与 OLLAMA_MODEL_REF 是共享模型输入，写入 deploy/site.env；扩展专属 provider、角色、通知目标与功能开关变量写入对应 agent/extensions/<extension-id>/deploy/extension.env。
  - 不要填写 /opt/openclaw-tools/.../*.service.json 路径。
- 约束：只允许已登记或有效发现的 profile id；例如 agent_platform、扩展 profile id 或组合 profile id。
- 避免：
  - 把 /opt/openclaw-tools/.../*.service.json 路径填到 deploy/site.env。
  - 把扩展自身变量写到 deploy/site.env，而不是写入对应扩展内部 agent/extensions/<extension-id>/deploy/extension.env。
  - profile id 与同时存在的 OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 指向不一致。
- 验证：
  - 执行 `bash ./scripts/setup/one_click_config.sh` 生成 `deploy/.env`；生成结果会同时写出 `OPENCLAW_CONTROL_PLANE_PROFILE` 与由 profile 推导的 `OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH`。
  - 执行 `bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate --env-file deploy/.env`，确认 profile id 可解析、运行态路径与 profile registry 映射一致。
  - 启用 dispatch registry 时，执行 `bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate-dispatch-registry --env-file deploy/.env`，确认 active profile 关联的 dispatch target registry 可加载。
- 命令示例：

```text
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane config profiles --format text
```

## 输入约束

1. `OPENCLAW_TLS_CN` 是唯一访问主机名、证书主机名与 Gateway Control UI origin。
2. `OPENCLAW_INGRESS_LISTEN_IP` 只用于私有 HTTPS ingress 绑定；Gateway 容器内 bind 固定遵循容器侧网络合同。
3. `OPENCLAW_INGRESS_LISTEN_IP` 仅接受 RFC1918/loopback IPv4 或 ULA/loopback IPv6 字面量；拒绝 hostname、0.0.0.0/:: 与公网地址。
4. 只有浏览器与目标服务位于同一操作系统实例时，`OPENCLAW_INGRESS_LISTEN_IP` 才允许使用 loopback；跨机器、跨 OS / 跨网络实例、VPN 或上游代理场景都不属于该例外。
5. `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 用于定义 ingress 允许来源段；host_firewall 模式下 root 侧基础 evidence 必须把它与宿主机防火墙规则逐项对齐，部署用户侧会把当前 Nginx allowlist 校验结果合并回 evidence；external_acl 模式必须把它与结构化证据逐项对齐，并按最小必要来源面填写。
6. `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 必须包含目标机本机 full test 来源；默认 one_click_deploy 会在目标机本机通过 ingress 验证 `/healthz` 与 `/readyz`。
7. 当前部署输入不接受未登记的 `OPENCLAW_*` 键。
8. 当前部署输入只接受单一官方 Gateway token auth 所需字段。
9. `OPENCLAW_RUNTIME_UID` / `OPENCLAW_RUNTIME_GID` 是 runtime 可写 bind mount 的用户配置；默认写为执行 `one_click_config.sh` 的当前部署用户；`one_click_config.sh`、basic gate 与部署主链都会拒绝 root 误入。
10. 需要把 runtime 绑定到其他 UID/GID 时，宿主机 bind mount owner/UID/GID 合同必须闭合；bootstrap/fix_permissions 以 root 执行时必须能解析 OPENCLAW_RUNTIME_UID/GID，解析失败会中止，不会自动 sudo 或留下 root-owned runtime state。
11. compose 运行镜像变量由 runtime source strategy 声明并固定由 image pins 提供，不在 `deploy/site.env` 手工填写；canonical / acceleration / selected runtime source 的分层统一看 `../operations/runtime-service-reference.md` 的“运行镜像来源与 source strategy”。
12. `OPENCLAW_TLS_MODE` 只允许 `self_signed` 或 `provided_files`；切换到 `provided_files` 时，必须同时提供 `OPENCLAW_TLS_CERT_SOURCE_PATH` / `OPENCLAW_TLS_KEY_SOURCE_PATH`。
13. `provided_files` 模式要求外部 PEM 证书包含精确 `dNSName:OPENCLAW_TLS_CN` SAN、未过期，并与未加密 PEM 私钥匹配；目标机与访问端必须已信任签发链。
14. `OPENCLAW_INGRESS_BOUNDARY_MODE` 只允许 `host_firewall` 或 `external_acl`；切换到 `external_acl` 时，必须同时提供 `OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH`。
15. `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 只允许逗号分隔的私网或 loopback IPv4/IPv6 CIDR，并作为 ingress 来源限制的固定输入项；没有业务必要时不得放开泛化私网大网段，公网客户端 CIDR 只能写在上游 ACL 或安全组策略中。
16. root 侧 `apply_ingress_boundary_rules.sh` 会写出与当前 env 对齐的基础 evidence；部署阶段渲染 Nginx 后，部署用户会本地校验 allowlist 并把 `nginx_policy` 合并到 `<current-host-state-root>/control_plane/setup/ingress_boundary_evidence.json`。只有基础 evidence 缺失、env 漂移或本地 Nginx 校验失败时，才补跑 root 侧 `check_ingress_boundary_evidence.sh --require-nginx-policy`。
17. `external_acl` 模式下，证据文件必须是结构化 JSON，并且其中 `source_cidrs`、`allowed_ports`、`default_deny`、`ip_families`、`enforcement_plane` 与目标信息必须能证明和当前部署输入一致。
18. 模型/API provider 输入只有在当前 active profile 的 deploy env schema 明确声明时才可填写；未声明时不要向 deploy/site.env 增加额外 provider 字段。
19. 当前部署输入不接受额外认证模式切换字段。

## 填写完成后的下一步

```bash
bash ./scripts/setup/one_click_config.sh
bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate --env-file deploy/.env
sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env
sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env
bash ./scripts/setup/one_click_test_basic.sh
```

## 正式认证路径

- 外部请求先进入私有 HTTPS ingress；
- `OPENCLAW_TLS_CN` 是唯一访问主机名；ingress 只做 TLS 终结与反向代理；
- 官方 Gateway 直接执行 token auth，令牌字段固定为 `OPENCLAW_GATEWAY_TOKEN`；
- Python 业务面只在内网接受来自运行链路的调用。
