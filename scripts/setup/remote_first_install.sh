#!/usr/bin/env bash
# 用途：远程首次安装向导；默认 dry-run，显式 --apply 后才执行写入、传输或部署动作。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/cidr_contract.sh
source "$ROOT_DIR/scripts/lib/cidr_contract.sh"

TS="$(date +%Y%m%d-%H%M%S)"
STATE_DIR="$ROOT_DIR/state/remote_first_install/$TS"
LOG_PATH="$STATE_DIR/remote_first_install.log"
SUMMARY_PATH="$STATE_DIR/summary.md"
STATUS_PATH="$STATE_DIR/status.env"
STATE_INITIALIZED=0

HOST=""
REPO_DIR="/opt/openclaw/clawctl"
DEPLOY_USER="openclaw"
BRANCH="main"
GIT_URL=""
BUNDLE_PATH=""
TLS_CN=""
LISTEN_IP=""
CLIENT_CIDR=""
NETWORK_PROFILE="${OPENCLAW_DEPLOY_NETWORK_PROFILE:-cn}"
CONTROL_PLANE_PROFILE="${OPENCLAW_CONTROL_PLANE_PROFILE:-agent_platform}"
APPLY=0
PLAN_JSON=0
RUN_PREFLIGHT=0
RUN_STAGE_BUNDLE=0
RUN_PREPARE_REPO=0
RUN_CONFIGURE_BASE=0
RUN_DEPLOY=0
SSH_PORT=""
SSH_OPTS=()

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/remote_first_install.sh --preflight --host <ssh-target>
  bash ./scripts/setup/remote_first_install.sh --apply --host <ssh-target> --repo-dir /opt/openclaw/clawctl --deploy-user openclaw --git-url <url> --prepare-repo --configure-base --deploy

阶段：
  --preflight        SSH、sudo、OS、Docker、Compose、端口、磁盘、目标路径、已有容器只读检查
  --stage-bundle     传输本地源码 bundle 或使用 --git-url；不会传输 state/、deploy/.env、secret
  --prepare-repo     创建 repo 目录、切换 main、执行 prepare_deploy_user.sh
  --configure-base   生成 deploy/site.env，只写基础平台必需字段
  --deploy           执行 config、边界规则、权限修复、basic gate、deploy、acceptance 主链

