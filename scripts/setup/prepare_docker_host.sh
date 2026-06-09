#!/usr/bin/env bash
# 用途：统一准备 CentOS 7 存量老系统路径 Docker 宿主机；支持 CentOS 7 vault repo 修复、预工具安装、Docker / Compose 安装、daemon 基线与防火墙开放。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/setup/lib/setup_flow_handoff.sh"
source "$ROOT_DIR/scripts/setup/lib/setup_cli_common.sh"
# shellcheck source=../lib/docker_host_support_truth.sh
source "$ROOT_DIR/scripts/lib/docker_host_support_truth.sh"
# shellcheck source=../lib/system_time_guard.sh
source "$ROOT_DIR/scripts/lib/system_time_guard.sh"
HOST_SUPPORT_POLICY_PATH="$(docker_host_support_truth_path)"
DEFAULT_TIMEZONE="Asia/Shanghai"
OFFLINE_MODE=0
RUN_REPAIR_REPOS=0
RUN_INSTALL_BASE_TOOLS=0
RUN_UPDATE_SYSTEM_TIME=0
RUN_CONFIGURE_KERNEL=0
RUN_INSTALL_DOCKER=0
RUN_INSTALL_COMPOSE=0
RUN_CONFIGURE_DAEMON=0
RUN_OPEN_FIREWALL=0
RUN_ALL=0
FORCE_REPAIR_REPOS=0
CENTOS7_VAULT_SOURCE="${OPENCLAW_CENTOS7_VAULT_SOURCE:-all}"
DOCKER_REPO_SOURCE="${OPENCLAW_DOCKER_CENTOS_REPO_SOURCE:-all}"
NETWORK_PROFILE="${OPENCLAW_DEPLOY_NETWORK_PROFILE:-auto}"
CENTOS7_VAULT_SOURCE_EXPLICIT=0
DOCKER_REPO_SOURCE_EXPLICIT=0
[[ -n "${OPENCLAW_CENTOS7_VAULT_SOURCE:-}" ]] && CENTOS7_VAULT_SOURCE_EXPLICIT=1
[[ -n "${OPENCLAW_DOCKER_CENTOS_REPO_SOURCE:-}" ]] && DOCKER_REPO_SOURCE_EXPLICIT=1

usage() {
  local purpose=''
  local examples=''
  local notes=''
  local help_lines=''
  local references=''
  purpose="$(setup_cli_docker_host_scalar_from_truth '.entrypoint.purpose' 'entrypoint.purpose')"
  examples="$(setup_cli_docker_host_lines_from_truth '.entrypoint.command_examples' 'entrypoint.command_examples')"
  notes="$(setup_cli_docker_host_lines_from_truth '.entrypoint.notes' 'entrypoint.notes')"
  help_lines="$(setup_cli_docker_host_lines_from_truth '.help_surface.lines' 'help_surface.lines')"
  references="$(setup_cli_docker_host_lines_from_truth '.entrypoint.references' 'entrypoint.references')"
  cat <<'USAGE'
用法：
  sudo bash ./scripts/setup/prepare_docker_host.sh [选项]
USAGE
  if [[ -n "$purpose" ]]; then
    echo
    echo "$purpose"
  fi
  if [[ -n "$examples" ]]; then
    echo
    echo '常用命令：'
    local line=''
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      echo "  $line"
    done <<< "$examples"
  fi
  if [[ -n "$notes" ]]; then
    echo
    echo '说明：'
    local line=''
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      echo "  - $line"
    done <<< "$notes"
  fi
  if [[ -n "$help_lines" ]]; then
    echo
    echo '帮助面边界：'
    local line=''
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      echo "  - $line"
    done <<< "$help_lines"
  fi
  if [[ -n "$references" ]]; then
    echo
    echo '统一参考：'
    local line=''
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      echo "  - $line"
    done <<< "$references"
  fi
  cat <<'USAGE'

选项：
  --all                             执行完整宿主机准备流程。
  --offline                         仅跳过外网探测 / 在线仓库刷新；不适用于在线安装预工具、Docker、Compose。
  --repair-centos7-vault-repos      强制把 CentOS 7 Base repo 切到已登记 vault 候选源。
  --centos7-vault-source <id|all>   选择 CentOS 7 vault repo；默认 all，按 official -> aliyun_cn -> tuna_cn 自动回退。
  --network-profile <auto|cn|global> 按部署网络 profile 选择 repo 默认源；cn 会把 CentOS 7 vault 与 Docker Yum repo 固定到 aliyun_cn。
  --install-base-tools              安装 vim / less / wget / jq / bind-utils / net-tools / lsof 等预工具。
  --update-system-time              设置时区、启用 NTP，并按多源 HTTPS HTTP Date 基准校验 / 修复系统时间。
  --configure-kernel                配置时区、系统时间、br_netfilter 与 sysctl。
  --install-docker                  安装 Docker Engine 建议版本。
  --install-compose                 安装 Docker Compose V2 插件。
  --docker-repo-source <id|all>      选择 Docker Yum repo；默认 all，按 official -> aliyun_cn -> tuna_cn 自动回退。
  --configure-daemon                写入 /etc/docker/daemon.json 并重启 Docker。
  --open-firewall                   若 firewalld 正在运行，先预开放 80/443，并收口 Docker permanent docker zone；host_firewall 的来源限制需在 one_click_config 后通过 apply_ingress_boundary_rules.sh 物化。
  -h, --help                        显示帮助。
USAGE
  setup_help_surface_guarantee_text
}

