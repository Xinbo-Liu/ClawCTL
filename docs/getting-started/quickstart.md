# Quickstart

## 本页解决什么问题

本页提供 **正式部署路径** 的最简部署主线：从宿主机准备、private ingress 选择、`deploy/site.env` 与启用扩展内部 `agent/extensions/<extension-id>/deploy/extension.env` 的 schema 工具化填写、one-click 主链，到 deployment acceptance、client access acceptance 与最终交付包导出。

正文覆盖部署主链、阶段顺序与恢复执行语义；需要运行验收与排障时，再分别跳到 `../operations/runtime-service-reference.md` 与 `../operations/troubleshooting.md`。

## 适用范围

本文描述正式部署路径：**official Gateway token auth + private HTTPS ingress + Python 平台控制平面**。

CentOS 7 x86_64 是受支持的存量老系统部署路径，并配套完整脚本链与静态门禁。该路径不作为默认推荐安全基线；新部署优先使用维护期内的 Linux 宿主机。宿主机前置链、one_click 主链与支持 / 不支持范围统一查看 `../architecture/supported-deployment-boundary.md`。

- 正式支持边界与不支持范围：`../architecture/supported-deployment-boundary.md`
- 宿主机准备与 readiness：`environment-setup.md`
- 阶段顺序与 `--resume-from` 语义：本页下方的“部署阶段与 `--resume-from`”
- acceptance 产物与 required checks：`../operations/runtime-service-reference.md#deployment-acceptance-artifacts`

## 执行角色

- 目标机：执行宿主机准备、填写 `deploy/site.env` 与启用扩展内部 `agent/extensions/<extension-id>/deploy/extension.env`、生成 `deploy/.env`、basic gate、正式部署、full test、pairing 批准，以及所有 `scripts/setup/*` / `scripts/runtime/*` / `scripts/doctor/*` 默认命令。
- 访问端：执行内网 DNS / hosts 解析检查、`curl --resolve` 私有 HTTPS 验证，并在浏览器中打开 `https://<OPENCLAW_TLS_CN>/`。访问端应尽量与真实使用浏览器的机器一致。
- 交付工作机：只在需要准备离线镜像归档或整理最终干净交付包时使用；仓库同时保存在交付工作机与目标机时，固定在交付工作机执行导出命令；仓库仅保存在目标机时，在目标机执行。

## 部署用户与权限边界

固定部署用户示例：

```bash
sudo bash ./scripts/setup/prepare_deploy_user.sh --user openclaw --repo-dir /opt/openclaw/clawctl
sudo -iu openclaw
cd /opt/openclaw/clawctl
id
docker info
```

- `prepare_deploy_user.sh` 是 root 阶段到固定部署用户阶段的唯一交接入口；它负责创建用户、写入 `.openclaw/deploy-user.marker` 记录该用户是否由 OpenClaw 创建、加入 docker 组、校验仓库路径并把仓库目录交接给部署用户，但不会自动切换当前 shell。
- 脚本不会自动切换当前 shell；完成 `prepare_docker_host.sh` 与 `prepare_deploy_user.sh` 后，必须显式 `sudo -iu openclaw`、`runuser -u openclaw -- bash -lc 'cd /opt/openclaw/clawctl && <command>'` 或重新以固定部署用户登录，继续执行 `prepare_control_plane_medium.sh` 与 `one_click_config.sh`；`deploy/.env` 生成后，再用 root 执行 `apply_ingress_boundary_rules.sh` 与 `fix_permissions.sh`，随后切回固定部署用户执行 basic gate 与 `one_click_deploy.sh`。若当前 shell 是 root 且目标机没有 sudo，可改用 `su - openclaw`。
- `prepare_deploy_user.sh` 不会复制 root 或当前登录用户的 SSH 公钥；若需要直接 SSH 登录固定部署用户，必须按目标机安全策略显式配置该用户 `authorized_keys`，否则使用已有 sudo/root 会话内的 `sudo -iu openclaw`。
- `one_click_config.sh` 会把当前执行用户写入 `OPENCLAW_RUNTIME_UID` / `OPENCLAW_RUNTIME_GID`；`one_click_config.sh`、`one_click_test_basic.sh` 与 `one_click_deploy.sh` 都会在正式动作前拒绝 root 误入，避免 runtime bind user 变成 `0:0` 或留下 root-owned state。
- 仓库目录、`deploy/`、`state/openclaw` 与当前 host state root 应由固定部署用户可管理；root 侧 ingress evidence 只会对 evidence 输出目录和文件做定向 chown，不应通过递归 chown/chmod 放宽整个运行态目录。
- `fix_permissions.sh` 和 `bootstrap.sh` 以 root 执行时必须能从当前环境、`deploy/.env` 或正在运行的 OpenClaw runtime 容器解析 runtime UID/GID；解析失败会中止，避免生成 root-owned runtime state。
- private ingress 保持 `cap_drop: ALL` 与最小 capability；Nginx 配置、证书和日志写入依赖 `fix_permissions.sh`、`bootstrap.sh`、`gen_cert.sh` 中的 ACL 合同，不通过补 `CAP_DAC_OVERRIDE` 解决权限问题。

## 执行位置切换规则

- 先在目标机完成宿主机基础环境准备，并通过宿主机 readiness 准入；两项都完成后，才进入第 0 步。
- 先在目标机执行第 0.1 小节，算出 `OPENCLAW_INGRESS_LISTEN_IP` 与 `OPENCLAW_TLS_CN`；随后切换到访问端执行第 0.2 ~ 0.4 小节，确认 DNS 或 hosts 解析。
- 完成第 0 步后，切回目标机，连续执行第 1 步到第 5 步。除文档显式标注“访问端”外，其余部署主链命令都默认在目标机执行。
- 第 6 步先在目标机确认 runtime 服务健康，再切换到访问端执行 `curl --resolve` 与浏览器访问；若出现首次设备配对批准，再短暂切回目标机执行批准命令，随后返回访问端刷新页面。
- 第 7 步在当前保存交付仓库的机器执行；仓库同时保存在交付工作机与目标机时，固定在交付工作机执行；仓库仅位于目标机时，直接在目标机执行。

## 最短路径

### 远程首装向导（默认 dry-run）