安全约束：
  - 默认 dry-run；显式 --apply 才执行远程写入、scp、git clone、容器启动。
  - 不接受命令行明文密码或 secret；使用 SSH key/agent，secret 只通过远端 env 或 owner-only 文件导入。
  - stage-bundle 排除 state/、deploy/.env、deploy/site.env、agent/extensions/*/deploy/extension.env 与常见 secret 文件。

常用参数：
  --host <ssh-target>          SSH 目标，例如 user@host
  --repo-dir <path>            远端仓库目录，默认 /opt/openclaw/clawctl
  --deploy-user <user>         固定部署用户，默认 openclaw
  --git-url <url>              远端 git clone URL
  --bundle <path>              本地源码 bundle；未提供时 stage-bundle 会临时打包当前仓库
  --branch <name>              目标分支，默认 main
  --tls-cn <host>              configure-base 写入 OPENCLAW_TLS_CN
  --listen-ip <ip>             configure-base 写入 OPENCLAW_INGRESS_LISTEN_IP
  --client-cidr <cidr[,cidr]>  configure-base 写入 OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS，支持逗号分隔多个来源段
  --control-plane-profile <id> configure-base 写入 OPENCLAW_CONTROL_PLANE_PROFILE，默认 agent_platform
  --ssh-port <port>            SSH/scp 端口，默认使用客户端默认值 22
  --ssh-option <opt>           追加 ssh/scp option，例如 -oStrictHostKeyChecking=accept-new
  --plan-json                  只输出结构化阶段计划；不写本地 state，不执行 SSH、scp 或远端命令
  --apply                     执行写入动作；缺省只输出计划
USAGE
}

shell_quote() {
  printf '%q' "$1"
}

init_state() {
  if [[ "$STATE_INITIALIZED" == '0' ]]; then
    mkdir -p "$STATE_DIR"
    : > "$LOG_PATH"
    STATE_INITIALIZED=1
  fi
}

log() {
  init_state
  printf '%s\n' "$*" | tee -a "$LOG_PATH"
}

resume_command() {
  local cmd='bash ./scripts/setup/remote_first_install.sh'
  [[ -n "$HOST" ]] && cmd+=" --host $(shell_quote "$HOST")" || cmd+=' --host <ssh-target>'
  cmd+=" --repo-dir $(shell_quote "$REPO_DIR")"
  cmd+=" --deploy-user $(shell_quote "$DEPLOY_USER")"
  [[ "$APPLY" == '1' ]] && cmd+=' --apply'
  [[ "$BRANCH" != 'main' ]] && cmd+=" --branch $(shell_quote "$BRANCH")"
  [[ -n "$GIT_URL" ]] && cmd+=" --git-url $(shell_quote "$GIT_URL")"
  [[ -n "$BUNDLE_PATH" ]] && cmd+=" --bundle $(shell_quote "$BUNDLE_PATH")"
  [[ -n "$TLS_CN" ]] && cmd+=" --tls-cn $(shell_quote "$TLS_CN")"
  [[ -n "$LISTEN_IP" ]] && cmd+=" --listen-ip $(shell_quote "$LISTEN_IP")"
  [[ -n "$CLIENT_CIDR" ]] && cmd+=" --client-cidr $(shell_quote "$CLIENT_CIDR")"
  [[ "$CONTROL_PLANE_PROFILE" != 'agent_platform' ]] && cmd+=" --control-plane-profile $(shell_quote "$CONTROL_PLANE_PROFILE")"
  [[ "$NETWORK_PROFILE" != "${OPENCLAW_DEPLOY_NETWORK_PROFILE:-cn}" ]] && cmd+=" --network-profile $(shell_quote "$NETWORK_PROFILE")"
  [[ -n "$SSH_PORT" ]] && cmd+=" --ssh-port $(shell_quote "$SSH_PORT")"
  local opt=''
  for opt in "${SSH_OPTS[@]}"; do
    cmd+=" --ssh-option $(shell_quote "$opt")"
  done
  if [[ "$RUN_PREFLIGHT" == '1' || "$RUN_STAGE_BUNDLE" == '1' || "$RUN_PREPARE_REPO" == '1' || "$RUN_CONFIGURE_BASE" == '1' || "$RUN_DEPLOY" == '1' ]]; then
    [[ "$RUN_PREFLIGHT" == '1' ]] && cmd+=' --preflight'
    [[ "$RUN_STAGE_BUNDLE" == '1' ]] && cmd+=' --stage-bundle'
    [[ "$RUN_PREPARE_REPO" == '1' ]] && cmd+=' --prepare-repo'
    [[ "$RUN_CONFIGURE_BASE" == '1' ]] && cmd+=' --configure-base'
    [[ "$RUN_DEPLOY" == '1' ]] && cmd+=' --deploy'
  else
    cmd+=' --preflight'
  fi
  printf '%s\n' "$cmd"
}

fail() {
  if [[ "${PLAN_JSON:-0}" == '1' && "$STATE_INITIALIZED" == '0' ]]; then
    printf '[FAIL] %s\n' "$*" >&2
    exit 2
  fi
  log "[FAIL] $*"
  write_summary failed "$*"
  exit 2
}

write_status() {
  init_state
  local stage="$1"
  local status="$2"
  local resume=''
  resume="$(resume_command)"
  {
    printf 'CURRENT_STAGE=%q\n' "$stage"
    printf 'STATUS=%q\n' "$status"
    printf 'HOST=%q\n' "$HOST"
    printf 'REPO_DIR=%q\n' "$REPO_DIR"
    printf 'DEPLOY_USER=%q\n' "$DEPLOY_USER"
    printf 'CONTROL_PLANE_PROFILE=%q\n' "$CONTROL_PLANE_PROFILE"
    printf 'LOG_PATH=%q\n' "$LOG_PATH"
    printf 'SUMMARY_PATH=%q\n' "$SUMMARY_PATH"
    printf 'RESUME_COMMAND=%q\n' "$resume"
  } > "$STATUS_PATH"
}

write_summary() {
  init_state
  local status="$1"
  local detail="${2:-}"
  {
    echo "# remote_first_install summary"
    echo
    echo "- status: $status"
    echo "- generated_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "- host: ${HOST:-<unset>}"
    echo "- repo_dir: $REPO_DIR"
    echo "- deploy_user: $DEPLOY_USER"
    echo "- control_plane_profile: $CONTROL_PLANE_PROFILE"
    echo "- log_path: $LOG_PATH"
    echo "- status_path: $STATUS_PATH"
    if [[ -n "$detail" ]]; then
      echo "- detail: $detail"
    fi
    echo
    echo "Resume:"
    echo "  $(resume_command)"
  } > "$SUMMARY_PATH"
}

ssh_base() {
  local port_args=()
  [[ -n "$SSH_PORT" ]] && port_args=(-p "$SSH_PORT")
  ssh "${port_args[@]}" "${SSH_OPTS[@]}" "$HOST" "$@"
}

scp_base() {
  local port_args=()
  [[ -n "$SSH_PORT" ]] && port_args=(-P "$SSH_PORT")
  scp "${port_args[@]}" "${SSH_OPTS[@]}" "$@"
}

validate_simple_value() {
  local label="$1"
  local value="$2"
  local pattern="$3"
  [[ -z "$value" || "$value" =~ $pattern ]] || fail "$label 包含不支持字符：$value"
}

validate_ssh_port() {
  [[ -z "$SSH_PORT" ]] && return 0
  [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || fail "--ssh-port 必须是 1-65535 的整数：$SSH_PORT"
  local port_num=$((10#$SSH_PORT))
  (( port_num >= 1 && port_num <= 65535 )) || fail "--ssh-port 必须是 1-65535 的整数：$SSH_PORT"
}

validate_inputs() {
  validate_simple_value '--host' "$HOST" '^[A-Za-z0-9_.:@-]+$'
  validate_simple_value '--repo-dir' "$REPO_DIR" '^/[A-Za-z0-9_./-]+$'
  validate_simple_value '--deploy-user' "$DEPLOY_USER" '^[a-z_][a-z0-9_-]{0,31}$'
  validate_simple_value '--branch' "$BRANCH" '^[A-Za-z0-9._/-]+$'
  validate_simple_value '--network-profile' "$NETWORK_PROFILE" '^[A-Za-z0-9_.-]+$'
  validate_simple_value '--tls-cn' "$TLS_CN" '^[A-Za-z0-9_.-]+$'
  validate_simple_value '--listen-ip' "$LISTEN_IP" '^[A-Fa-f0-9:.]+$'
  validate_ssh_port
  local cidr_error=''
  if ! cidr_error="$(openclaw_cidr_validate_list "$CLIENT_CIDR" '--client-cidr' 2>&1)"; then
    fail "$cidr_error"
  fi
  validate_simple_value '--control-plane-profile' "$CONTROL_PLANE_PROFILE" '^[a-z0-9_]+$'
  if [[ -n "$GIT_URL" && "$GIT_URL" =~ [[:space:]\'\"\`] ]]; then
    fail '--git-url 包含空白或 shell 特殊引号；请使用不含明文 secret 的标准 SSH/HTTPS git URL。'
  fi
  if [[ -n "$BUNDLE_PATH" && ! -f "$BUNDLE_PATH" ]]; then
    fail "bundle 文件不存在：$BUNDLE_PATH"
  fi
}

json_string() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '"%s"' "$value"
}

json_bool() {
  [[ "$1" == '1' ]] && printf 'true' || printf 'false'
}

json_string_array() {
  local first=1
  local item=''
  printf '['
  for item in "$@"; do
    [[ "$first" == '1' ]] || printf ', '
    first=0
    json_string "$item"
  done
  printf ']'
}

json_cidr_array() {
  local cidr=''
  local first=1
  printf '['
  if [[ -n "$CLIENT_CIDR" ]]; then
    while IFS= read -r cidr; do
      [[ -n "$cidr" ]] || continue
      [[ "$first" == '1' ]] || printf ', '
      first=0
      json_string "$cidr"
    done < <(printf '%s\n' "$CLIENT_CIDR" | tr ',' '\n' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')
  fi
  printf ']'
}

emit_plan_preflight_stage() {
  cat <<'JSON'
    {
      "id": "preflight",
      "title": "SSH 与宿主机只读预检",
      "actor": "ssh-target-user",
      "requiresRoot": false,
      "writesRemote": false,
      "inputs": ["--host", "--repo-dir"],
      "outputs": ["远端 sudo、OS、Docker、Compose、端口、磁盘、目标目录和既有 OpenClaw 容器只读报告"],
      "failureBoundary": "SSH、sudo、Docker/Compose、80/443 端口、目标目录或既有 openclaw-* 容器不满足条件时阻断。"
    }
JSON
}

emit_plan_stage_bundle_stage() {
  cat <<'JSON'
    {
      "id": "stage_bundle",
      "title": "准备远端源码介质",
      "actor": "local-user-and-ssh-target-user",
      "requiresRoot": false,
      "writesRemote": true,
      "inputs": ["--git-url 或 --bundle", "--branch"],
      "outputs": ["/tmp/openclaw-remote-first-install/openclaw-source.tar.gz 或远端 git URL 计划"],
      "failureBoundary": "bundle 不存在、传输失败或 git URL 不满足安全合同则阻断。"
    }
JSON
}

emit_plan_prepare_repo_stage() {
  cat <<'JSON'
    {
      "id": "prepare_repo",
      "title": "创建仓库目录并交接部署用户",
      "actor": "root-via-sudo",
      "requiresRoot": true,
      "writesRemote": true,
      "inputs": ["--repo-dir", "--deploy-user", "--branch", "--git-url 或已传输 bundle"],
      "outputs": ["远端仓库目录", "固定部署用户", "Docker 组成员关系", "仓库目录 owner 交接"],
      "failureBoundary": "目录创建、git clone/fetch、bundle 解包或 prepare_deploy_user.sh 失败时阻断。"
    }
JSON
}

emit_plan_configure_base_stage() {
  cat <<'JSON'
    {
      "id": "configure_base",
      "title": "写入基础平台配置",
      "actor": "deploy-user",
      "requiresRoot": false,
      "writesRemote": true,
      "inputs": ["--control-plane-profile", "--tls-cn", "--listen-ip", "--client-cidr"],
      "outputs": ["deploy/site.env 中的平台必需字段"],
      "failureBoundary": "site.env 初始化或字段写入失败时阻断；secret 不从命令行写入。"
    }
JSON
}

emit_plan_deploy_stage() {
  cat <<'JSON'
    {
      "id": "deploy",
      "title": "远程部署主链",
      "actor": "deploy-user-and-root-via-sudo",
      "requiresRoot": true,
      "writesRemote": true,
      "inputs": ["远端仓库", "deploy/site.env", "Docker daemon", "控制面执行介质"],
      "outputs": ["deploy/.env", "host firewall ingress 规则", "运行态权限修复", "basic gate 结果", "compose 部署结果", "full test 结果"],
      "failureBoundary": "任一步失败时停在失败阶段，按 summary/status.env 的 resume 命令恢复。",
      "steps": [
        {"id": "prepare_control_plane_medium", "actor": "deploy-user", "requiresRoot": false},
        {"id": "one_click_config", "actor": "deploy-user", "requiresRoot": false},
        {"id": "apply_ingress_boundary_rules", "actor": "root-via-sudo", "requiresRoot": true},
        {"id": "fix_permissions", "actor": "root-via-sudo", "requiresRoot": true},
        {"id": "one_click_test_basic", "actor": "deploy-user", "requiresRoot": false},
        {"id": "one_click_deploy", "actor": "deploy-user", "requiresRoot": false},
        {"id": "one_click_test_full", "actor": "deploy-user", "requiresRoot": false}
      ]
    }
JSON
}

emit_plan_json() {
  local first_stage=1
  cat <<JSON
{
  "kind": "openclaw_remote_first_install_plan",
  "schemaVersion": 1,
  "apply": $(json_bool "$APPLY"),
  "host": $(json_string "$HOST"),
  "repoDir": $(json_string "$REPO_DIR"),
  "deployUser": $(json_string "$DEPLOY_USER"),
  "branch": $(json_string "$BRANCH"),
  "gitUrlProvided": $(json_bool "$([[ -n "$GIT_URL" ]] && printf '1' || printf '0')"),
  "bundlePathProvided": $(json_bool "$([[ -n "$BUNDLE_PATH" ]] && printf '1' || printf '0')"),
  "networkProfile": $(json_string "$NETWORK_PROFILE"),
  "controlPlaneProfile": $(json_string "$CONTROL_PLANE_PROFILE"),
  "sshPort": $(json_string "$SSH_PORT"),
  "clientCidrs": $(json_cidr_array),
  "selectedStages": $(json_string_array $([[ "$RUN_PREFLIGHT" == '1' ]] && printf 'preflight ') $([[ "$RUN_STAGE_BUNDLE" == '1' ]] && printf 'stage_bundle ') $([[ "$RUN_PREPARE_REPO" == '1' ]] && printf 'prepare_repo ') $([[ "$RUN_CONFIGURE_BASE" == '1' ]] && printf 'configure_base ') $([[ "$RUN_DEPLOY" == '1' ]] && printf 'deploy ')),
  "stages": [
JSON
  if [[ "$RUN_PREFLIGHT" == '1' ]]; then
    [[ "$first_stage" == '1' ]] || printf ',\n'
    first_stage=0
    emit_plan_preflight_stage
  fi
  if [[ "$RUN_STAGE_BUNDLE" == '1' ]]; then
    [[ "$first_stage" == '1' ]] || printf ',\n'
    first_stage=0
    emit_plan_stage_bundle_stage
  fi
  if [[ "$RUN_PREPARE_REPO" == '1' ]]; then
    [[ "$first_stage" == '1' ]] || printf ',\n'
    first_stage=0
    emit_plan_prepare_repo_stage
  fi
  if [[ "$RUN_CONFIGURE_BASE" == '1' ]]; then
    [[ "$first_stage" == '1' ]] || printf ',\n'
    first_stage=0
    emit_plan_configure_base_stage
  fi
  if [[ "$RUN_DEPLOY" == '1' ]]; then
    [[ "$first_stage" == '1' ]] || printf ',\n'
    first_stage=0
    emit_plan_deploy_stage
  fi
  cat <<'JSON'
  ]
}
JSON
}

remote_sh() {
  local script="$1"
  if [[ "$APPLY" == '1' ]]; then
    ssh_base "bash -s" <<< "$script"
  else
    log "[DRY-RUN][remote $HOST]"
    printf '%s\n' "$script" | tee -a "$LOG_PATH"
  fi
}

remote_readonly() {
  local script="$1"
  ssh_base "bash -s" <<< "$script"
}

ensure_host() {
  [[ -n "$HOST" ]] || fail '必须提供 --host <ssh-target>'
}

run_preflight() {
  ensure_host
  write_status preflight running
  log "[STEP] preflight"
  remote_readonly "$(cat <<EOF
set -euo pipefail
echo "[remote] ssh ok: \$(hostname -f 2>/dev/null || hostname)"
if sudo -n true >/dev/null 2>&1; then echo "[remote] sudo_nopass=ok"; else echo "[remote][FAIL] sudo -n 不可用"; exit 31; fi
if [[ -f /etc/os-release ]]; then . /etc/os-release; echo "[remote] os=\${PRETTY_NAME:-unknown}"; else echo "[remote] os=unknown"; fi
command -v docker >/dev/null 2>&1 && docker version --format '[remote] docker={{.Server.Version}}' || { echo "[remote][FAIL] docker 不可用"; exit 32; }
docker compose version || { echo "[remote][FAIL] docker compose 不可用"; exit 33; }
if command -v ss >/dev/null 2>&1; then
  occupied_ports="\$(ss -H -ltn '( sport = :80 or sport = :443 )' 2>/dev/null || true)"
else
  echo "[remote][FAIL] 缺少 ss，无法确认 80/443 是否空闲"; exit 34
fi
if [[ -n "\$occupied_ports" ]]; then
  echo "[remote][FAIL] 80/443 端口已被占用，远程首装不能继续："
  printf '%s\n' "\$occupied_ports"
  exit 34
fi
echo "[remote] ports_80_443=free"
df -h "$REPO_DIR" 2>/dev/null || df -h /opt 2>/dev/null || df -h /
if [[ -e "$REPO_DIR" ]]; then echo "[remote][FAIL] repo_dir 已存在：$REPO_DIR"; exit 35; else echo "[remote] repo_dir_absent=$REPO_DIR"; fi
existing_openclaw_containers="\$(docker ps -a --format '{{.Names}}' | grep -E '^openclaw-' || true)"
if [[ -n "\$existing_openclaw_containers" ]]; then
  echo "[remote][FAIL] 已存在 OpenClaw 容器，远程首装不能覆盖："
  printf '%s\n' "\$existing_openclaw_containers"
  exit 36
fi
echo "[remote] openclaw_containers=absent"
EOF
)"
  write_status preflight done
  log "[OK] preflight"
}

create_source_bundle() {
  local bundle="$STATE_DIR/openclaw-source-$TS.tar.gz"
  if [[ -n "$BUNDLE_PATH" ]]; then
    printf '%s\n' "$BUNDLE_PATH"
    return 0
  fi
  (
    cd "$ROOT_DIR"
    tar \
      --exclude='.git' \
      --exclude='state' \
      --exclude='deploy/.env' \
      --exclude='deploy/site.env' \
      --exclude='deploy/targets.d/*.env' \
      --exclude='agent/extensions/*/deploy/extension.env' \
      --exclude='*.secret' \
      --exclude='*.pem' \
      -czf "$bundle" .
  )
  printf '%s\n' "$bundle"
}

