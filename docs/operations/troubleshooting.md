# 排障总入口

## 本页解决什么问题

本页只负责先把故障路由到正确阶段，再给出对应阶段的首个定位命令与下一跳文档。

它覆盖宿主机准备、配置生成、部署主链、runtime / ingress、deployment acceptance、交付导出与 dispatch。setup first-hop、full test、dispatch 恢复与 control-plane 对象事实分别查看本页、`runtime-service-reference.md`、`dispatch-targets.md` 与 `agent/README.md`。

## 适用范围

- 正式实施路径：**official Gateway + private HTTPS ingress + internal-api + control-plane scheduler**。
- 需要部署主链时回到 `../getting-started/quickstart.md`。
- 需要运行态统一入口与 acceptance 口径时回到 `runtime-service-reference.md`。
- 需要 dispatch 晨检、恢复或 target 首次接入时回到 `dispatch-targets.md`。

## 先收集当前现场信息

先执行固定现场采集命令：

```bash
pwd
hostname
hostname -I
docker version
docker compose version
bash ./scripts/runtime/show_runtime_service_status.sh
```

如果已经完成 `one_click_config.sh`，再补当前关键输入：

```bash
source <(bash ./scripts/runtime/run_openclaw_python_tool.sh setup env query-env-batch OPENCLAW_TLS_MODE OPENCLAW_TLS_CN OPENCLAW_INGRESS_LISTEN_IP OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS OPENCLAW_RUNTIME_UID OPENCLAW_RUNTIME_GID --format shell)
printf 'TLS_MODE=%s\nTLS_CN=%s\nLISTEN_IP=%s\nCIDRS=%s\nRUNTIME_UID=%s\nRUNTIME_GID=%s\n' "$OPENCLAW_TLS_MODE" "$OPENCLAW_TLS_CN" "$OPENCLAW_INGRESS_LISTEN_IP" "$OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS" "$OPENCLAW_RUNTIME_UID" "$OPENCLAW_RUNTIME_GID"
```

## 先判断故障落在哪一段

| 现象                                                         | 所在阶段              | 首个定位命令                                                                                                           |
|------------------------------------------------------------|-------------------|------------------------------------------------------------------------------------------------------------------|
| `Cannot find a valid baseurl for repo: base/7/x86_64`      | 宿主机准备             | `sudo bash ./scripts/setup/prepare_docker_host.sh --repair-centos7-vault-repos`                                  |
| `CERT_NOT_YET_VALID`、证书时间错误、系统时间漂移或 `check_system_time` 失败 | 宿主机准备 / readiness | `sudo bash ./scripts/setup/update_system_time.sh`                                                                |
| `docker: command not found`、`docker info` 失败               | 宿主机准备             | `sudo bash ./scripts/setup/prepare_docker_host.sh --install-docker --install-compose`                            |
| Docker bridge / NAT 报 `INVALID_ZONE: docker`               | 宿主机准备 / compose   | `sudo bash ./scripts/setup/prepare_docker_host.sh --open-firewall`                                               |
| Docker Yum repo 或 GHCR 拉取在国内网络下超时 / 403 / 连接失败             | 宿主机准备 / 镜像准备      | `sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn`                                    |
| Gateway candidate 已拉取，但 compose up 仍引用 canonical 或 final image 本地不存在 | 镜像准备 / 部署主链        | `bash ./scripts/images/check_deployment_image_contract.sh --env-file deploy/.env --compose-file state/openclaw/control_plane/setup/docker-compose.effective.yml --require-local` |
| `deploy/.env` 里还有 `__REQUIRED__`                           | 配置生成              | `bash ./scripts/setup/one_click_config.sh`                                                                       |
| 扩展 env required/manual_required 字段缺失                         | 配置生成              | `bash ./scripts/setup/check_extension_env_values.sh --profile <profile_id>`                                      |
| 当前 pin 可用但上游 latest 更高导致发布检查提示                         | basic gate / release | `bash ./scripts/setup/one_click_test_basic.sh --strict-release-check`                                            |
| `one_click_test_basic.sh` 失败                               | basic gate        | `bash ./scripts/setup/one_click_test_basic.sh --help`                                                            |
| `one_click_deploy.sh` 失败                                   | 部署主链              | `bash ./scripts/setup/one_click_deploy.sh --explain`                                                             |
| `one_click_test_full.sh` 失败                                | full test / 验收    | `bash ./scripts/setup/one_click_test_full.sh --explain`                                                          |
| UI 打不开、`/healthz` 异常、service unhealthy                     | runtime / ingress | `bash ./scripts/runtime/show_runtime_service_status.sh --target gateway --target ingress`                        |
| evidence export / clean delivery 失败                        | 归档导出              | `bash ./scripts/runtime/check_runtime_evidence_prereqs.sh --scope evidence-export`                               |
| dispatch target 配置不生效或 dry-run 失败                          | dispatch          | `bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate-dispatch-registry --env-file deploy/.env` |