`remote_first_install.sh` 用于把 SSH preflight、仓库/归档 staging、部署用户准备、基础 site.env 生成与 one-click 主链串成可恢复的远程首装流程；默认只做 dry-run，只有显式 `--apply` 才会写远端目录、传输 bundle 或启动部署。`--plan-json` 只输出阶段顺序、执行身份、输入输出和失败边界，不写本地 state 或远端。非默认 SSH 端口使用 `--ssh-port`，其他 SSH/scp 选项继续使用 `--ssh-option`。deploy 阶段固定在 basic gate 前执行 `fix_permissions.sh`，并允许 `--client-cidr` 使用逗号分隔多个来源段。preflight 会在无 sudo、Docker/Compose 缺失、80/443 端口占用、repo 已存在或已有 openclaw-* 容器时明确阻断。

```bash
bash ./scripts/setup/remote_first_install.sh --preflight --host <ssh-target>
bash ./scripts/setup/remote_first_install.sh --plan-json --host <ssh-target> --ssh-port <port> --deploy --client-cidr '<cidr[,cidr]>'
bash ./scripts/setup/remote_first_install.sh --apply --host <ssh-target> --repo-dir /opt/openclaw/clawctl --deploy-user openclaw --git-url <url> --prepare-repo --configure-base --deploy
```

- 向导不在命令行接收明文密码或 secret；推荐 SSH key/agent，secret 只通过远端环境变量或 owner-only 文件导入。
- stage-bundle 会排除 `state/`、`deploy/.env`、`deploy/site.env`、`agent/extensions/*/deploy/extension.env` 与常见 secret 文件。
- 每次运行都会输出固定 log、summary、status.env 与 resume 命令；遇到无 sudo、端口占用、既有 repo 冲突、既有 OpenClaw 容器或 Docker/Compose 缺失时在失败阶段明确阻断。

### 在线首轮部署（目标机）

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --all
sudo bash ./scripts/setup/prepare_deploy_user.sh --user openclaw --repo-dir /opt/openclaw/clawctl
sudo runuser -u openclaw -- bash -lc '
set -euo pipefail
cd /opt/openclaw/clawctl
bash ./scripts/doctor/check_docker_host_readiness.sh
bash ./scripts/setup/init_private_ingress.sh
vim deploy/site.env
# 启用扩展时，使用 check_extension_env_values.sh / apply_extension_env_values.sh 补齐 extension.env
bash ./scripts/setup/prepare_control_plane_medium.sh
'
sudo runuser -u openclaw -- bash -lc '
set -euo pipefail
cd /opt/openclaw/clawctl
bash ./scripts/setup/one_click_config.sh
'
cd /opt/openclaw/clawctl
sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env
sudo runuser -u openclaw -- bash -lc '
set -euo pipefail
cd /opt/openclaw/clawctl
bash ./scripts/setup/fix_permissions.sh
bash ./scripts/setup/one_click_test_basic.sh
bash ./scripts/setup/one_click_deploy.sh
'
```

- 中国国内网络首轮部署使用下面的 profile 命令替换在线路径第一行；国际网络出口继续使用默认 `--all`。

中国国内网络首轮部署时，固定使用：

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn
```