run_stage_bundle() {
  ensure_host
  write_status stage-bundle running
  log "[STEP] stage-bundle"
  if [[ -n "$GIT_URL" ]]; then
    log "[INFO] 使用 git URL：$GIT_URL（不传输本地 state/deploy env/secret）"
    write_status stage-bundle done
    log "[OK] stage-bundle"
    return 0
  fi
  local bundle=''
  if [[ "$APPLY" != '1' && -z "$BUNDLE_PATH" ]]; then
    log "[DRY-RUN] 将临时打包当前仓库并传输到 $HOST:/tmp/openclaw-remote-first-install/openclaw-source.tar.gz（dry-run 不生成本地 bundle）"
    write_status stage-bundle done
    log "[OK] stage-bundle"
    return 0
  fi
  bundle="$(create_source_bundle)"
  if [[ "$APPLY" == '1' ]]; then
    ssh_base "mkdir -p /tmp/openclaw-remote-first-install"
    scp_base "$bundle" "$HOST:/tmp/openclaw-remote-first-install/openclaw-source.tar.gz"
    log "[INFO] 已传输 bundle：/tmp/openclaw-remote-first-install/openclaw-source.tar.gz"
  else
    log "[DRY-RUN] 将传输 bundle：$bundle -> $HOST:/tmp/openclaw-remote-first-install/openclaw-source.tar.gz"
  fi
  write_status stage-bundle done
  log "[OK] stage-bundle"
}