note() {
  echo "[prepare_docker_host][INFO] $1"
}

warn() {
  echo "[prepare_docker_host][WARN] $1"
}

fail() {
  echo "[prepare_docker_host][FAIL] $1" >&2
  exit "${2:-2}"
}

require_root() {
  [[ "$(id -u)" == "0" ]] || fail '当前脚本需要 root 权限；请使用 sudo bash ./scripts/setup/prepare_docker_host.sh ...' 30
}

require_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || fail "缺少命令：$cmd" 31
}

DOCKER_VERSION="$(docker_host_support_supported_centos7_section_value "$HOST_SUPPORT_POLICY_PATH" docker_server recommended 26.1.4)"
COMPOSE_VERSION="$(docker_host_support_supported_centos7_section_value "$HOST_SUPPORT_POLICY_PATH" docker_compose recommended 2.27.1)"
DOCKER_RPM_VERSION="${DOCKER_VERSION}-1.el7"
COMPOSE_RPM_VERSION="${COMPOSE_VERSION}-1.el7"
JQ_DIRECT_BINARY_VERSION="1.7.1"

compose_download_sha256() {
  case "$(compose_arch_suffix)" in
    x86_64)
      docker_host_support_supported_centos7_section_value "$HOST_SUPPORT_POLICY_PATH" compose_binary_linux_x86_64 sha256 ''
      ;;
    *)
      printf '
'
      ;;
  esac
}

jq_download_sha256() {
  local asset_name=''
  asset_name="$(select_jq_download_asset)"
  case "$asset_name" in
    jq-linux-amd64)
      docker_host_support_supported_centos7_section_value "$HOST_SUPPORT_POLICY_PATH" jq_binary_linux_amd64 sha256 ''
      ;;
    *)
      printf '
'
      ;;
  esac
}

verify_download_sha256() {
  local file_path="$1"
  local expected_sha256="$2"
  local label="$3"
  local actual_sha256=''
  [[ -n "$expected_sha256" ]] || fail "$label 缺少已登记 SHA256；当前宿主机不允许在未登记校验值时继续二进制回退。" 43
  require_cmd sha256sum
  actual_sha256="$(sha256sum "$file_path" | awk '{print $1}')"
  [[ "$actual_sha256" == "$expected_sha256" ]] || fail "$label 下载校验失败：期望 $expected_sha256，实际 $actual_sha256。已拒绝安装未校验二进制。" 43
}

is_centos7() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    [[ "${ID:-}" == "centos" && "${VERSION_ID:-}" == 7* ]]
    return
  fi
  [[ -f /etc/centos-release ]] && grep -Eq 'CentOS( Linux)? release 7' /etc/centos-release
}

compose_arch_suffix() {
  local arch=''
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64)
      printf 'x86_64\n'
      ;;
    aarch64|arm64)
      printf 'aarch64\n'
      ;;
    *)
      fail "当前架构暂未登记 Compose 下载后缀：$arch；请先补充 prepare_docker_host.sh" 32
      ;;
  esac
}

compose_plugin_path() {
  printf '/usr/local/lib/docker/cli-plugins/docker-compose\n'
}

compose_download_url() {
  if [[ -n "${OPENCLAW_COMPOSE_DOWNLOAD_URL:-}" ]]; then
    printf '%s\n' "${OPENCLAW_COMPOSE_DOWNLOAD_URL}"
    return 0
  fi
  local arch_suffix=''
  arch_suffix="$(compose_arch_suffix)"
  printf 'https://github.com/docker/compose/releases/download/v%s/docker-compose-linux-%s\n' "$COMPOSE_VERSION" "$arch_suffix"
}

current_compose_version() {
  local output=''
  local version=''
  if output="$(docker compose version --short 2>/dev/null)"; then
    version="${output#v}"
    version="${version%%[!0-9.]*}"
    if [[ -n "$version" ]]; then
      printf '%s\n' "$version"
      return 0
    fi
  fi
  if output="$(docker compose version 2>/dev/null)"; then
    version="$(printf '%s\n' "$output" | grep -Eo 'v?[0-9]+\.[0-9]+\.[0-9]+' | head -n 1 | sed 's/^v//' || true)"
    if [[ -n "$version" ]]; then
      printf '%s\n' "$version"
      return 0
    fi
  fi
  return 1
}

compose_version_is_recommended() {
  local version=''
  version="$(current_compose_version || true)"
  [[ "$version" == "$COMPOSE_VERSION" ]]
}