### 离线首轮部署（目标机，已安装 Docker / Compose 且已具备本地镜像归档）

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --configure-kernel --configure-daemon --open-firewall
sudo bash ./scripts/setup/prepare_deploy_user.sh --user openclaw --repo-dir /opt/openclaw/clawctl
sudo runuser -u openclaw -- bash -lc '
set -euo pipefail
cd /opt/openclaw/clawctl
bash ./scripts/doctor/check_docker_host_readiness.sh --offline
bash ./scripts/setup/init_private_ingress.sh
vim deploy/site.env
# 启用扩展时，使用 check_extension_env_values.sh / apply_extension_env_values.sh 补齐 extension.env
bash ./scripts/setup/prepare_control_plane_medium.sh --offline --image-archive <local-path>
'
sudo runuser -u openclaw -- bash -lc '
set -euo pipefail
cd /opt/openclaw/clawctl
bash ./scripts/setup/one_click_config.sh
'
cd /opt/openclaw/clawctl
sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env
sudo runuser -u openclaw -- bash -lc '
set -euo pipefail
cd /opt/openclaw/clawctl
bash ./scripts/setup/fix_permissions.sh
bash ./scripts/setup/one_click_test_basic.sh --offline --image-archive <local-path>
bash ./scripts/setup/one_click_deploy.sh --offline --image-archive <local-path>
'
```

- 离线新机尚未安装 Docker / Compose 时，先按 `environment-setup.md` 挂载本地 RPM / YUM 源并完成 Docker / Compose 安装；不要把 `--offline` 当作安装路径。
- 访问端动作只出现在第 1 步与第 6 步；其余默认命令都在目标机执行。

## 部署前检查清单

- 时间对齐：先运行 `bash ./scripts/doctor/check_system_time.sh` 或宿主机 readiness；服务器快照恢复、证书时间错误或 `CERT_NOT_YET_VALID` 时，固定用 `sudo bash ./scripts/setup/update_system_time.sh` 修复后再继续。
- 版本对齐：默认首装允许当前 pin tag@digest 合同可验证但 upstream latest 更高时记 WARN；发布门禁、升级验证或 candidate 提升验证必须显式使用 `--strict-release-check`。
- 用户对齐：先用 `prepare_deploy_user.sh` 完成 root 到固定部署用户的交接；`id` 与 `docker info` 必须在固定部署用户下成功；root 只执行宿主机准备、部署用户准备、host_firewall 规则物化和权限修复，不承担 one_click 主链；若误用 root 执行 basic gate 或部署主链，入口会直接失败，不会自动切换用户。
- 路径对齐：仓库固定放在目标机部署路径，例如 `/opt/openclaw/clawctl`；人工输入只改 `deploy/site.env`、启用扩展内部 `agent/extensions/<extension-id>/deploy/extension.env` 与 `deploy/targets.d/*.env`，不手改 `deploy/.env` 和生成出的 compose/runtime 文件。
- 权限对齐：basic gate 前先用 `fix_permissions.sh` 恢复 zip/scp/git archive 落地后的脚本执行位、runtime bind mount、ingress ACL 与 owner-only 边界；root 执行时必须能确定 runtime UID/GID，不允许静默留下 root-owned state；不要用递归 `chmod 777`、大范围 `chown root` 或给 ingress 增加绕过读权限的 capability。
- 边界证据对齐：`host_firewall` 模式先执行 `sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env` 物化 DOCKER-USER 规则并写出 root 侧基础证据；部署渲染 Nginx 后，部署用户会复用该基础证据并对当前 Nginx allowlist 做本地校验，只有基础证据缺失、env 漂移或本地 Nginx 校验失败时才需要 root 侧补跑 `check_ingress_boundary_evidence.sh --require-nginx-policy`。
- 扩展配置对齐：active profile 启用业务扩展时，先用 `check_extension_env_values.sh --profile <profile_id>` 查看 schema 缺项，再用 `apply_extension_env_values.sh --profile <profile_id>` 写入 `agent/extensions/<extension-id>/deploy/extension.env`；secret 只从环境变量导入，日志与 summary 只记录存在性。
- 扩展运行环境对齐：active profile 启用 managed extension 时，scheduler 视角必须先通过 managed extension lifecycle lock 校验；`one_click_deploy.sh` 与 `one_click_upgrade.sh` 会通过 `extension-env ensure` 自动同步离线 wheelhouse、准备扩展 venv 并 verify。ensure 是唯一扩展环境外部入口，失败时按其结构化输出修复 lock、requirements 或离线 wheelhouse 后再次执行同一命令闭合。
- 国内网络对齐：中国国内网络首轮部署固定使用 `sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn`，该 profile 同时收口 CentOS 7 vault repo、Docker Yum repo 与 daemon registry-mirrors；Gateway GHCR 拉取不通时 `pull_images.sh` 默认自动选择等值 candidate 并只改当前 `deploy/.env`，selected/candidate 都不可达时走离线归档，不反复硬拉。
- 换行符对齐：仓库文本由 `.gitattributes` 统一约束为 LF；从 Windows 驱动 Linux 远程命令时，避免把 PowerShell here-string 的 CRLF 直接喂给 bash/ssh，多行远程脚本使用 LF 文件、base64 包装或 `tr -d '\r' | runuser -u openclaw -- bash -s` 这类显式去 CR 管道。
- 测试策略对齐：full test 和 `one_click_deploy` 默认验收可能耗时较久；排查部署根因时先跑 basic gate 与定向 doctor，必要时用 `one_click_deploy.sh --skip-acceptance` 启动服务，但必须把 acceptance 未闭合作为显式状态记录。

## 正式步骤

### 第 0 步：完成宿主机准备与 readiness 准入

- 固定入口：`sudo bash ./scripts/setup/prepare_docker_host.sh --all`；中国国内网络首轮部署使用 `sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn`。
- 固定部署用户交接：`sudo bash ./scripts/setup/prepare_deploy_user.sh --user openclaw --repo-dir /opt/openclaw/clawctl`，随后用 `sudo -iu openclaw` 切换到该用户继续主链。
- 固定准入：`bash ./scripts/doctor/check_docker_host_readiness.sh`
- 详细命令、CentOS 7 仓库修复与离线分支统一查看 `environment-setup.md`。

### 第 1 步：确定 private ingress 地址与访问端解析

- 目标机默认把 `OPENCLAW_INGRESS_LISTEN_IP` 选为 `hostname -I` 中的目标私网 IPv4，例如 `<hostname -I 首个私网 IPv4>`；也可手工使用 ULA/loopback IPv6。只有浏览器与目标服务位于同一操作系统实例时，才允许手工使用 loopback。跨 OS / 跨网络实例访问不属于该例外。
- 访问主机名 `OPENCLAW_TLS_CN` 必须解析到该地址，例如 `openclaw.internal.example`。
- 先在目标机执行统一初始化命令，再在访问端判断是否已有可用 DNS；没有就写 hosts。

目标机固定初始化命令：

```bash
bash ./scripts/setup/init_private_ingress.sh
bash ./scripts/setup/init_private_ingress.sh --platform windows -- 192.168.50.10 openclaw.internal.example
```

- 默认命令会从 `hostname -I` 中选取首个 RFC1918 私网 IPv4，并把 `OPENCLAW_TLS_CN` 默认写成 `openclaw.internal.example`。
- 执行后会统一回填 `deploy/site.env` 中的 `OPENCLAW_INGRESS_LISTEN_IP` / `OPENCLAW_TLS_CN`，并在终端打印当前平台的访问端 DNS / hosts 命令；默认平台是 Windows，可用 --platform 切换。
- 若实际部署网卡不是首个私网 IPv4，必须改用 `-- <listen_ip> <tls_cn>` 显式覆盖。
- 只有浏览器与目标服务位于同一操作系统实例时，才允许把 `<listen_ip>` 填写为 `127.0.0.1`；跨 OS / 跨网络实例访问必须填写目标机私网地址。
- 访问端默认按 Windows PowerShell 输出；浏览器所在系统不是 Windows 时，必须通过 `--platform linux`、`--platform macos` 或 `--platform all` 显式切换。
- Linux、macOS、Windows 三个平台的 hosts 命令都使用“删除既有记录 + 重写目标映射 + 再核对”的稳定块，可整段复制粘贴。
- hosts 改写块会先备份原文件并在终端打印回滚命令；访问端核对失败时，先恢复备份，再重新确认 `OPENCLAW_TLS_CN` 与 `OPENCLAW_INGRESS_LISTEN_IP`。
- Windows hosts 重写必须在“以管理员身份运行”的 PowerShell 中执行；macOS 重写后立即执行 `dscacheutil -flushcache` 与 `killall -HUP mDNSResponder`。
- 第 1 步只验证 `OPENCLAW_TLS_CN` 到 `OPENCLAW_INGRESS_LISTEN_IP` 的名称对齐。Linux hosts 重写后，用 `getent hosts` 核对实际解析；macOS 用 `dscacheutil -q host -a name`；Windows 依赖 hosts 时用 hosts 文件目标记录与 `ping` 核对。`nslookup`、`dig` 与 `Resolve-DnsName` 只验证 DNS 记录，不验证 hosts 覆盖。
- `443/TCP` 连通性属于部署完成后的验证项；只有 private ingress 已启动、证书已落地且宿主机边界已放行后，才检查 `https://<OPENCLAW_TLS_CN>` 或 `Test-NetConnection -Port 443`。
- 跨 OS / 跨网络实例访问时，访问端 DNS/hosts 固定把 `OPENCLAW_TLS_CN` 指向目标机 ingress 地址；不要指向访问端地址或上游网关地址。第 2 步的 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 也必须按目标机实际看到的访问端源 IP、NAT 翻译后地址或链路网段填写。

访问端 DNS / hosts 处理固定使用第 1 步初始化命令打印的平台化指令；需要手工核对时使用当前平台的事实命令：

- Linux：`getent hosts "$OPENCLAW_TLS_CN"`，DNS 旁路检查可用 `nslookup` 或 `dig +short`。
- macOS：`dscacheutil -q host -a name "$OPENCLAW_TLS_CN"`，改写 hosts 后刷新 DNS 缓存。
- Windows PowerShell：管理员窗口写 hosts；核对 hosts 目标记录与 `ping $OpenClawTlsCn`，`Resolve-DnsName` 只验证 DNS。

若访问端没有内网 DNS，使用 `init_private_ingress.sh --platform <windows|linux|macos|all> -- <listen_ip> <tls_cn>` 打印的 hosts 改写块；该块会备份 hosts、删除已有映射、写入目标映射并打印回滚命令。

### 第 2 步：填写部署输入并生成 `deploy/.env`

```bash
vim deploy/site.env
# 启用扩展时，使用 check_extension_env_values.sh / apply_extension_env_values.sh 补齐 extension.env
bash ./scripts/setup/prepare_control_plane_medium.sh
bash ./scripts/setup/one_click_config.sh
```

- target 级变量维护入口固定为 `deploy/targets.d/<target_id>.env`；批量写入使用 `apply_target_env_values.sh --target <target_id>`，不要从 Windows PowerShell here-string 直接向远程 bash 写多行 env，也不要手改 `deploy/.env`。
- 启用业务扩展时，先执行 `bash ./scripts/setup/check_extension_env_values.sh --profile <profile_id>` 查看按 group 分组的 required / manual_required / secret 字段，再用 `apply_extension_env_values.sh --profile <profile_id> --init-from-example --set KEY=VALUE` 或 `--set-secret-from-env KEY` 写入扩展 env。
- 需要逐项解释输入字段时，转到 `deployment-inputs.md`。
- one_click 入口职责、默认主链、阶段顺序与 `--resume-from` 语义统一查看本页。
- 第 2 步只做三类动作：先补必填人工项，再复核第 1 步已回填的地址/主机名，最后只在切换模式时补条件字段。
- `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 是第 2 步最关键的人工确认项，它描述“哪些来源可以访问 ingress”，必须按目标机最终看到的源地址网段填写，而不是目标机绑定地址。
- 默认部署会在目标机本机执行 full test，因此来源 CIDR 必须同时包含访问端来源，以及 `OPENCLAW_INGRESS_LISTEN_IP` 对应的本机来源精确主机段。
- 第 1 步会写入 `OPENCLAW_INGRESS_LISTEN_IP` 与 `OPENCLAW_TLS_CN`；第 2 步默认只复核两者是否分别等于真实对外监听私网 IP 与访问端实际使用的唯一主机名。只有浏览器与目标服务位于同一操作系统实例时，才允许把监听地址填写为 loopback。跨 OS / 跨网络实例访问不属于该例外。
- 只有切到 `OPENCLAW_TLS_MODE=provided_files` 时，才补 `OPENCLAW_TLS_CERT_SOURCE_PATH` / `OPENCLAW_TLS_KEY_SOURCE_PATH`；只有切到 `OPENCLAW_INGRESS_BOUNDARY_MODE=external_acl` 时，才补 `OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH`。
- 跨 OS / 跨网络实例访问时，`OPENCLAW_TLS_CN` 必须解析到目标机 ingress 地址，`OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 必须填写目标机实际看到的访问端源 IP `/32`、NAT 翻译后地址 `/32`，或确有必要时填写对应链路网段；同时保留目标机本机 full test 来源。

### 第 3 步：确认配置生成结果可进入 basic gate

```bash
grep -n "__REQUIRED__" deploy/site.env agent/extensions/*/deploy/extension.env deploy/targets.d/*.env deploy/.env || true
bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate --env-file deploy/.env
source <(bash ./scripts/runtime/run_openclaw_python_tool.sh setup env query-env-batch OPENCLAW_TLS_MODE OPENCLAW_TLS_CN OPENCLAW_INGRESS_LISTEN_IP OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS --format shell)
printf "OPENCLAW_TLS_MODE=%s\nOPENCLAW_TLS_CN=%s\nOPENCLAW_INGRESS_LISTEN_IP=%s\nOPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS=%s\n" "$OPENCLAW_TLS_MODE" "$OPENCLAW_TLS_CN" "$OPENCLAW_INGRESS_LISTEN_IP" "$OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS"
sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env
sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env
```

- 出现 `__REQUIRED__`、TLS / ingress 参数不一致、setup env validate 失败，或 `check_ingress_boundary_evidence.sh` 失败时，先不要进入 basic gate。
- 来源限制语义错误应在此步骤直接暴露，作为进入 full test 前的前置校验。
- 配置生成与输入问题的失败分流统一查看 `../operations/troubleshooting.md#配置生成与输入问题`。

### 第 4 步：执行基础测试门禁

```bash
bash ./scripts/setup/one_click_test_basic.sh
```

- 离线首轮示例：`bash ./scripts/setup/one_click_test_basic.sh --offline --image-archive <local-path>`。
- `--image-archive` 只允许与 `--offline` 同用；在线模式传入离线归档会直接失败，basic gate proof 与镜像来源状态必须一致。
- basic gate 固定前置检查 ingress 边界证据与来源限制语义；失败时先修正 `deploy/site.env`、启用扩展内部 `agent/extensions/<extension-id>/deploy/extension.env`、宿主机防火墙或 external ACL 证据，再继续。
- release alignment 默认使用 `relaxed_install` 策略：当前 pin tag@digest 可解析且合同闭合时，上游 latest 更高只记 WARN；发布门禁、升级验证或 candidate 提升验证才使用 `--strict-release-check`。
- basic gate 会拒绝 `deploy/.env`、`deploy/site.env`、启用扩展内部 `agent/extensions/<extension-id>/deploy/extension.env`、`deploy/targets.d/*.env` 与仓库可执行脚本中的 CRLF / 回车字符；从 Windows 写入部署输入后先保持 LF，再进入正式部署。
- 详细边界、默认主链与 basic gate 语义统一查看本页。

### 第 5 步：执行正式部署与自动验收闭环

```bash
bash ./scripts/setup/one_click_deploy.sh
```

- `one_click_deploy.sh` 默认会校验或刷新 basic gate proof，启动 runtime 服务后自动执行 `one_click_test_full.sh` 并导出 runtime acceptance evidence。
- 若 `pull_images.sh` 在 `PULL_GATEWAY_CANDIDATE_MODE=auto-switch` 下改写了 `deploy/.env`，部署入口会重新加载镜像 env、重新渲染 effective compose、刷新 basic gate proof，并在 `docker_compose_up` 前用最终 compose image refs 做本地镜像合同检查。
- 若当前 profile / extension 声明 required run ledger jobs，`one_click_deploy.sh` 会先受控执行一次 `run_control_plane_run_all_once.sh` 生成真实 run ledger，再进入 full test；该入口只在 scheduler cycle lock busy 时有限重试，其他失败立即保留。发送动作按当前 target 配置执行；当前 target 配置不允许发送且 run ledger 缺失时，应改用 `--skip-acceptance` 仅启动服务，并把 deployment acceptance / runtime acceptance evidence 未闭合作为交接状态。run ledger execution 与 artifact evidence 必须同时闭合，执行失败、job 缺失、artifact root 缺失或声明输出没有可接受 evidence 都会阻断 full test / deployment acceptance。
- 需要结构化 full test 摘要时，执行 `bash ./scripts/setup/one_click_test_full.sh --json`。
- 需要快速恢复、验证服务启动或先定位部署根因时，可对部署入口追加 `--skip-acceptance`；该模式不会运行 full test，也不会把 deployment acceptance 置为通过。
- `--skip-acceptance` 后先执行 `show_runtime_service_status.sh`、`check_internal_api_runtime.sh`、`check_control_plane_runtime.sh` 与 root 侧 ingress 边界证据检查；当前 target 配置允许发送时，选择 `--resume-from post_deploy_acceptance` 闭合 required jobs、full test 与 runtime evidence；required run ledger jobs 已 accepted、仅 full test 或 runtime evidence 未闭合时，选择 `--resume-from post_deploy_full_acceptance`。
- `--skip-release-check`、`--skip-browser-verify`、`--require-basic-gate-proof` 与 `--skip-acceptance` 都属于恢复/排障开关；使用时部署日志会写出 WARN，不能把服务启动当作完整验收通过。
- 若服务阶段已经健康但 required run ledger jobs 缺失或失败，使用 `bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance` 执行 required jobs、full test 与 runtime evidence；发送动作按当前 target 配置执行。
- 若 required run ledger jobs 已 accepted，仅 full test 或 runtime evidence 导出未闭合，使用 `bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance` 执行 full test 与 runtime evidence，且跳过镜像拉取、compose 阶段与 run_all_once。
- 正式部署阶段会在启动服务前复核 `nginx.gateway.conf` 的来源 allowlist 与 ingress 边界证据；自动 full test 会再次执行同一闭环检查。
- 若部署中修复过 Docker / firewalld，必须重新执行 root 侧 `apply_ingress_boundary_rules.sh --env-file deploy/.env`，再 resume compose 或验收阶段。
- 失败后第一跳统一回 `../operations/troubleshooting.md`；阶段顺序与恢复执行语义统一回本页“部署阶段与 `--resume-from`”。

### 第 6 步：人工补充核对与浏览器首连

- 目标机先执行运行态人工补充核对：`../operations/runtime-service-reference.md#manual-post-deploy-checks`。
- 访问端再执行 `/healthz`、`/readyz` 与浏览器访问。
- 访问端闭环可先在目标机生成辅助命令：`bash ./scripts/setup/check_client_access_acceptance.sh --env-file deploy/.env --client-cidr '<目标机实际看到的访问端私网CIDR[,CIDR]>' --tls-cn <OPENCLAW_TLS_CN>`；该入口区分目标机本机 `deployment_acceptance` 与外部浏览器/DNS/hosts/证书信任的 `client_access_acceptance`。
- 浏览器必须打开 `https://<OPENCLAW_TLS_CN>/`，Control UI 的 WebSocket URL 使用 `wss://<OPENCLAW_TLS_CN>`；若改用 IP 或其他别名访问，Gateway 会因 Origin 不在 `allowedOrigins` 中拒绝连接。
- Control UI 连接时必须填入 `OPENCLAW_GATEWAY_TOKEN` 的真实值；目标机可用 `bash ./scripts/runtime/run_openclaw_python_tool.sh setup env query-env OPENCLAW_GATEWAY_TOKEN --env-file deploy/.env` 查询。
- 浏览器本地凭据丢失或更换浏览器时，不重新部署、不重新生成 token；重新读取当前 `OPENCLAW_GATEWAY_TOKEN` 并批准新设备配对即可恢复访问。
- `deploy/.env`、`deploy/site.env`、`deploy/targets.d/*.env` 与启用扩展内部 `agent/extensions/*/deploy/extension.env` 保持 owner-only `600`；日常检查用 validate、schema check 与脱敏 compose，确需查看 token / secret / webhook 明文时只在目标机受控终端定向读取目标 key。
- 第 5 步未使用 `--skip-acceptance` 时，deployment acceptance 与 runtime acceptance evidence 已由 `one_click_deploy.sh` 自动闭合；本步只做人工补充核对与首次浏览器配对。
- 若第 5 步使用 `--skip-acceptance`，或需要复核默认顺序、通过标准与证据产物，统一查看：

访问端 HTTPS 验证命令：

#### `self_signed`

```bash
export OPENCLAW_TLS_CN=openclaw.internal.example
export OPENCLAW_INGRESS_LISTEN_IP=<第 0 步选定的目标机私网 IP>
export OPENCLAW_CURL_RESOLVE_IP="${OPENCLAW_INGRESS_LISTEN_IP}"
case "$OPENCLAW_CURL_RESOLVE_IP" in *:*) OPENCLAW_CURL_RESOLVE_IP="[$OPENCLAW_CURL_RESOLVE_IP]" ;; esac
curl --cacert <openclaw-self-signed.crt> --resolve ${OPENCLAW_TLS_CN}:443:${OPENCLAW_CURL_RESOLVE_IP} https://${OPENCLAW_TLS_CN}/healthz
curl --cacert <openclaw-self-signed.crt> --resolve ${OPENCLAW_TLS_CN}:443:${OPENCLAW_CURL_RESOLVE_IP} https://${OPENCLAW_TLS_CN}/readyz
```

#### `provided_files`

```bash
export OPENCLAW_TLS_CN=openclaw.internal.example
export OPENCLAW_INGRESS_LISTEN_IP=<第 0 步选定的目标机私网 IP>
export OPENCLAW_CURL_RESOLVE_IP="${OPENCLAW_INGRESS_LISTEN_IP}"
case "$OPENCLAW_CURL_RESOLVE_IP" in *:*) OPENCLAW_CURL_RESOLVE_IP="[$OPENCLAW_CURL_RESOLVE_IP]" ;; esac
curl --resolve ${OPENCLAW_TLS_CN}:443:${OPENCLAW_CURL_RESOLVE_IP} https://${OPENCLAW_TLS_CN}/healthz
curl --resolve ${OPENCLAW_TLS_CN}:443:${OPENCLAW_CURL_RESOLVE_IP} https://${OPENCLAW_TLS_CN}/readyz
```

#### 访问端 SSH 隧道（Windows）

访问端不能直连目标机 private ingress 地址时，可通过 SSH 本地转发访问 Control UI。SSH 进程必须保持运行；该方式只转发本机 443 到目标机 ingress，不改变 TLS 主机名、证书或 Gateway Origin 合同。

访问端保持隧道进程运行：

```powershell
ssh -p <ssh-port> -L 127.0.0.1:443:<OPENCLAW_INGRESS_LISTEN_IP>:443 <ssh-user>@<ssh-host> -N
```

Windows 访问端以管理员 PowerShell 写入 hosts，流程为备份、过滤旧记录、写入目标映射并刷新解析缓存：

```powershell
$OpenClawTlsCn = "openclaw.internal.example"
$hosts = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$backup = "$hosts.openclaw-backup-$(Get-Date -Format yyyyMMddHHmmss)"
Copy-Item -LiteralPath $hosts -Destination $backup -Force
$lines = @(Get-Content -LiteralPath $hosts -ErrorAction Stop)
$escapedTlsCn = [regex]::Escape($OpenClawTlsCn)
$lines = $lines | Where-Object { $_ -notmatch "(^|\s)$escapedTlsCn(\s|$)" }
$lines += "127.0.0.1 $OpenClawTlsCn"
[System.IO.File]::WriteAllLines($hosts, $lines, [System.Text.Encoding]::ASCII)
ipconfig /flushdns
Select-String -LiteralPath $hosts -SimpleMatch $OpenClawTlsCn
```

`OPENCLAW_TLS_MODE=self_signed` 时，在 Windows 访问端信任目标机证书。只导入当前部署生成的 `OPENCLAW_TLS_CN` 公共证书，不导入私钥：

```powershell
$OpenClawTlsCn = "openclaw.internal.example"
$certPath = "$env:TEMP\$OpenClawTlsCn.crt"
ssh -p <ssh-port> <ssh-user>@<ssh-host> "cd /opt/openclaw/clawctl && (cat deploy/nginx/certs/openclaw.crt 2>/dev/null || sudo -n cat deploy/nginx/certs/openclaw.crt)" | Set-Content -LiteralPath $certPath -Encoding ASCII
Import-Certificate -FilePath $certPath -CertStoreLocation Cert:\CurrentUser\Root
```

证书信任后，访问端用系统 trust store 验证，不使用 `-k`：

```powershell
$OpenClawTlsCn = "openclaw.internal.example"
curl.exe "https://$OpenClawTlsCn/healthz"
curl.exe "https://$OpenClawTlsCn/readyz"
```

- 浏览器固定打开 `https://<OPENCLAW_TLS_CN>/`。不使用 `http://localhost:443/`、`https://localhost/`、目标机 IP 或其他别名；证书、Origin 与 WebSocket 主机名都以 `OPENCLAW_TLS_CN` 为准。
- 证书目录按运行态权限收口；SSH 用户是固定部署用户时可直接读取证书，否则需要具备非交互 sudo。只读取 `.crt` 公共证书，不读取 `.key` 私钥。

