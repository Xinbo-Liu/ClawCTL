# 基础环境准备

本页只负责把目标机准备到“可以开始部署 private ingress + official gateway”的状态。CentOS 7 x86_64 是受支持的存量老系统部署路径，并配套完整脚本链与静态门禁。该路径不作为默认推荐安全基线；新部署优先使用维护期内的 Linux 宿主机。

## 完成标准

完成本页后，应同时满足：

1. 系统时间已通过 check_system_time.sh / check_docker_host_readiness.sh 校验，Docker Engine 与 Docker Compose 已安装并可执行；
2. 宿主机已准备好把 80/443 交给 private ingress；
3. 已确定 `OPENCLAW_INGRESS_LISTEN_IP` 与 `OPENCLAW_TLS_CN`；
4. 访问端可以把 `OPENCLAW_TLS_CN` 解析到 `OPENCLAW_INGRESS_LISTEN_IP`；
5. 已确定来源限制证据模式与允许来源段，并写清 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS`；
6. 已理解 VPN、堡垒机、外部 DNS 与上游安全组策略不在本仓库直接代管；

## 运行面与网络边界

- base 默认 target 为：`gateway / ingress / internal-api / scheduler`；启用 profile + extension 后，才会额外出现扩展贡献的 runtime target。
- 官方 Gateway 不直接暴露宿主机端口；
- private ingress 绑定宿主机 80/443；
- `OPENCLAW_TLS_CN` 是唯一访问主机名；
- ingress 负责 TLS、HSTS、来源 allowlist 与反向代理；`/` 进入 official Gateway，`/v1/control-plane/*` 与 `/v1/config/summary` 作为只读控制面 API 代理到 internal-api；来源限制还必须在宿主机防火墙、基础设施 ACL 或 DOCKER-USER 中显式落地，并与 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 保持一致；同时必须能证明默认拒绝。
- Gateway UI 与只读控制面 API 都必须先通过 `OPENCLAW_GATEWAY_TOKEN` 校验；private ingress 仅在 token 通过后注入 internal-api token 访问内部服务；
- `OPENCLAW_INGRESS_BOUNDARY_MODE=external_acl` 时，必须保留结构化 JSON 外部 ACL 证据文件，并显式给出 `source_cidrs` / `allowed_ports` / `default_deny` / `ip_families` / 目标信息。
- internal-api / scheduler 只位于 internal bridge，不对宿主机发布端口；internal-api 的外部可见面仅限 private ingress 完成 Gateway token 校验并注入内部 token 后代理的只读控制面 API；扩展贡献的内部 runtime target 也必须遵守同一规则。

- `OPENCLAW_INGRESS_LISTEN_IP` 仅接受 RFC1918/loopback IPv4 或 ULA/loopback IPv6 字面量；拒绝 hostname、0.0.0.0/:: 与公网地址。

## 宿主机至少应具备的工具

```bash
command -v bash curl wget openssl jq git tar unzip zip vim less which nslookup dig ss ip lsof docker
docker compose version
```

## 固定宿主机准备入口

先用仓库内脚本完成 CentOS 7 存量老系统路径的宿主机准备：repo 修复、基础工具、系统时间校验 / 更新、Docker、Compose 与 daemon 基线；脚本不会自动 sudo，执行时必须显式使用 root 或 sudo。

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --all
sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn
sudo bash ./scripts/setup/prepare_docker_host.sh --update-system-time
sudo bash ./scripts/setup/prepare_docker_host.sh --configure-kernel --configure-daemon --open-firewall
sudo bash ./scripts/setup/prepare_docker_host.sh --install-base-tools --configure-kernel
sudo bash ./scripts/setup/prepare_docker_host.sh --install-docker --install-compose --configure-daemon --open-firewall
sudo bash ./scripts/setup/prepare_docker_host.sh --install-docker --install-compose --network-profile cn --configure-daemon --open-firewall
```

- 在线机器优先执行完整宿主机准备命令：`sudo bash ./scripts/setup/prepare_docker_host.sh --all`。
- 中国国内网络首轮部署固定执行 `sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn`；该 profile 会把 CentOS 7 vault repo 与 Docker Yum repo 同时固定到 aliyun_cn，并继续写入 docker_host 真源中的 registry-mirrors。
- 若只修复服务器快照恢复后的时钟漂移或证书时间错误，执行 `sudo bash ./scripts/setup/prepare_docker_host.sh --update-system-time` 或 `sudo bash ./scripts/setup/update_system_time.sh`，再回到 readiness。
- 宿主机准备完成后执行 `sudo bash ./scripts/setup/prepare_deploy_user.sh --user openclaw --repo-dir <repo>` 完成用户交接并写入部署用户证据 marker，再用 `sudo -iu openclaw`、`runuser -u openclaw -- bash -lc 'cd <repo> && <command>'` 或固定部署用户 SSH 登录继续执行 `prepare_control_plane_medium.sh`、`one_click_config.sh`、basic gate 与部署主链；不要沿用 root 生成 runtime UID/GID。
- Docker Engine Yum repo 默认按 official、aliyun_cn、tuna_cn 顺序尝试；中国国内网络下优先使用 `--network-profile cn`，单独修复 Docker repo 时也可显式传入 `--docker-repo-source aliyun_cn`，内网制品源可设置 `OPENCLAW_DOCKER_CENTOS_REPO_URL`。
- 离线机器若已经具备 Docker / Compose，可执行 `--configure-kernel --configure-daemon --open-firewall`，执行范围限定为本地配置类步骤。
- 离线新机若尚未装好 Docker / Compose，先挂载本地 RPM / YUM 源，不要传 `--offline`；随后按 `--install-base-tools --configure-kernel` 与 `--install-docker --install-compose --configure-daemon --open-firewall` 分步安装和配置。
- 若只想修复 CentOS 7 repo 失效，再执行：`sudo bash ./scripts/setup/prepare_docker_host.sh --repair-centos7-vault-repos`。
- 若进入 Compose / jq 二进制回退，脚本会按 docker_host.json 的已登记 SHA256 做 fail-closed 校验。

## 固定宿主机 readiness 准入入口

宿主机准备完成后，必须通过唯一只读准入入口 check_docker_host_readiness.sh；该入口会先复核系统时间，通过前不得进入 one_click_config.sh。

```bash
bash ./scripts/doctor/check_docker_host_readiness.sh
bash ./scripts/doctor/check_docker_host_readiness.sh --offline
```

- `check_docker_host_readiness.sh` 只做只读预检；真正修复系统时间或其他宿主机前提统一通过 `prepare_docker_host.sh`。
- JSON 真源解析固定走仓库脚本内 jq / curl 路径；readiness 不执行 host 控制面命令，也不替代 prepare_control_plane_medium.sh。
- readiness 通过后，进入 host 控制面命令前必须显式执行 `prepare_control_plane_medium.sh`。

## host 控制面执行介质前置步骤

完成宿主机基础环境后，在进入 `one_click_config.sh` 与任意 host 控制面 Python 工具子命令前，必须先显式准备 host 控制面执行介质。


### 在线目标机

```bash
bash ./scripts/setup/prepare_control_plane_medium.sh
```

- 优先复用本地已存在的 OPENCLAW_CONTROL_PLANE_IMAGE。
- 本地缺失时，先尝试导入显式指定或自动发现的 deployment_images_*.tar。
- 归档未命中时，允许执行 docker pull OPENCLAW_CONTROL_PLANE_IMAGE。

### 离线目标机

```bash
bash ./scripts/setup/prepare_control_plane_medium.sh --offline --image-archive <local-path>
```

- 优先复用本地已存在的 OPENCLAW_CONTROL_PLANE_IMAGE。
- 本地缺失时，只允许导入显式指定或自动发现的 deployment_images_*.tar。
- 离线模式下禁止网络拉取；部署镜像归档必须来自当前 release 并与当前 pin 一致。

## 帮助面与执行面边界

- --help / --explain / 未知参数 必须优先输出可阅读帮助，不得因为 Docker、Docker daemon 或控制面镜像未就绪而阻塞帮助面。
- 动态帮助面可用时，只允许展示由控制面真源派生的阶段映射、固定路径与默认入口；动态帮助面不可用时，必须使用静态说明。
- 帮助面只用于确认职责边界、阶段映射、常用变体与固定参考页；默认顺序以治理真源和正式文档为准。

固定帮助入口：

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --help
bash ./scripts/doctor/check_docker_host_readiness.sh --help
bash ./scripts/setup/prepare_control_plane_medium.sh --help
bash ./scripts/setup/init_private_ingress.sh --help
bash ./scripts/setup/one_click_config.sh --explain
sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --help
bash ./scripts/setup/one_click_test_basic.sh --help
bash ./scripts/setup/one_click_deploy.sh --explain
bash ./scripts/setup/one_click_test_full.sh --explain
```

## TLS 证书模式

### `self_signed`

默认引导模式；由 `gen_cert.sh` 直接生成 `deploy/nginx/certs/openclaw.crt` / `openclaw.key`。

- 该模式用于首次部署与封闭测试；
- 浏览器或 curl 默认不会信任该证书，访问端必须显式使用目标机生成的自签证书作为 trust anchor。

### `provided_files`

正式证书模式；由 `gen_cert.sh` 严格校验未过期 PEM 证书、精确 dNSName SAN、未加密 PEM 私钥与非输出目录源路径后复制到 `deploy/nginx/certs/`。

- 必须在 `deploy/site.env` 中填写 `OPENCLAW_TLS_CERT_SOURCE_PATH` / `OPENCLAW_TLS_KEY_SOURCE_PATH`；
- 提供的证书必须包含精确 `dNSName:OPENCLAW_TLS_CN` SAN、未过期，且与未加密 PEM 私钥匹配；
- 目标机与访问端必须已信任签发链。

## 第 1 步：确认系统版本、架构与绑定 IP

```bash
cat /etc/os-release
uname -m
hostname -I
hostname -I | tr ' ' '\n' | grep -E '^(10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|192\.168\.)' | head -n 1
```

- `OPENCLAW_INGRESS_LISTEN_IP` 固定先取 `hostname -I` 输出中的首个私网 IPv4；只有部署网卡不是该地址时，才填写目标网卡 IP。
- 文档中的 IP 示例只作占位；实际填写值以目标机探测结果为准。

## 第 2 步：修复 CentOS 7 初始机 YUM 仓库（如遇 baseurl 错误）

```bash
sudo yum makecache fast
sudo bash ./scripts/setup/prepare_docker_host.sh --repair-centos7-vault-repos
sudo bash ./scripts/setup/prepare_docker_host.sh --repair-centos7-vault-repos --network-profile cn
sudo bash ./scripts/setup/prepare_docker_host.sh --repair-centos7-vault-repos --centos7-vault-source aliyun_cn
sudo env OPENCLAW_CENTOS7_VAULT_BASE_URL='https://<internal-mirror>/centos-vault/7.9.2009' bash ./scripts/setup/prepare_docker_host.sh --repair-centos7-vault-repos
sudo yum clean all
sudo yum makecache fast
```

- 若报错 `Cannot find a valid baseurl for repo: base/7/x86_64`，说明默认 mirrorlist 已不可用；必须切到 CentOS 7 vault repo 再继续安装。
- 修复脚本默认按 official、aliyun_cn、tuna_cn 顺序尝试；中国国内网络优先使用 `--network-profile cn`，单独修复 repo 时也可直接指定 `--centos7-vault-source aliyun_cn`，内网 vault 用 `OPENCLAW_CENTOS7_VAULT_BASE_URL` 覆盖。
- 修复脚本会先备份 `/etc/yum.repos.d/*.repo`，再重写 `CentOS-Base.repo`。

## 第 3 步：安装宿主机预工具

```bash
sudo yum install -y yum-utils device-mapper-persistent-data lvm2 curl wget git tar unzip zip vim-enhanced less which bind-utils net-tools lsof rsync ca-certificates chrony iptables iproute openssl
sudo yum install -y jq || true
sudo yum install -y epel-release jq || true
command -v vim less wget jq dig ss ip lsof
```

- `vim-enhanced`、`less`、`wget`、`bind-utils`、`net-tools`、`lsof` 属于现场排障高频工具，准备阶段需要显式安装。
- CentOS 7 默认 Base / Updates 仓库经常不直接提供 `jq`；当前统一以 `prepare_docker_host.sh --install-base-tools` 为准，由脚本自动尝试 `epel-release` 或 jq 官方静态二进制补装，并在结束前强制校验 `jq`、`dig`、`ss`、`ip` 等命令全部存在。
- jq 静态二进制回退必须命中已登记 SHA256；校验失败或缺少校验值时直接失败，不安装未校验下载物。

## 第 4 步：配置时区、系统时间、NTP、内核模块与 sysctl

```bash
sudo bash ./scripts/setup/update_system_time.sh
bash ./scripts/doctor/check_system_time.sh
timedatectl status
cat <<'EOF2' | sudo tee /etc/modules-load.d/br_netfilter.conf
br_netfilter
EOF2
sudo modprobe br_netfilter
cat <<'EOF2' | sudo tee /etc/sysctl.d/99-docker.conf
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
EOF2
sudo sysctl --system
```

- 时间同步是证书有效期、镜像拉取校验、调度 run ledger 与日志排序的共同前提；服务器快照恢复后先执行 update_system_time.sh，再用 check_system_time.sh 复核。
- 在线模式会用多源 HTTPS HTTP Date 基准与本机 UTC 时间比对；漂移超过默认 300 秒时，update_system_time.sh 会在启用 NTP / chronyd 后按可信基准直接校正系统时间，直接校正受可信时间窗口与最大跳变阈值约束。
- 这是 Docker bridge / private ingress / 宿主机防火墙协同的前提项，不能跳过。

## 第 5 步：安装 Docker Engine 26.1.4

```bash
sudo yum remove -y docker docker-client docker-client-latest docker-common docker-latest docker-latest-logrotate docker-logrotate docker-engine docker-ce docker-ce-cli || true
sudo bash ./scripts/setup/prepare_docker_host.sh --install-docker
sudo bash ./scripts/setup/prepare_docker_host.sh --install-docker --network-profile cn
sudo bash ./scripts/setup/prepare_docker_host.sh --install-docker --docker-repo-source aliyun_cn
docker version
docker info | sed -n "1,120p"
```

- 当前 CentOS 7 存量支持路径固定使用 `26.1.4` 组合。
- `prepare_docker_host.sh --install-docker` 会先用官方 Docker Yum repo，失败后自动尝试国内加速源；国内网络优先使用 `--network-profile cn`，单独修复 Docker repo 时也可指定 `--docker-repo-source aliyun_cn`，内网制品源用 `OPENCLAW_DOCKER_CENTOS_REPO_URL` 覆盖。

## 第 6 步：安装 Docker Compose V2 2.27.1 插件

```bash
rpm -q docker-compose-plugin >/dev/null 2>&1 || sudo yum install -y docker-compose-plugin-2.27.1-1.el7
test ! -f /usr/local/lib/docker/cli-plugins/docker-compose || sudo rm -f /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version
```

- 安装策略为：优先复用或安装 `docker-compose-plugin-2.27.1-1.el7`；只有 RPM 无法提供建议版本时，`prepare_docker_host.sh --install-compose` 才回退到二进制下载。
- 若目标机到 GitHub Releases 较慢，可在执行脚本前显式设置 `OPENCLAW_COMPOSE_DOWNLOAD_URL` 指向内网制品源或镜像下载地址；脚本会自动启用重试、断点续传与低速保护。
- 若存在 `/usr/local/lib/docker/cli-plugins/docker-compose` 覆盖件，必须删除该文件，确保 RPM 插件版本生效。
- Compose 二进制回退同样必须命中已登记 SHA256；校验失败或缺少校验值时直接失败，不允许继续安装未校验下载物。

## 第 7 步：配置 Docker daemon 与日志滚动

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --configure-daemon
sudo env OPENCLAW_DOCKER_REGISTRY_MIRRORS='https://<internal-mirror-1>,https://<internal-mirror-2>' bash ./scripts/setup/prepare_docker_host.sh --configure-daemon
docker info | grep -E "Storage Driver|Logging Driver"
```

- 若目标机已有 `/etc/docker/daemon.json`，先备份再覆盖；仓库脚本会自动留备份。
- registry-mirrors 默认来自 `config/governance/support/docker_host.json`；企业内网或特定地区可用 `OPENCLAW_DOCKER_REGISTRY_MIRRORS` 覆盖。
- 中国网络默认 profile 下，Python / Nginx 默认固定为 Daocloud tag@digest pin；Docker daemon registry-mirrors 作为补充拉取加速。

## 第 8 步：预开放宿主机 80/443

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --open-firewall
sudo firewall-cmd --list-services
sudo firewall-cmd --permanent --zone=docker --get-target
```

- `--open-firewall` 只预开放 80/443，并在 firewalld 运行时确保 permanent `docker` zone 存在且 target 为 `ACCEPT`；`18789` 与 `19001` 继续只绑定宿主机本地。
- 若该步骤修复了 Docker / firewalld zone，脚本会重启 Docker 并执行 Docker bridge 烟测；存在 ingress 边界证据的环境需要重新执行 `apply_ingress_boundary_rules.sh --env-file deploy/.env`。
- 若 `OPENCLAW_INGRESS_BOUNDARY_MODE=host_firewall`，完成 one_click_config 后必须执行 `apply_ingress_boundary_rules.sh --env-file deploy/.env`，并把 80/443 限制到 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS`。

## 第 9 步：执行仓库预检并进入 one_click 主路径

```bash
bash ./scripts/doctor/check_docker_host_readiness.sh
bash ./scripts/doctor/check_docker_host_readiness.sh --offline
bash ./scripts/setup/prepare_control_plane_medium.sh
bash ./scripts/setup/prepare_control_plane_medium.sh --offline --image-archive <local-path>
bash ./scripts/setup/one_click_config.sh
bash ./scripts/setup/one_click_test_basic.sh
```

- `check_docker_host_readiness.sh` 只做只读预检；真正修复宿主机前提统一通过 `prepare_docker_host.sh`。
- 在线与离线只选择对应模式的一组 readiness / control-plane medium 命令；不要在同一轮部署中混跑两种模式。
- `check_docker_host_readiness.sh` 会先调用 `check_system_time.sh`；系统时间未闭合时不得继续 Docker / Compose、镜像来源或 one_click 主链。
- JSON 真源解析固定走仓库脚本内 jq / curl 路径；readiness 不执行 host 控制面命令，也不替代 prepare_control_plane_medium.sh。
- 仓库级静态 Python 入口治理由仓库发布门禁覆盖；目标机 readiness 只负责宿主机、Docker、Compose、网络与镜像来源只读检查。
- 对带 canonical / acceleration / selected runtime source 分层的对象，预检会先探测当前 selected runtime source；若 selected source 不可达，再继续探测 `../operations/runtime-service-reference.md` 中声明的 acceleration candidates。
- Python / Nginx 默认固定为 Daocloud tag@digest pin；宿主机预检只验证本地缓存与当前 selected source 传输链路，不在此阶段复做精确 tag@digest artifact 判定。
- `check_deployment_image_readiness.sh`、`load_deployment_images.sh` 与 `ensure_control_plane_image.sh` 支持显式指定归档，未指定时自动尝试 `state/image_artifacts/` 下最新的 `deployment_images_*.tar`；归档按合同校验部署镜像角色，并在导入后写出包含 pin、managed role tag 与 image id 的 verified local refs。host 控制面进入前统一显式执行 `prepare_control_plane_medium.sh`，由该步骤完成 OPENCLAW_CONTROL_PLANE_IMAGE 准备。
- Ubuntu 22.04 推荐基线提供 readiness 检测与准备指引；Docker / Compose 缺失时按已安装环境修 PATH/daemon、未安装环境预先安装 Docker/Compose、离线环境准备离线安装包和镜像归档三路处理。
- 启用的 provider / API 入口预检只验证 DNS/TLS/HTTP reachability；只要能收到 HTTP 响应码，就判定网络连通，不把 base URL 当作业务健康检查接口。
- 若预检发现当前 selected source 不可达但 acceleration candidate 可用，脚本只给出只读诊断；后续 `pull_images.sh` 默认用 `PULL_GATEWAY_CANDIDATE_MODE=auto-switch` 只改当前 `deploy/.env`，或按显式 `fail-fast/off` 模式处理。selected / candidate 都不可达时使用离线镜像归档。
- `one_click_test_basic.sh` 通过后会写出 latest basic gate proof；`one_click_deploy.sh` 会校验同一 env/mode 的 proof，不得跳过 basic gate 直接部署。

## private ingress 人工输入口径

固定初始化命令：

```bash
bash ./scripts/setup/init_private_ingress.sh
bash ./scripts/setup/init_private_ingress.sh --platform windows -- 192.168.50.10 openclaw.internal.example
```

- 默认命令会从 `hostname -I` 中选取首个 RFC1918 私网 IPv4，并把 `OPENCLAW_TLS_CN` 默认写成 `openclaw.internal.example`。
- 执行后会统一回填 `deploy/site.env` 中的 `OPENCLAW_INGRESS_LISTEN_IP` / `OPENCLAW_TLS_CN`，并在终端打印当前平台的访问端 DNS / hosts 命令；默认平台是 Windows，可用 --platform 切换。
- 若实际部署网卡不是首个私网 IPv4，必须改用 `-- <listen_ip> <tls_cn>` 显式覆盖。
- 只有浏览器与目标服务位于同一操作系统实例时，才允许把 `<listen_ip>` 填写为 `127.0.0.1`；跨 OS / 跨网络实例访问必须填写目标机私网地址。

`OPENCLAW_INGRESS_LISTEN_IP` 必须是目标机自己的私网或 loopback IP；默认先使用 `hostname -I` 输出中的首个私网 IPv4，即 `<hostname -I 首个私网 IPv4>`，也可手工使用 ULA/loopback IPv6。只有浏览器与目标服务位于同一操作系统实例时，才允许手工使用 loopback；跨 OS / 跨网络实例访问不属于该例外。它不能是 hostname、`0.0.0.0`、`::` 或公网地址。

`OPENCLAW_TLS_CN` 示例可写为 `openclaw.internal.example`。它必须同时承担：

- 浏览器访问主机名；
- 证书精确 dNSName SAN；
- Gateway Control UI origin。

访问端必须能把：

```text
OPENCLAW_TLS_CN -> OPENCLAW_INGRESS_LISTEN_IP
```

解析成功。没有内网 DNS 就写 hosts。

宿主机若启用防火墙，只开放 80/443。firewalld 环境还必须保留 permanent `docker` zone 且 target 为 `ACCEPT`，避免 Docker bridge / NAT 在 compose up 时遇到 `INVALID_ZONE: docker`。Nginx 会按 `OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS` 渲染本地 allowlist；来源限制必须在宿主机防火墙、ACL 或 DOCKER-USER 中显式落地，并保持默认拒绝；若环境启用 IPv6，则必须同时提供 IPv6 边界证据，或在宿主机 / 上游网络层显式禁用 IPv6 对外暴露。

## 宿主机预检

```bash
bash ./scripts/doctor/check_docker_host_readiness.sh
```

离线镜像场景可追加：

```bash
bash ./scripts/doctor/check_docker_host_readiness.sh --offline
```