run_prepare_repo() {
  ensure_host
  write_status prepare-repo running
  log "[STEP] prepare-repo"
if [[ -n "$GIT_URL" ]]; then
    remote_sh "$(cat <<EOF
set -euo pipefail
sudo mkdir -p "$(dirname "$REPO_DIR")"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  sudo git clone --branch "$BRANCH" "$GIT_URL" "$REPO_DIR"
else
  sudo git -C "$REPO_DIR" fetch --all --prune
  sudo git -C "$REPO_DIR" checkout "$BRANCH"
  sudo git -C "$REPO_DIR" pull --ff-only
fi
cd "$REPO_DIR"
sudo bash ./scripts/setup/prepare_deploy_user.sh --user "$DEPLOY_USER" --repo-dir "$REPO_DIR"
EOF
)"
  else
    remote_sh "$(cat <<EOF
set -euo pipefail
sudo mkdir -p "$REPO_DIR"
sudo tar -xzf /tmp/openclaw-remote-first-install/openclaw-source.tar.gz -C "$REPO_DIR"
cd "$REPO_DIR"
sudo bash ./scripts/setup/prepare_deploy_user.sh --user "$DEPLOY_USER" --repo-dir "$REPO_DIR"
EOF
)"
  fi
  write_status prepare-repo done
  log "[OK] prepare-repo"
}