#### 浏览器凭据恢复

浏览器本地凭据丢失或更换浏览器后，不重新部署、不重新生成 token。访问端重新填入目标机当前 `deploy/.env` 中的 `OPENCLAW_GATEWAY_TOKEN`，随后按 `pairing required` 批准新设备。

目标机读取 Gateway token：

```bash
cd /opt/openclaw/clawctl
bash ./scripts/runtime/run_openclaw_python_tool.sh setup env query-env OPENCLAW_GATEWAY_TOKEN --env-file deploy/.env
```

Windows 访问端需要避免终端明文停留时，只把 token 放入剪贴板：

```powershell
$token = ssh -p <ssh-port> <ssh-user>@<ssh-host> "cd /opt/openclaw/clawctl && bash ./scripts/runtime/run_openclaw_python_tool.sh setup env query-env OPENCLAW_GATEWAY_TOKEN --env-file deploy/.env"
$token.Trim() | Set-Clipboard
```

- 不要把 token 粘贴到聊天、issue、日志或截图；只在当前访问端输入框或受控剪贴板中短暂使用。
- 新浏览器首次连通后仍会生成新的设备配对请求，需要在目标机执行 `devices list` / `devices approve <requestId>`。

#### 敏感项检查

`deploy/.env`、`deploy/site.env`、`deploy/targets.d/*.env` 与启用扩展内部 `agent/extensions/*/deploy/extension.env` 保持 owner-only `600`。配置检查默认看存在性、schema 和脱敏摘要；恢复访问或排查明确指向某个 key 时，才在目标机受控终端定向查看明文。