compose_rpm_version() {
  rpm -q --qf '%{VERSION}\n' docker-compose-plugin 2>/dev/null || true
}

compose_rpm_is_recommended() {
  [[ "$(compose_rpm_version)" == "$COMPOSE_VERSION" ]]
}

remove_local_compose_plugin() {
  local plugin_path=''
  plugin_path="$(compose_plugin_path)"
  [[ -f "$plugin_path" ]] || return 0
  rm -f "$plugin_path"
}

verify_compose_installation() {
  docker compose version >/dev/null 2>&1 || fail 'Docker Compose 插件已安装，但 docker compose 命令仍不可用。' 37
  compose_version_is_recommended || fail "Docker Compose 插件版本不符合建议值：期望 $COMPOSE_VERSION，实际 $(current_compose_version || echo '<unknown>')。" 37
}

install_compose_via_binary_fallback() {
  require_cmd curl
  local plugin_path=''
  local plugin_dir=''
  local partial_path=''
  local download_url=''
  local expected_sha256=''
  plugin_path="$(compose_plugin_path)"
  plugin_dir="$(dirname "$plugin_path")"
  partial_path="${plugin_path}.part"
  download_url="$(compose_download_url)"
  mkdir -p "$plugin_dir"
  expected_sha256="$(compose_download_sha256)"
  note "docker-compose-plugin RPM 未就绪；改用二进制下载：$download_url"
  curl -fL     --retry 8     --retry-all-errors     --connect-timeout 10     --speed-time 30     --speed-limit 10240     -C -     "$download_url"     -o "$partial_path"
  verify_download_sha256 "$partial_path" "$expected_sha256" 'Docker Compose 插件'
  mv -f "$partial_path" "$plugin_path"
  chmod 0755 "$plugin_path"
}

select_jq_download_asset() {
  local arch=''
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64)
      printf 'jq-linux-amd64\n'
      ;;
    aarch64|arm64)
      printf 'jq-linux-arm64\n'
      ;;
    *)
      fail "当前架构暂未登记 jq 下载资源：$arch；请先手工安装 jq 后再继续。" 39
      ;;
  esac
}

verify_base_tool_commands() {
  local missing=()
  local cmd=''
  local required=(bash curl wget openssl git tar unzip zip vim less which nslookup dig ss ip lsof jq setfacl)
  for cmd in "${required[@]}"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    fail "宿主机预工具安装未完成；仍缺少命令：${missing[*]}。CentOS 7 常见原因是 jq 不在默认 Base 仓库；脚本已尝试 EPEL / 直接二进制补装。请检查上方安装日志并重新执行 --install-base-tools。" 40
  fi
}

install_jq_via_direct_binary() {
  require_cmd curl
  local asset_name=''
  local target='/usr/local/bin/jq'
  local partial_target=''
  local download_url=''
  local expected_sha256=''
  asset_name="$(select_jq_download_asset)"
  download_url="https://github.com/jqlang/jq/releases/download/jq-${JQ_DIRECT_BINARY_VERSION}/${asset_name}"
  partial_target="${target}.part"
  expected_sha256="$(jq_download_sha256)"
  note "默认 yum 仓库未提供 jq；改用官方静态二进制补装：$download_url"
  curl -fL     --retry 8     --retry-all-errors     --connect-timeout 10     --speed-time 30     --speed-limit 10240     "$download_url"     -o "$partial_target"
  verify_download_sha256 "$partial_target" "$expected_sha256" 'jq'
  mv -f "$partial_target" "$target"
  chmod 0755 "$target"
}

ensure_jq_installed() {
  if command -v jq >/dev/null 2>&1; then
    return 0
  fi
  [[ "$OFFLINE_MODE" == "1" ]] && fail '--offline 模式下无法在线补装 jq；请先通过本地 RPM / 本地仓库提供 jq，或把 jq 二进制预放到 /usr/local/bin/jq。' 41

  warn '当前系统尚未检测到 jq；开始执行补装逻辑。'

  if yum install -y jq; then
    command -v jq >/dev/null 2>&1 && return 0
    warn 'yum install jq 已返回成功，但 jq 命令仍不存在；继续尝试补装。'
  else
    warn '默认 yum 仓库未直接提供 jq；继续尝试启用 EPEL。'
  fi

  if ! rpm -q epel-release >/dev/null 2>&1; then
    if yum install -y epel-release; then
      note 'epel-release 已安装。'
    else
      warn '通过 yum 安装 epel-release 失败；继续尝试直接安装 epel-release RPM。'
      yum install -y https://dl.fedoraproject.org/pub/epel/epel-release-latest-7.noarch.rpm || warn '直接安装 epel-release RPM 失败；继续尝试 jq 静态二进制。'
    fi
  fi

  if rpm -q epel-release >/dev/null 2>&1; then
    yum clean all >/dev/null 2>&1 || true
    yum_makecache_maybe_repair || true
    if yum install -y jq; then
      command -v jq >/dev/null 2>&1 && return 0
      warn 'EPEL 中 jq 安装返回成功，但 jq 命令仍不存在；继续尝试静态二进制。'
    else
      warn 'EPEL 已存在，但 yum install jq 仍失败；继续尝试静态二进制。'
    fi
  fi

  install_jq_via_direct_binary
  command -v jq >/dev/null 2>&1 || fail 'jq 补装失败；请检查网络出口或手工提供 jq 后重试。' 42
}