## 宿主机准备问题

### CentOS 7 仓库不可用

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --repair-centos7-vault-repos
sudo bash ./scripts/setup/prepare_docker_host.sh --repair-centos7-vault-repos --network-profile cn
sudo yum clean all
sudo yum makecache fast
```

国内网络优先使用 `--network-profile cn`；企业内网镜像源和 `--centos7-vault-source` 细节回 `../getting-started/environment-setup.md`。若还需要补基础工具或 Docker，继续执行：

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn
```

下一跳：`../getting-started/environment-setup.md`

### 系统时间未闭合

```bash
sudo bash ./scripts/setup/update_system_time.sh
bash ./scripts/doctor/check_system_time.sh
bash ./scripts/doctor/check_docker_host_readiness.sh
```

系统时间未闭合时，镜像拉取、TLS 下载、证书有效期校验与运行日志排序都会继续失败；离线机可追加 `--offline`，但本机时间仍必须位于可信窗口。

### Docker / Compose 未安装或 daemon 没起来

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --install-docker --install-compose --configure-daemon
docker info
docker compose version
```

国内网络和企业内网 Docker repo / registry mirror 细节回 `environment-setup.md`。若 compose up 或 Docker bridge 网络创建报 `INVALID_ZONE: docker`，先只修复宿主机 firewalld / Docker zone 合同：

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --open-firewall
```

Docker / firewalld 修复后重跑 readiness；已有 ingress 边界证据的环境再补跑 `sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env`。

GHCR 官方源拉取慢时，先用 `bash ./scripts/images/check_openclaw_release.sh --json` 确认当前 pin；镜像站只能作为 digest 等值的 candidate source，镜像准备链路按 `../getting-started/image-preparation.md`。

### 镜像来源或部署镜像就绪性失败

```bash
bash ./scripts/doctor/check_docker_host_readiness.sh
bash ./scripts/doctor/check_deployment_image_readiness.sh
```

中国国内网络先确认宿主机已经执行 `prepare_docker_host.sh --all --network-profile cn`。在线补齐执行 `pull_images.sh`；受限网络或离线目标机走 `export_deployment_images.sh`、`load_deployment_images.sh` 与 `prepare_control_plane_medium.sh --offline --image-archive <local-path>`。