默认检查配置是否闭合，不打印 secret 明文：

```bash
cd /opt/openclaw/clawctl
ls -l deploy/.env deploy/site.env
bash ./scripts/runtime/run_openclaw_python_tool.sh setup env validate --env-file deploy/.env
bash ./scripts/setup/check_extension_env_values.sh --profile <profile_id> --format json
bash ./scripts/runtime/show_runtime_compose_config.sh --env-file deploy/.env
```

确需查看 token、secret 或 webhook 明文时，只查目标 key，并在查看后清空屏幕或终端滚动历史：

```bash
sudo -u <deploy-user> bash -lc 'cd /opt/openclaw/clawctl && grep -hE "^(OPENCLAW_GATEWAY_TOKEN|<SECRET_KEY>|<WEBHOOK_KEY>)=" deploy/.env agent/extensions/*/deploy/extension.env deploy/targets.d/*.env 2>/dev/null || true'
```

- `<deploy-user>` 是执行 `one_click_config.sh` 和部署主链的固定部署用户；使用默认示例部署时通常是 `openclaw`。
- 不要为排查把这些文件 chmod 成 group/world 可读；固定做法是切换到部署用户或通过 sudo 读取。
- 需要查看完整 compose 原始值时，只在本机受控终端追加 `--show-secrets`，不要把输出保存进仓库或交付包。