run_configure_base() {
  ensure_host
  write_status configure-base running
  log "[STEP] configure-base"
  [[ -n "$TLS_CN" ]] || log "[WARN] 未提供 --tls-cn；site.env 会保留模板中的必填占位。"
  remote_sh "$(cat <<EOF
set -euo pipefail
cd "$REPO_DIR"
sudo -u "$DEPLOY_USER" bash ./scripts/setup/apply_site_env_values.sh --init-from-example \
  --set OPENCLAW_CONTROL_PLANE_PROFILE="$CONTROL_PLANE_PROFILE" \
  ${TLS_CN:+--set OPENCLAW_TLS_CN="$TLS_CN"} \
  ${LISTEN_IP:+--set OPENCLAW_INGRESS_LISTEN_IP="$LISTEN_IP"} \
  ${CLIENT_CIDR:+--set OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS="$CLIENT_CIDR"}
EOF
)"
  write_status configure-base done
  log "[OK] configure-base"
}

run_deploy() {
  ensure_host
  write_status deploy running
  log "[STEP] deploy"
  remote_sh "$(cat <<EOF
set -euo pipefail
cd "$REPO_DIR"
sudo -u "$DEPLOY_USER" bash ./scripts/setup/prepare_control_plane_medium.sh
sudo -u "$DEPLOY_USER" bash ./scripts/setup/one_click_config.sh
sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env
sudo bash ./scripts/setup/fix_permissions.sh
sudo -u "$DEPLOY_USER" bash ./scripts/setup/one_click_test_basic.sh
sudo -u "$DEPLOY_USER" bash ./scripts/setup/one_click_deploy.sh
sudo -u "$DEPLOY_USER" bash ./scripts/setup/one_click_test_full.sh
EOF
)"
  write_status deploy done
  log "[OK] deploy"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) [[ $# -ge 2 ]] || fail '--host 缺少参数'; HOST="$2"; shift 2 ;;
    --repo-dir) [[ $# -ge 2 ]] || fail '--repo-dir 缺少参数'; REPO_DIR="$2"; shift 2 ;;
    --deploy-user) [[ $# -ge 2 ]] || fail '--deploy-user 缺少参数'; DEPLOY_USER="$2"; shift 2 ;;
    --git-url) [[ $# -ge 2 ]] || fail '--git-url 缺少参数'; GIT_URL="$2"; shift 2 ;;
    --bundle) [[ $# -ge 2 ]] || fail '--bundle 缺少路径参数'; BUNDLE_PATH="$2"; shift 2 ;;
    --branch) [[ $# -ge 2 ]] || fail '--branch 缺少参数'; BRANCH="$2"; shift 2 ;;
    --tls-cn) [[ $# -ge 2 ]] || fail '--tls-cn 缺少参数'; TLS_CN="$2"; shift 2 ;;
    --listen-ip) [[ $# -ge 2 ]] || fail '--listen-ip 缺少参数'; LISTEN_IP="$2"; shift 2 ;;
    --client-cidr) [[ $# -ge 2 ]] || fail '--client-cidr 缺少参数'; CLIENT_CIDR="$2"; shift 2 ;;
    --control-plane-profile) [[ $# -ge 2 ]] || fail '--control-plane-profile 缺少参数'; CONTROL_PLANE_PROFILE="$2"; shift 2 ;;
    --network-profile) [[ $# -ge 2 ]] || fail '--network-profile 缺少参数'; NETWORK_PROFILE="$2"; shift 2 ;;
    --ssh-port) [[ $# -ge 2 ]] || fail '--ssh-port 缺少参数'; SSH_PORT="$2"; shift 2 ;;
    --ssh-option) [[ $# -ge 2 ]] || fail '--ssh-option 缺少参数'; SSH_OPTS+=("$2"); shift 2 ;;
    --apply) APPLY=1; shift ;;
    --plan-json) PLAN_JSON=1; shift ;;
    --preflight) RUN_PREFLIGHT=1; shift ;;
    --stage-bundle) RUN_STAGE_BUNDLE=1; shift ;;
    --prepare-repo) RUN_PREPARE_REPO=1; shift ;;
    --configure-base) RUN_CONFIGURE_BASE=1; shift ;;
    --deploy) RUN_DEPLOY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "未知参数：$1" ;;
  esac
done

ensure_host
validate_inputs
if [[ "$RUN_PREFLIGHT" == '0' && "$RUN_STAGE_BUNDLE" == '0' && "$RUN_PREPARE_REPO" == '0' && "$RUN_CONFIGURE_BASE" == '0' && "$RUN_DEPLOY" == '0' ]]; then
  RUN_PREFLIGHT=1
fi

if [[ "$PLAN_JSON" == '1' ]]; then
  emit_plan_json
  exit 0
fi

write_status init running
write_summary running ''
log "[INFO] remote_first_install log=$LOG_PATH"
log "[INFO] summary=$SUMMARY_PATH"
log "[INFO] status=$STATUS_PATH"
[[ "$APPLY" == '1' ]] || log "[INFO] 当前为 dry-run；追加 --apply 才执行远端写入。"

[[ "$RUN_PREFLIGHT" == '1' ]] && run_preflight
[[ "$RUN_STAGE_BUNDLE" == '1' ]] && run_stage_bundle
[[ "$RUN_PREPARE_REPO" == '1' ]] && run_prepare_repo
[[ "$RUN_CONFIGURE_BASE" == '1' ]] && run_configure_base
[[ "$RUN_DEPLOY" == '1' ]] && run_deploy

write_status complete success
write_summary success ''
log "[OK] remote_first_install 完成；summary=$SUMMARY_PATH"