yum_makecache_maybe_repair() {
  [[ "$OFFLINE_MODE" == "1" ]] && return 0
  local cache_log=""
  cache_log="$(mktemp)"
  if yum makecache fast >"$cache_log" 2>&1; then
    rm -f "$cache_log"
    return 0
  fi
  if is_centos7 && grep -q 'Cannot find a valid baseurl for repo' "$cache_log"; then
    warn '检测到 CentOS 7 YUM baseurl 异常；自动切换到已登记 CentOS 7 vault 候选源。'
    cat "$cache_log" >&2
    repair_centos7_vault_repos
    rm -f "$cache_log"
    yum clean all >/dev/null 2>&1 || true
    yum makecache fast
    return 0
  fi
  cat "$cache_log" >&2
  rm -f "$cache_log"
  fail 'yum makecache fast 失败；请先修复仓库配置。' 33
}

centos7_vault_candidate_lines() {
  if [[ -n "${OPENCLAW_CENTOS7_VAULT_BASE_URL:-}" ]]; then
    printf '%s\t%s\n' "${OPENCLAW_CENTOS7_VAULT_SOURCE:-override}" "$OPENCLAW_CENTOS7_VAULT_BASE_URL"
    return 0
  fi
  local id='' base_url=''
  while IFS=$'\t' read -r id base_url; do
    [[ -n "$id" && -n "$base_url" ]] || continue
    if [[ "$CENTOS7_VAULT_SOURCE" == "all" || "$CENTOS7_VAULT_SOURCE" == "$id" ]]; then
      printf '%s\t%s\n' "$id" "$base_url"
    fi
  done < <(docker_host_support_supported_centos7_vault_repo_candidates "$HOST_SUPPORT_POLICY_PATH")
}

write_centos7_base_repo() {
  local repo_dir="$1"
  local base_url="${2%/}"
  cat > "$repo_dir/CentOS-Base.repo" <<REPO
[base]
name=CentOS-7 - Base
baseurl=${base_url}/os/\$basearch/
gpgcheck=1
enabled=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7

[updates]
name=CentOS-7 - Updates
baseurl=${base_url}/updates/\$basearch/
gpgcheck=1
enabled=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7

[extras]
name=CentOS-7 - Extras
baseurl=${base_url}/extras/\$basearch/
gpgcheck=1
enabled=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7

[centosplus]
name=CentOS-7 - Plus
baseurl=${base_url}/centosplus/\$basearch/
gpgcheck=1
enabled=0
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7
REPO
}