- 第 6 步分成两个执行位置：`show_runtime_service_status.sh`、control-plane / ingress / dispatch 的 doctor 检查在目标机执行；`curl --resolve` 与浏览器验证在访问端执行。
- 访问端命令不应依赖仓库内脚本；访问端只需要第 0 步和第 1 步已经确定的 `OPENCLAW_TLS_CN` 与 `OPENCLAW_INGRESS_LISTEN_IP`。
- 若目标机本身就是实际访问浏览器的机器，可不切换设备，但应按“先服务状态、后 curl / 浏览器”的顺序执行。
- `OPENCLAW_TLS_MODE=self_signed` 时，把目标机 `deploy/nginx/certs/openclaw.crt` 复制到访问端，再用 `curl --cacert <openclaw-self-signed.crt>` 校验。
- `OPENCLAW_TLS_MODE=provided_files` 时，目标机与访问端必须已信任签发链，访问端直接使用系统 trust store 执行不带 `-k` 的 `curl`。
- 目标机 full test 通过只代表 `deployment_acceptance` 闭合；若 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 仅包含目标机本机 `/32`，外部浏览器仍可能未放行，必须单独完成 `client_access_acceptance`。
- 公网、非私网或过宽 client CIDR 会被访问端闭环脚本拒绝；这类场景应先确认外部 ACL、安全组、VPN 或 NAT 后目标机实际看到的私网来源。