若 `gateway_source_selection.json` 显示 `envRewritten=true`，但部署日志或 effective compose 仍引用 canonical GHCR，说明 `pull_images` 之后没有重新加载镜像 env 或重渲染 effective compose。默认 `one_click_deploy.sh` 会自动重新加载镜像 env、重渲染 effective compose 并刷新 proof；手工分步执行时执行：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh runtime mounts sync-compose --env-file deploy/.env --output state/openclaw/control_plane/setup/docker-compose.effective.yml
bash ./scripts/images/check_deployment_image_contract.sh --env-file deploy/.env --compose-file state/openclaw/control_plane/setup/docker-compose.effective.yml --require-local
```

`check_deployment_image_contract.sh` 会在 `docker_compose_up` 前区分 candidate 已拉取但 compose 仍指 canonical、selected ref 未拉取、digest 不一致和 registry 不可达；不要通过手改 compose 绕过 selected ref。

### 当前用户无权访问 `/var/run/docker.sock`

```bash
id
ls -l /var/run/docker.sock
docker info
```

当前部署用户未加入 `docker` 组时：

```bash
sudo bash ./scripts/setup/prepare_deploy_user.sh --user openclaw --repo-dir /opt/openclaw/clawctl
docker info
```

### 部署用户与 runtime UID/GID 不一致

```bash
id
bash ./scripts/runtime/run_openclaw_python_tool.sh setup env query-env-batch OPENCLAW_RUNTIME_UID OPENCLAW_RUNTIME_GID --env-file deploy/.env --format shell
stat -c '%u:%g %n' . deploy state state/openclaw 2>/dev/null || true
bash ./scripts/doctor/check_local_runtime_fs_contract.sh --env-file deploy/.env --require-current-runtime-user --reject-root-runtime-user
```

规则：`prepare_deploy_user.sh` 是 root 阶段到固定部署用户阶段的交接入口；它会写入 `.openclaw/deploy-user.marker`，记录该部署用户是否由 OpenClaw 创建，远程清理只把该 marker 中 `created_by_openclaw=1` 的用户视为可删除用户。`one_click_config.sh` 会把当前执行用户写入 runtime UID/GID，basic gate 与部署主链都会拒绝 root 误入。误用 root 后，切回固定部署用户，修正仓库与 host state root owner，再重新执行 `one_click_config.sh` 和 basic gate。

若 doctor 明确显示 `deploy/nginx/certs` 或 repo-local runtime bind 目录由 root 持有，使用 root 侧固定修复入口收口后再切回部署用户：

```bash
sudo bash ./scripts/setup/fix_permissions.sh
```

## 配置生成与输入问题

### `deploy/.env` 含 `__REQUIRED__`

```bash
grep -n "__REQUIRED__" deploy/site.env agent/extensions/*/deploy/extension.env deploy/targets.d/*.env deploy/.env || true
bash ./scripts/setup/one_click_config.sh
bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate --env-file deploy/.env
```

规则：`deploy/.env` 只读不手改；人工维护入口固定在 `deploy/site.env`、启用扩展内部 `agent/extensions/<extension-id>/deploy/extension.env` 与 `deploy/targets.d/<target_id>.env`。

启用业务扩展 profile 时，使用 schema 工具定位缺项并写入字段：

```bash
bash ./scripts/setup/check_extension_env_values.sh --profile <profile_id>
bash ./scripts/setup/apply_extension_env_values.sh --profile <profile_id> --init-from-example --set KEY=<value>
export SECRET_KEY=<secret>
bash ./scripts/setup/apply_extension_env_values.sh --profile <profile_id> --set-secret-from-env SECRET_KEY
```

`check_extension_env_values.sh` 的 text 输出会按 group 列出字段与修复命令；JSON 输出只记录字段是否存在，不输出 secret 明文。`one_click_config.sh` 在扩展缺项时会打印同一组修复命令。

下一跳：`../getting-started/deployment-inputs.md`、`troubleshooting.md`

### `OPENCLAW_INGRESS_LISTEN_IP` 或 `OPENCLAW_TLS_CN` 不对

```bash
bash ./scripts/setup/init_private_ingress.sh
bash ./scripts/setup/init_private_ingress.sh --platform windows -- 192.168.50.10 openclaw.internal.example
```

访问端解析、hosts 写入与平台差异统一按 quickstart 第 1 步执行。规则：`OPENCLAW_INGRESS_LISTEN_IP` 必须是目标机私网 IP；`OPENCLAW_TLS_CN` 必须解析到该地址；无内网 DNS 时先写 hosts。默认修复入口统一用 `init_private_ingress.sh`，它会同步更新 deploy/site.env；浏览器所在系统不是 Windows 时通过 `--platform` 显式切换。第 1 步只验证名称是否对齐到目标 IP：Linux / macOS 写入后分别用 `getent hosts` / `dscacheutil -q host -a name` 核对实际解析；Windows 写入后用 hosts 文件目标记录与 `ping` 核对。`nslookup`、`dig` 与 `Resolve-DnsName` 只验证 DNS，不验证 hosts 覆盖。`443/TCP` 连通性属于部署完成后的验证项，不应作为第 1 步门禁。

## 部署主链问题

### Release gate 默认 WARN 或 strict FAIL

```bash
bash ./scripts/images/check_openclaw_release.sh
bash ./scripts/setup/one_click_test_basic.sh
bash ./scripts/setup/one_click_test_basic.sh --strict-release-check
```

默认首装使用 `relaxed_install`：当前 pin tag@digest 可解析、digest 与声明 pin 一致且官方运行合同可验证时，上游 latest 更高只记 WARN，basic gate 返回 0。仍会 FAIL 的情况包括当前 pin digest 不可解析、selected/candidate digest 不一致、镜像合同不完整、release 元数据异常，或显式 `--strict-release-check`。发布门禁、升级验证和 candidate 提升验证必须使用 strict 模式；普通首装不要把 upstream latest 更新当作安装阻断。

### `one_click_test_basic.sh`、`one_click_deploy.sh` 或 `one_click_test_full.sh` 失败

```bash
bash ./scripts/setup/one_click_test_basic.sh --help
bash ./scripts/setup/one_click_deploy.sh --explain
bash ./scripts/setup/one_click_test_full.sh --json
```

先按失败入口分流：basic gate 失败先修配置、readiness、镜像或 compose 合同；deploy 失败看 latest 摘要并按 quickstart 阶段矩阵选择 `--resume-from`；full test 失败看 full latest 摘要或 `--json` 输出。

部署主链 latest JSON 固定路径：`<current-host-state-root>/control_plane/setup/one_click_deploy.latest.summary.json`；对象 entry 为 `one_click_deploy_latest_summary_json`。

`one_click_deploy.latest.summary.*` 中的 `failed_stage` 是第一判定点：脚本执行位回 `fix_permissions.sh`，basic proof 回 `one_click_test_basic.sh`，extension env 统一看 `extension-env ensure --enabled --offline --json` 输出并按其阻断项修复 lock、requirements 或离线 wheelhouse，ingress evidence 回 root 侧 `apply_ingress_boundary_rules.sh` 与 `check_ingress_boundary_evidence.sh --require-nginx-policy`，运行账本和 evidence 导出按摘要中的阻塞点补跑。

若 `failed_stage=docker_compose_up` 且日志包含 `INVALID_ZONE: docker`，先修复 firewalld / Docker zone、重放 ingress 边界证据，再执行 `bash ./scripts/setup/one_click_deploy.sh --resume-from docker_compose_up`。服务已启动但 post-deploy acceptance / runtime evidence 失败时，先看 full latest summary 的 run ledger：required jobs 缺失或失败时用 `--resume-from post_deploy_acceptance` 执行 required jobs、full test 与 runtime evidence；发送动作按当前 target 配置执行。required jobs 已 accepted 但 full test 或 runtime evidence 未闭合时用 `--resume-from post_deploy_full_acceptance` 执行 full test 与 runtime evidence，且跳过 run_all_once。

需要人工补跑阶段时，不要自行猜顺序；阶段矩阵回 `../getting-started/quickstart.md`，deployment acceptance 顺序回 `runtime-service-reference.md#deployment-acceptance-default-flow`。

## runtime 与 ingress 问题

### UI / `healthz` / `readyz` 不正常

```bash
bash ./scripts/runtime/show_runtime_service_status.sh --target gateway --target ingress
bash ./scripts/runtime/show_runtime_container_logs.sh --target gateway
bash ./scripts/doctor/check_internal_api_runtime.sh
sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy
```

下一跳：`runtime-service-reference.md#manual-post-deploy-checks`

### ingress 边界证据不通过

```bash
sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env
sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy
bash ./scripts/runtime/run_openclaw_python_tool.sh setup env query-env-batch OPENCLAW_INGRESS_BOUNDARY_MODE OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH --format shell
```

host_firewall 模式下，iptables / firewalld 语义应由 root 侧检查并写出基础 evidence；非 root 部署用户只负责读取已落盘且与当前 env 对齐的基础 evidence，并对当前渲染出的 Nginx allowlist 做本地校验，不应覆盖 root 侧有效 evidence。只有基础 evidence 缺失、env 漂移或 Nginx 本地校验失败时，才需要 root 侧补跑 `check_ingress_boundary_evidence.sh --require-nginx-policy`。若 root evidence 写出后非 root 不可读，先检查 evidence 文件 owner，不要递归重置整个 `state/openclaw`。

下一跳：`runtime-service-reference.md#manual-post-deploy-checks`、`security-boundary.md`

<a id="client-access-acceptance"></a>
### 外部访问端未闭合

```bash
bash ./scripts/setup/check_client_access_acceptance.sh --env-file deploy/.env --client-cidr '<目标机实际看到的访问端私网CIDR[,CIDR]>' --tls-cn <OPENCLAW_TLS_CN>
```

`deployment_acceptance` 是目标机本机验收，默认 full test 覆盖；`client_access_acceptance` 是访问端 DNS/hosts、证书信任、来源 CIDR、浏览器/HTTP 验证的独立闭环。若 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 只包含目标机本机 `/32`，目标机自验可以通过，但外部浏览器仍未放行。

脚本拒绝公网、非私网或过宽 client CIDR。遇到 VPN、NAT、堡垒机或安全组时，先确认目标机实际看到的访问来源，再选择外部 ACL 证据、VPN/NAT 后私网来源或更精确的 `/32`。不要把访问端本地 Wi-Fi 网段、公网地址或覆盖范围过大的私网段直接写入来源合同。

<a id="control-ui-origin-not-allowed"></a>
<a id="control-ui-gateway-token-missing"></a>
<a id="control-ui-first-pairing"></a>
<a id="control-ui-certificate-warning"></a>
<a id="control-ui-localhost-url"></a>
### Control UI 首连错误分流

`origin not allowed` 表示访问主机名不是 `OPENCLAW_TLS_CN`，按 quickstart 第 1 步修 DNS/hosts，不把 IP 加入 `allowedOrigins`。`gateway token missing` / `unauthorized` 表示未填真实 `OPENCLAW_GATEWAY_TOKEN`。`pairing required` 表示主机名和 token 已通过，是首次浏览器配对动作，用 official CLI `devices list` / `devices approve <requestId>` 处理。

浏览器显示“网站不是私密链接”时，先确认地址是 `https://<OPENCLAW_TLS_CN>/`，不是 `http://localhost:443/`、`https://localhost/`、目标机 IP 或其他别名。`OPENCLAW_TLS_MODE=self_signed` 时，访问端必须信任目标机生成的 `deploy/nginx/certs/openclaw.crt`；证书的 `subjectAltName` 必须包含当前 `OPENCLAW_TLS_CN`。Windows 访问端按 quickstart 第 6 步的“访问端 SSH 隧道”导入 `Cert:\CurrentUser\Root` 后，使用不带 `-k` 的 `curl.exe https://<OPENCLAW_TLS_CN>/healthz` 验证证书链。

访问端使用 SSH 隧道时，hosts 仍必须把 `OPENCLAW_TLS_CN` 指向 `127.0.0.1`，浏览器仍必须打开 `https://<OPENCLAW_TLS_CN>/`。隧道只负责把本机 `127.0.0.1:443` 转发到目标机 private ingress，不改变 TLS 主机名合同；`http://localhost:443/` 会按明文 HTTP 访问 TLS 端口，属于错误地址。

<a id="control-ui-token-recovery"></a>
### 浏览器凭据恢复或敏感项复核

`deploy/site.env` 是人工输入真源，`deploy/.env` 是 `one_click_config.sh` 渲染后的运行态 env；两者仍然有用，且都不提交到 git。`600` 权限是正常状态，不要为了排查改成 group/world 可读。

默认只检查配置闭合和 secret 存在性，不打印明文：

```bash
cd /opt/openclaw/clawctl
ls -l deploy/.env deploy/site.env
bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate --env-file deploy/.env
bash ./scripts/setup/check_extension_env_values.sh --profile <profile_id> --format json
bash ./scripts/runtime/show_runtime_compose_config.sh --env-file deploy/.env
```

浏览器本地凭据丢失或更换浏览器时，不重新部署、不重新生成 token；读取当前 `OPENCLAW_GATEWAY_TOKEN`，在浏览器填入后再批准新设备：

```bash
cd /opt/openclaw/clawctl
bash ./scripts/runtime/run_openclaw_python_tool.sh setup env query-env OPENCLAW_GATEWAY_TOKEN --env-file deploy/.env
bash ./scripts/runtime/run_openclaw_official_cli.sh --target gateway -- devices list
bash ./scripts/runtime/run_openclaw_official_cli.sh --target gateway -- devices approve <requestId>
```

确需查看 token、secret 或 webhook 明文时，只在目标机受控终端定向读取目标 key；查看后清空屏幕或终端滚动历史，不把输出写入仓库、日志、issue 或截图：

```bash
sudo -u <deploy-user> bash -lc 'cd /opt/openclaw/clawctl && grep -hE "^(OPENCLAW_GATEWAY_TOKEN|<SECRET_KEY>|<WEBHOOK_KEY>)=" deploy/.env agent/extensions/*/deploy/extension.env deploy/targets.d/*.env 2>/dev/null || true'
```

`<deploy-user>` 是执行 `one_click_config.sh` 与部署主链的固定部署用户；使用默认示例部署时通常是 `openclaw`。

## full test 与 deployment acceptance 问题

### full test 或默认 deploy 验收耗时过长

```bash
bash ./scripts/runtime/show_runtime_service_status.sh
sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy
```

排查部署根因时不要先跑整套 full test。先用 basic gate 与定向 runtime / ingress doctor 确认服务、边界和控制面事实；慢项看 latest summary 的 `slow_checks`。

### `deployment_acceptance.json` 未达到 accepted

```bash
cat <current-host-state-root>/control_plane/setup/one_click_test_full.latest.summary.json
bash ./scripts/runtime/run_openclaw_python_tool.sh runtime acceptance acceptance-summary
```

下一跳：`runtime-service-reference.md#deployment-acceptance-pass-criteria`、`agent/README.md`

### run ledger 因 source health 阻塞

```bash
bash ./agent/extensions/<extension-id>/agent/modules/<module-id>/bin/<module-bin> manual-verify-results --limit 10
bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance
```

`source_health_high`、`pending_high_manual_verify` 或 `manual_verify_blocking` 表示运行事实还未被 ledger 接受，不是 Docker / ingress 问题。先看 latest summary 中的 blocked source 与 manual verify 列表，再按对应扩展 runbook 补人工校验；不要直接改 acceptance JSON。

## 验收归档与交付导出问题

### `export_runtime_acceptance_evidence.sh` 失败

```bash
bash ./scripts/runtime/check_runtime_evidence_prereqs.sh --scope evidence-export
sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy
bash ./scripts/runtime/export_runtime_acceptance_evidence.sh
```

### clean delivery 导出失败或工作树不干净

```bash
bash ./scripts/setup/export_clean_delivery_bundle.sh --check-only --bundle runtime-core
bash ./scripts/setup/export_clean_delivery_bundle.sh --bundle runtime-core --clean
```

`--check-only` 只会因为默认可清理目标或派生物失败；`state/openclaw` 与 `state/image_artifacts` 本身不会阻断 clean delivery，但会被排除出 bundle。

## Windows 远程命令与换行符问题

仓库文本由 `.gitattributes` 约束为 LF；Linux 端出现 `--list\r`、`$'\r'` 或参数末尾带回车时，先用 `git ls-files --eol scripts deploy config` 与 `bash -n ./scripts/setup/one_click_deploy.sh` 区分仓库文件和远程输入流。必须从 PowerShell 管道传入临时多行脚本时，SSH 远端先显式去掉 CR：

```powershell
$script | ssh @SshArgs "tr -d '\r' | runuser -u openclaw -- bash -s"
```

PowerShell 中调用 OpenSSH 时使用显式可执行文件路径和参数数组；批量写 env 使用 `apply_site_env_values.sh` 或 `apply_target_env_values.sh`。

## dispatch 问题

### dispatch target 配置不生效、provider endpoint 不合法、dry-run 失败

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate-dispatch-registry --env-file deploy/.env
bash ./scripts/runtime/run_openclaw_python_tool.sh setup env query-dispatch-registry --env-file deploy/.env
```

下一跳：首次接入、变量填写、晨检与 provider 操作看 `dispatch-targets.md`；运行态字段与审计对象看 `agent/README.md`。

## 下一步
部署主链：`../getting-started/quickstart.md`；运行态统一入口：`runtime-service-reference.md`；dispatch 运维承接页：`dispatch-targets.md`。