repair_centos7_vault_repos() {
  is_centos7 || fail '仅 CentOS 7 需要执行 vault repo 修复。' 34
  local repo_dir='/etc/yum.repos.d'
  local backup_dir=''
  local repo_specs=()
  local repo_spec='' repo_id='' base_url='' attempted_repos=''
  backup_dir="$repo_dir/openclaw-backup-$(date +%Y%m%d%H%M%S)"
  mkdir -p "$backup_dir"
  find "$repo_dir" -maxdepth 1 -type f -name '*.repo' -exec cp -a {} "$backup_dir"/ \;
  note "已备份原始 repo 文件到：$backup_dir"
  mapfile -t repo_specs < <(centos7_vault_candidate_lines)
  (( ${#repo_specs[@]} > 0 )) || fail "未找到可用 CentOS 7 vault repo 候选：CENTOS7_VAULT_SOURCE=$CENTOS7_VAULT_SOURCE。" 34
  for repo_spec in "${repo_specs[@]}"; do
    repo_id="${repo_spec%%$'\t'*}"
    base_url="${repo_spec#*$'\t'}"
    [[ -z "$attempted_repos" ]] || attempted_repos+=","
    attempted_repos+="$repo_id"
    write_centos7_base_repo "$repo_dir" "$base_url"
    yum clean all >/dev/null 2>&1 || true
    if yum --disablerepo='*' --enablerepo=base --enablerepo=updates --enablerepo=extras makecache fast >/dev/null 2>&1; then
      note "已重写 /etc/yum.repos.d/CentOS-Base.repo -> $base_url；repo=$repo_id"
      return 0
    fi
    warn "CentOS 7 vault repo 刷新失败：$repo_id -> $base_url；继续尝试下一个候选。"
  done
  fail "CentOS 7 vault repo 修复失败；已尝试：$attempted_repos。中国国内网络优先执行 --network-profile cn，单独修复 repo 时可显式执行 --centos7-vault-source aliyun_cn；内网 vault 可设置 OPENCLAW_CENTOS7_VAULT_BASE_URL。" 34
}

install_base_tools() {
  [[ "$OFFLINE_MODE" == "1" ]] && fail '--offline 模式下不能执行在线 yum 安装；请先准备本地仓库或取消 --offline。' 35
  yum_makecache_maybe_repair
  yum install -y \
    yum-utils \
    device-mapper-persistent-data \
    lvm2 \
    curl \
    wget \
    git \
    tar \
    unzip \
    zip \
    vim-enhanced \
    less \
    which \
    bind-utils \
    net-tools \
    lsof \
    rsync \
    ca-certificates \
    chrony \
    iptables \
    iproute \
    acl \
    openssl
  ensure_jq_installed
  verify_base_tool_commands
  note '预工具安装完成。'
  note "vim 路径：$(command -v vim)"
  note "jq 路径：$(command -v jq)"
  note "dig 路径：$(command -v dig)"
  note "ss 路径：$(command -v ss)"
  note "ip 路径：$(command -v ip)"
}

configure_kernel() {
  update_system_time
  cat > /etc/modules-load.d/br_netfilter.conf <<'EOF2'
br_netfilter
EOF2
  modprobe br_netfilter
  cat > /etc/sysctl.d/99-docker.conf <<'EOF2'
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
EOF2
  sysctl --system >/dev/null
  note '时区、系统时间、内核模块与 sysctl 准备完成。'
}

update_system_time() {
  local args=(--timezone "$DEFAULT_TIMEZONE")
  [[ "$OFFLINE_MODE" == "1" ]] && args+=(--offline)
  system_time_guard_update "${args[@]}" || fail '系统时间自动校验 / 更新失败；请先修复 NTP、网络时间基准或使用 --offline 明确跳过外部基准。' 44
}

preflight_system_time_before_network_install() {
  [[ "$OFFLINE_MODE" == "1" ]] && return 0
  command -v curl >/dev/null 2>&1 || {
    warn '缺少 curl；跳过网络安装前 HTTP Date 预校时。若后续 yum / TLS 报证书时间错误，请先补 curl 或执行 --install-base-tools 后再运行 --update-system-time。'
    return 0
  }
  note '执行网络安装前系统时间预校验。'
  system_time_guard_update --timezone "$DEFAULT_TIMEZONE" || fail '网络安装前系统时间预校验失败；请先修复 NTP / HTTP Date 参考端点，或设置 OPENCLAW_SYSTEM_TIME_REFERENCE_URLS 指向当前网络可达端点后重试。' 44
}

docker_repo_candidate_lines() {
  if [[ -n "${OPENCLAW_DOCKER_CENTOS_REPO_URL:-}" ]]; then
    printf '%s\t%s\n' "${OPENCLAW_DOCKER_CENTOS_REPO_SOURCE:-override}" "$OPENCLAW_DOCKER_CENTOS_REPO_URL"
    return 0
  fi
  local id='' url=''
  while IFS=$'\t' read -r id url; do
    [[ -n "$id" && -n "$url" ]] || continue
    if [[ "$DOCKER_REPO_SOURCE" == "all" || "$DOCKER_REPO_SOURCE" == "$id" ]]; then
      printf '%s\t%s\n' "$id" "$url"
    fi
  done < <(docker_host_support_supported_centos7_docker_repo_candidates "$HOST_SUPPORT_POLICY_PATH")
}

install_docker() {
  [[ "$OFFLINE_MODE" == "1" ]] && fail '--offline 模式下不能执行在线 Docker 安装；请先准备离线 RPM 或取消 --offline。' 36
  if ! (yum_makecache_maybe_repair); then
    warn 'Docker 安装前 yum makecache 预热失败；继续进入 Docker Yum repo 候选循环，由每个候选源独立刷新并给出最终错误。'
  fi
  require_cmd yum-config-manager
  local repo_specs=()
  local repo_spec='' repo_id='' repo_url='' attempted_repos='' install_status=0
  mapfile -t repo_specs < <(docker_repo_candidate_lines)
  (( ${#repo_specs[@]} > 0 )) || fail "未找到可用 Docker Yum repo 候选：DOCKER_REPO_SOURCE=$DOCKER_REPO_SOURCE。" 36
  yum remove -y docker docker-client docker-client-latest docker-common docker-latest docker-latest-logrotate docker-logrotate docker-engine docker-ce docker-ce-cli >/dev/null 2>&1 || true
  for repo_spec in "${repo_specs[@]}"; do
    repo_id="${repo_spec%%$'\t'*}"
    repo_url="${repo_spec#*$'\t'}"
    [[ -z "$attempted_repos" ]] || attempted_repos+=","
    attempted_repos+="$repo_id"
    note "准备使用 Docker Yum repo：$repo_id -> $repo_url"
    if ! yum-config-manager --add-repo "$repo_url" >/dev/null; then
      warn "Docker Yum repo 写入失败：$repo_id；继续尝试下一个候选。"
      continue
    fi
    if ! (yum_makecache_maybe_repair); then
      warn "Docker Yum repo 刷新失败：$repo_id；继续尝试下一个候选。"
      continue
    fi
    set +e
    yum install -y "docker-ce-${DOCKER_RPM_VERSION}" "docker-ce-cli-${DOCKER_RPM_VERSION}" containerd.io
    install_status=$?
    set -e
    if [[ "$install_status" -eq 0 ]]; then
      systemctl enable --now docker
      docker version >/dev/null
      note "Docker Engine 已安装并启动；建议版本：$DOCKER_VERSION；repo=$repo_id"
      return 0
    fi
    warn "Docker Engine RPM 安装失败：repo=$repo_id exit=$install_status；继续尝试下一个候选。"
  done
  fail "Docker Engine 安装失败；已尝试 Docker Yum repo：$attempted_repos。中国国内网络优先执行 --network-profile cn，单独修复 Docker repo 时可显式执行 --docker-repo-source aliyun_cn；内网制品源可设置 OPENCLAW_DOCKER_CENTOS_REPO_URL。" 36
}

install_compose() {
  [[ "$OFFLINE_MODE" == "1" ]] && fail '--offline 模式下不能在线安装 Compose 插件；请先准备本地 RPM / YUM 源，或取消 --offline 后重新执行。' 37
  require_cmd docker

  if compose_rpm_is_recommended; then
    if [[ -f "$(compose_plugin_path)" ]]; then
      note '检测到 docker-compose-plugin RPM 与 /usr/local/lib/docker/cli-plugins/docker-compose 并存；移除本地覆盖件，统一回 RPM 安装源。'
      remove_local_compose_plugin
    fi
    verify_compose_installation
    note "Docker Compose 插件已由 RPM 提供；建议版本：$COMPOSE_VERSION"
    return 0
  fi

  if compose_version_is_recommended; then
    note "Docker Compose 插件已存在；建议版本：$COMPOSE_VERSION"
    return 0
  fi

  if yum install -y "docker-compose-plugin-${COMPOSE_RPM_VERSION}"; then
    if [[ -f "$(compose_plugin_path)" ]]; then
      note '检测到 /usr/local/lib/docker/cli-plugins/docker-compose 本地覆盖件；移除后统一回 RPM 安装源。'
      remove_local_compose_plugin
    fi
    verify_compose_installation
    note "Docker Compose 插件已通过 RPM 安装；建议版本：$COMPOSE_VERSION"
    return 0
  fi

  install_compose_via_binary_fallback
  verify_compose_installation
  note "Docker Compose 插件已通过二进制回退安装；建议版本：$COMPOSE_VERSION"
}

backup_if_exists() {
  local path="$1"
  if [[ -f "$path" ]]; then
    cp -a "$path" "${path}.bak.$(date +%Y%m%d%H%M%S)"
  fi
}

json_escape_string() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s' "$value"
}

docker_registry_mirror_lines() {
  if [[ -n "${OPENCLAW_DOCKER_REGISTRY_MIRRORS:-}" ]]; then
    printf '%s\n' "$OPENCLAW_DOCKER_REGISTRY_MIRRORS" | tr ',;' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | awk 'NF'
    return 0
  fi
  docker_host_support_supported_centos7_registry_mirrors "$HOST_SUPPORT_POLICY_PATH"
}

configure_daemon() {
  local mirrors=()
  local mirror=''
  local index=0
  mkdir -p /etc/docker
  backup_if_exists /etc/docker/daemon.json
  mapfile -t mirrors < <(docker_registry_mirror_lines)
  (( ${#mirrors[@]} > 0 )) || fail 'Docker daemon registry-mirrors 候选为空；请检查 docker_host.json 或 OPENCLAW_DOCKER_REGISTRY_MIRRORS。' 45
  {
    cat <<'EOF2'
{
  "registry-mirrors": [
EOF2
    for index in "${!mirrors[@]}"; do
      mirror="${mirrors[$index]}"
      [[ -n "$mirror" ]] || continue
      if (( index + 1 < ${#mirrors[@]} )); then
        printf '    "%s",\n' "$(json_escape_string "$mirror")"
      else
        printf '    "%s"\n' "$(json_escape_string "$mirror")"
      fi
    done
    cat <<'EOF2'
  ],
  "max-concurrent-downloads": 6,
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  },
  "storage-driver": "overlay2"
}
EOF2
  } > /etc/docker/daemon.json
  systemctl restart docker
  note "Docker daemon 配置已写入并完成重启；registry-mirrors=${mirrors[*]}"
}

firewalld_permanent_zone_exists() {
  local zone="$1"
  firewall-cmd --permanent --get-zones 2>/dev/null | tr ' ' '\n' | grep -Fxq "$zone"
}

DOCKER_FIREWALLD_ZONE_CHANGED=0

ensure_docker_firewalld_zone() {
  local target=''
  firewalld_permanent_zone_exists docker || {
    firewall-cmd --permanent --new-zone=docker >/dev/null
    DOCKER_FIREWALLD_ZONE_CHANGED=1
    note '已创建 firewalld permanent docker zone，避免 Docker bridge / NAT 编程遇到 INVALID_ZONE。'
  }
  target="$(firewall-cmd --permanent --zone=docker --get-target 2>/dev/null || true)"
  if [[ "$target" != "ACCEPT" ]]; then
    firewall-cmd --permanent --zone=docker --set-target=ACCEPT >/dev/null
    DOCKER_FIREWALLD_ZONE_CHANGED=1
    note '已把 firewalld docker zone target 设置为 ACCEPT。'
  fi
}

smoke_test_docker_bridge_network() {
  local network_name="openclaw-firewalld-smoke-$$"
  local err_file=''
  local status=0
  command -v docker >/dev/null 2>&1 || return 0
  systemctl is-active --quiet docker || return 0
  docker info >/dev/null 2>&1 || return 0
  err_file="$(mktemp)"
  set +e
  docker network create "$network_name" >/dev/null 2>"$err_file"
  status=$?
  set -e
  if [[ "$status" -ne 0 ]]; then
    local err=''
    err="$(cat "$err_file" 2>/dev/null || true)"
    rm -f "$err_file"
    if printf '%s' "$err" | grep -q 'INVALID_ZONE.*docker'; then
      fail "Docker bridge NAT 烟测失败：firewalld 缺少可用 docker zone（$err）。请重新执行 sudo bash ./scripts/setup/prepare_docker_host.sh --open-firewall，或按 troubleshooting 的 INVALID_ZONE 修复段处理。" 48
    fi
    fail "Docker bridge NAT 烟测失败：$err" 48
  fi
  rm -f "$err_file"
  docker network rm "$network_name" >/dev/null 2>&1 || warn "Docker bridge 烟测网络清理失败：$network_name"
  note 'Docker bridge / firewalld NAT 烟测通过。'
}

open_firewall_ports() {
  if ! command -v firewall-cmd >/dev/null 2>&1; then
    warn '未检测到 firewall-cmd；跳过 firewalld 端口开放。'
    return 0
  fi
  if ! systemctl is-active --quiet firewalld; then
    warn 'firewalld 未运行；跳过 80/443 开放。'
    return 0
  fi
  DOCKER_FIREWALLD_ZONE_CHANGED=0
  firewall-cmd --permanent --add-service=http >/dev/null
  firewall-cmd --permanent --add-service=https >/dev/null
  ensure_docker_firewalld_zone
  firewall-cmd --reload >/dev/null
  if [[ "$DOCKER_FIREWALLD_ZONE_CHANGED" == "1" ]] && command -v docker >/dev/null 2>&1 && systemctl is-active --quiet docker; then
    systemctl restart docker
    note 'firewalld docker zone 已变更，Docker daemon 已重启以重新绑定 bridge / NAT 规则。'
  fi
  smoke_test_docker_bridge_network
  note 'firewalld 预开放 80/443 完成；host_firewall 的来源限制需后续通过 apply_ingress_boundary_rules.sh 继续收口。'
}

apply_network_profile_defaults() {
  local profile="$NETWORK_PROFILE"
  local vault_source=''
  local docker_source=''

  case "$profile" in
    ''|auto)
      NETWORK_PROFILE='auto'
      return 0
      ;;
    cn|china|mainland_cn)
      profile='cn'
      ;;
    global|official)
      profile='global'
      ;;
    *)
      fail "不支持的 --network-profile：$NETWORK_PROFILE；可选 auto|cn|global。" 2
      ;;
  esac

  vault_source="$(docker_host_support_supported_centos7_network_profile_value "$HOST_SUPPORT_POLICY_PATH" "$profile" centos7_vault_source '')"
  docker_source="$(docker_host_support_supported_centos7_network_profile_value "$HOST_SUPPORT_POLICY_PATH" "$profile" docker_repo_source '')"
  [[ -n "$vault_source" ]] || fail "network profile 缺少 centos7_vault_source：$profile。" 2
  [[ -n "$docker_source" ]] || fail "network profile 缺少 docker_repo_source：$profile。" 2

  NETWORK_PROFILE="$profile"
  [[ "$CENTOS7_VAULT_SOURCE_EXPLICIT" == "1" ]] || CENTOS7_VAULT_SOURCE="$vault_source"
  [[ "$DOCKER_REPO_SOURCE_EXPLICIT" == "1" ]] || DOCKER_REPO_SOURCE="$docker_source"
}

main() {
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      RUN_ALL=1
      ;;
    --offline)
      OFFLINE_MODE=1
      ;;
    --repair-centos7-vault-repos)
      RUN_REPAIR_REPOS=1
      FORCE_REPAIR_REPOS=1
      ;;
    --centos7-vault-source)
      [[ $# -ge 2 ]] || fail '--centos7-vault-source 缺少参数值。' 2
      CENTOS7_VAULT_SOURCE="$2"
      CENTOS7_VAULT_SOURCE_EXPLICIT=1
      shift
      ;;
    --network-profile)
      [[ $# -ge 2 ]] || fail '--network-profile 缺少参数值。' 2
      NETWORK_PROFILE="$2"
      shift
      ;;
    --install-base-tools)
      RUN_INSTALL_BASE_TOOLS=1
      ;;
    --update-system-time)
      RUN_UPDATE_SYSTEM_TIME=1
      ;;
    --configure-kernel)
      RUN_CONFIGURE_KERNEL=1
      ;;
    --install-docker)
      RUN_INSTALL_DOCKER=1
      ;;
    --install-compose)
      RUN_INSTALL_COMPOSE=1
      ;;
    --docker-repo-source)
      [[ $# -ge 2 ]] || fail '--docker-repo-source 缺少参数值。' 2
      DOCKER_REPO_SOURCE="$2"
      DOCKER_REPO_SOURCE_EXPLICIT=1
      shift
      ;;
    --configure-daemon)
      RUN_CONFIGURE_DAEMON=1
      ;;
    --open-firewall)
      RUN_OPEN_FIREWALL=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1" 2
      ;;
  esac
  shift
done

apply_network_profile_defaults

if [[ "$RUN_ALL" == "1" ]]; then
  RUN_REPAIR_REPOS=1
  RUN_INSTALL_BASE_TOOLS=1
  RUN_CONFIGURE_KERNEL=1
  RUN_INSTALL_DOCKER=1
  RUN_INSTALL_COMPOSE=1
  RUN_CONFIGURE_DAEMON=1
  RUN_OPEN_FIREWALL=1
fi

if [[ "$RUN_REPAIR_REPOS$RUN_INSTALL_BASE_TOOLS$RUN_UPDATE_SYSTEM_TIME$RUN_CONFIGURE_KERNEL$RUN_INSTALL_DOCKER$RUN_INSTALL_COMPOSE$RUN_CONFIGURE_DAEMON$RUN_OPEN_FIREWALL" == "00000000" ]]; then
  usage
  exit 2
fi

if [[ "$OFFLINE_MODE" == "1" && ( "$RUN_ALL" == "1" || "$RUN_INSTALL_BASE_TOOLS" == "1" || "$RUN_INSTALL_DOCKER" == "1" || "$RUN_INSTALL_COMPOSE" == "1" ) ]]; then
  fail '--offline 不能用于在线安装预工具 / Docker / Compose；离线新机请先挂载本地 RPM / YUM 源，然后不要传 --offline，按安装步骤分步执行。当前脚本对 Compose 的唯一安装策略为 RPM 优先、二进制下载后备路径；若当前机器已经具备 Docker / Compose，仅执行 --configure-kernel --configure-daemon --open-firewall。' 38
fi

require_root
note "仓库根目录：$ROOT_DIR"
note "部署网络 profile：$NETWORK_PROFILE；CentOS 7 vault source=$CENTOS7_VAULT_SOURCE；Docker Yum repo source=$DOCKER_REPO_SOURCE。"
if is_centos7; then
  note '检测到 CentOS 7 宿主机。'
else
  warn '当前宿主机不是 CentOS 7；repo 修复逻辑会按需跳过。'
fi

if [[ "$RUN_INSTALL_BASE_TOOLS$RUN_INSTALL_DOCKER$RUN_INSTALL_COMPOSE" != "000" || ( "$RUN_REPAIR_REPOS" == "1" && "$FORCE_REPAIR_REPOS" != "1" ) ]]; then
  preflight_system_time_before_network_install
fi

if [[ "$RUN_REPAIR_REPOS" == "1" ]]; then
  if [[ "$FORCE_REPAIR_REPOS" == "1" ]]; then
    repair_centos7_vault_repos
  elif is_centos7 && [[ "$OFFLINE_MODE" != "1" ]]; then
    yum_makecache_maybe_repair
  elif is_centos7; then
    warn '--offline 模式跳过自动 repo 探测；如需强制修复请显式执行 --repair-centos7-vault-repos。'
  fi
fi

[[ "$RUN_INSTALL_BASE_TOOLS" == "1" ]] && install_base_tools
[[ "$RUN_UPDATE_SYSTEM_TIME" == "1" && "$RUN_CONFIGURE_KERNEL" != "1" ]] && update_system_time
[[ "$RUN_CONFIGURE_KERNEL" == "1" ]] && configure_kernel
[[ "$RUN_INSTALL_DOCKER" == "1" ]] && install_docker
[[ "$RUN_INSTALL_COMPOSE" == "1" ]] && install_compose
[[ "$RUN_CONFIGURE_DAEMON" == "1" ]] && configure_daemon
[[ "$RUN_OPEN_FIREWALL" == "1" ]] && open_firewall_ports

note '宿主机准备脚本执行完成。'
setup_flow_print_unified_handoff '宿主机基础环境阶段' '已完成'
}

main "$@"