- `../operations/runtime-service-reference.md#deployment-acceptance-default-flow`
- `../operations/runtime-service-reference.md#deployment-acceptance-pass-criteria`
- `../operations/runtime-service-reference.md#deployment-acceptance-artifacts`

若首次访问出现 `disconnected (1008): pairing required`，按下面顺序在目标机批准一次设备：

```bash
bash ./scripts/runtime/run_openclaw_official_cli.sh --target gateway -- devices list
bash ./scripts/runtime/run_openclaw_official_cli.sh --target gateway -- devices approve <requestId>
```

批准完成后回到访问端刷新页面；这属于真实首连动作，不视为部署失败。

### 第 7 步：导出最终交付包

```bash
bash ./scripts/setup/export_clean_delivery_bundle.sh --bundle runtime-core --clean
```

- 第 7 步在当前保存交付仓库的机器执行；仓库同时保存在交付工作机与目标机时，固定在交付工作机执行；仓库仅位于目标机时，在目标机执行。
- 该命令只导出最终干净交付包。

## 部署阶段与 `--resume-from`

`one_click_deploy.sh --resume-from <stage>` 只接受下面这些 canonical stage id。

在线默认阶段：

- `fix_permissions`：fix_permissions.sh
- `bootstrap`：bootstrap.sh
- `gen_cert`：gen_cert.sh
- `render_gateway_ingress_conf`：run_openclaw_python_tool.sh setup ingress render-nginx
- `check_ingress_boundary_evidence`：check_ingress_boundary_evidence.sh（含 Nginx allowlist 渲染闭环）
- `check_openclaw_release`：check_openclaw_release.sh
- `pull_images`：pull_images.sh（拉取部署镜像合同角色）
- `check_deployment_image_contract`：check_deployment_image_contract.sh
- `check_runtime_compose_contract`：check_runtime_compose_contract.sh
- `verify_gateway_browser`：verify_gateway_browser.sh
- `docker_compose_config`：show_runtime_compose_config.sh（对应 one-click 内部的 docker_compose_config 阶段）
- `docker_compose_up`：启动 runtime 服务（内部执行 compose up -d，同时拉起官方 gateway、私有 HTTPS ingress、internal-api、control-plane scheduler（承载 delivery_adapter 执行能力）与 profile + extension 显式启用的附加 runtime 视角）
- `docker_compose_ps`：show_runtime_service_status.sh（对应 one-click 内部的 docker_compose_ps 阶段）

离线默认阶段：

- `fix_permissions`：fix_permissions.sh
- `bootstrap`：bootstrap.sh
- `gen_cert`：gen_cert.sh
- `render_gateway_ingress_conf`：run_openclaw_python_tool.sh setup ingress render-nginx
- `check_ingress_boundary_evidence`：check_ingress_boundary_evidence.sh（含 Nginx allowlist 渲染闭环）
- `load_deployment_images`：load_deployment_images.sh（导入部署镜像合同角色）
- `check_deployment_image_contract`：check_deployment_image_contract.sh
- `check_runtime_compose_contract`：check_runtime_compose_contract.sh
- `verify_gateway_browser`：verify_gateway_browser.sh
- `docker_compose_config`：show_runtime_compose_config.sh（对应 one-click 内部的 docker_compose_config 阶段）
- `docker_compose_up`：启动 runtime 服务（内部执行 compose up -d，同时拉起官方 gateway、私有 HTTPS ingress、internal-api、control-plane scheduler（承载 delivery_adapter 执行能力）与 profile + extension 显式启用的附加 runtime 视角）
- `docker_compose_ps`：show_runtime_service_status.sh（对应 one-click 内部的 docker_compose_ps 阶段）

后置 resume 阶段：

- `post_deploy_acceptance`：执行 required jobs、full test 与 runtime evidence 导出
- `post_deploy_full_acceptance`：执行 full test 与 runtime evidence 导出，跳过 run_all_once

- `--resume-from` 必须从真实失败阶段或更早阶段继续；如果失败发生在 `docker_compose_config` 或更早阶段，不能固定从 `docker_compose_up` 开始恢复执行。
- 若 `docker_compose_up` 因 `INVALID_ZONE: docker` 失败，先以 root 执行 `prepare_docker_host.sh --open-firewall` 与 `apply_ingress_boundary_rules.sh --env-file deploy/.env`，再从 `docker_compose_up` 继续。
- 若失败发生在服务启动后的 required run ledger jobs 阶段，使用 `--resume-from post_deploy_acceptance` 执行 required jobs、full test 与 runtime evidence；发送动作按当前 target 配置执行。
- 若 required run ledger jobs 已 accepted，仅 full test 或 runtime evidence 导出阶段失败，使用 `--resume-from post_deploy_full_acceptance` 执行 full test 与 runtime evidence，且跳过 run_all_once。
- 后置验收 resume 必须实际执行验收闭环，不能与 `--prepare-only` 或 `--skip-acceptance` 同用。
- 需要先确认入口与阶段说明时，用 `bash ./scripts/setup/one_click_deploy.sh --explain`。

### 关键阶段恢复执行命令

下列命令直接来自 `config/governance/flows/deploy_stage_flow.json` 的 `next_commands`，恢复执行或人工核对时以这些固定命令为准。

#### `fix_permissions`

```bash
bash ./scripts/setup/one_click_test_basic.sh${offlineFlag}
bash ./scripts/setup/fix_permissions.sh
bash ./scripts/doctor/doctor_paths.sh | sed -n '1,40p'
id
```

#### `bootstrap`

```bash
bash ./scripts/setup/bootstrap.sh
bash ./scripts/runtime/run_openclaw_python_tool.sh runtime paths check-generated
bash ./scripts/runtime/sync_workspace_templates.sh --check
```

#### `check_ingress_boundary_evidence`

```bash
sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env
sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy
bash ./scripts/runtime/run_openclaw_python_tool.sh setup ingress check-nginx --env-file deploy/.env
```

#### `check_runtime_compose_contract`

```bash
bash ./scripts/doctor/check_runtime_compose_contract.sh --env-file deploy/.env
bash ./scripts/runtime/show_runtime_compose_config.sh
```

#### `docker_compose_up`

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --open-firewall
sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env
bash ./scripts/runtime/run_runtime_service_action.sh up --all --force-recreate
bash ./scripts/setup/one_click_deploy.sh --resume-from docker_compose_up
bash ./scripts/runtime/show_runtime_service_status.sh
bash ./scripts/runtime/show_runtime_container_logs.sh --target gateway
```

#### `docker_compose_ps`

```bash
bash ./scripts/runtime/show_runtime_service_status.sh
bash ./scripts/setup/one_click_deploy.sh --resume-from docker_compose_ps
bash ./scripts/runtime/show_runtime_container_logs.sh --target gateway
bash ./scripts/runtime/show_runtime_container_logs.sh --target scheduler
bash ./scripts/doctor/check_control_plane_runtime.sh
```

#### `control_plane_run_all_once`

```bash
bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance
bash ./scripts/control_plane/run_control_plane_run_all_once.sh
```

#### `post_deploy_acceptance`

```bash
bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance
```

#### `post_deploy_full_acceptance`

```bash
bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance
```

#### `export_runtime_acceptance_evidence`

```bash
bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance
bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance
```

## 通过判据

1. [目标机] `gateway / ingress / internal-api / scheduler` 全部健康；
2. [目标机] `bash ./scripts/doctor/check_control_plane_runtime.sh` 通过；
3. [目标机] `sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy` 通过；
4. [目标机] `bash ./scripts/doctor/check_dispatch_runtime.sh` 通过；
5. [访问端] `bash ./scripts/setup/check_client_access_acceptance.sh --env-file deploy/.env --client-cidr '<目标机实际看到的访问端私网CIDR[,CIDR]>' --tls-cn <OPENCLAW_TLS_CN>` 已标记 client access acceptance 为 ready；
6. [访问端] `/healthz` 与 `/readyz` 返回 `200`；
7. [访问端] 浏览器使用 `https://<OPENCLAW_TLS_CN>/` 打开 Control UI，WebSocket URL 使用 `wss://<OPENCLAW_TLS_CN>`，不使用目标机 IP 或其他别名；
8. [访问端] Control UI 已填入 `OPENCLAW_GATEWAY_TOKEN` 的真实值并通过 token auth；
9. [目标机 + 访问端] 若新浏览器首次进入 Control UI，已完成一次设备配对批准并重新打开页面。

## 失败分流

| 当前现象                                                             | 先跳哪里                                                                                                   |
|------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| 宿主机仓库、Docker、Compose、firewall 或 firewalld docker zone 未准备完成      | `environment-setup.md`                                                                                 |
| `deploy/.env` 里还有 `__REQUIRED__`，或 setup env validate 失败         | `../operations/troubleshooting.md#配置生成与输入问题`                                                           |
| one_click_test_basic / one_click_deploy / one_click_test_full 失败 | `../operations/troubleshooting.md`                                                                     |
| 服务起来了但 UI / healthz / readyz 不正常                                 | `../operations/runtime-service-reference.md` 与 `../operations/troubleshooting.md#runtime-与-ingress-问题` |
| 目标机本机验收通过，但外部浏览器或访问端 curl 未闭合                                    | `../operations/troubleshooting.md#client-access-acceptance`                                            |
| Control UI 提示 origin not allowed                                 | `../operations/troubleshooting.md#control-ui-origin-not-allowed`                                       |
| Control UI 提示 gateway token missing 或 unauthorized               | `../operations/troubleshooting.md#control-ui-gateway-token-missing`                                    |
| 浏览器提示网站不是私密链接或证书不受信任                                             | `../operations/troubleshooting.md#control-ui-certificate-warning`                                      |
| 访问端打开了 localhost、localhost:443、目标机 IP 或其他别名                      | `../operations/troubleshooting.md#control-ui-localhost-url`                                            |
| 浏览器本地凭据丢失或更换浏览器后需要重新取 Gateway token                              | `../operations/troubleshooting.md#control-ui-token-recovery`                                           |
| 首次浏览器访问提示 pairing required                                       | `../operations/troubleshooting.md#control-ui-first-pairing`                                            |
| 导出 evidence 或 clean delivery 失败                                  | `../operations/troubleshooting.md#验收归档与交付导出问题`                                                         |

## 下一步

- 进入日常值守、服务状态、日志与证据归档：`../operations/runtime-service-reference.md`
- 进入统一排障：`../operations/troubleshooting.md`
- 进入 dispatch target 首次接入：`../operations/dispatch-targets.md`
