#!/usr/bin/env bash
# 用途：远程清理 OpenClaw 历史痕迹；默认 dry-run，显式 --apply 后才删除带项目证据的对象。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

HOST=""
REPO_DIR="/opt/openclaw/clawctl"
DEPLOY_USER="openclaw"
APPLY=0
SSH_PORT=""
SSH_OPTS=()

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/cleanup_remote_openclaw.sh --host <ssh-target>
  bash ./scripts/setup/cleanup_remote_openclaw.sh --host <ssh-target> --apply

说明：
  - 默认 dry-run，只读列出远端 OpenClaw 容器、网络、卷、项目容器关联镜像、目录、用户与 ingress 边界规则。
  - 显式 --apply 后才删除；不执行全局 Docker 清理命令，不按通用镜像名扫删，不保留备份。
  - 仓库目录、部署用户与同名用户组必须具备 OpenClaw 证据才会删除，避免误删普通目录或既有系统用户。
  - 镜像删除只针对 OpenClaw 容器曾使用的 image id；若仍被非项目容器占用，Docker 会拒绝删除并在未清理清单中显示。
  - 目录删除只覆盖 --repo-dir、/tmp/openclaw-source.tar、/tmp/openclaw-remote-first-install；仅当 /opt/openclaw 已空时才删除父目录。

参数：
  --host <ssh-target>   SSH 目标，例如 user@host
  --repo-dir <path>     远端仓库目录，默认 /opt/openclaw/clawctl
  --deploy-user <user>  固定部署用户，默认 openclaw
  --ssh-port <port>     SSH 端口，默认使用 ssh 客户端默认值 22
  --ssh-option <opt>    追加 ssh option，例如 -oStrictHostKeyChecking=accept-new
  --apply              执行删除；缺省只输出计划
USAGE
}

fail() {
  echo "[cleanup_remote_openclaw][FAIL] $*" >&2
  exit 2
}

shell_quote() {
  printf '%q' "$1"
}

validate_simple_value() {
  local label="$1"
  local value="$2"
  local pattern="$3"
  [[ -z "$value" || "$value" =~ $pattern ]] || fail "$label 包含不支持字符：$value"
}

validate_cleanup_repo_dir() {
  case "$REPO_DIR" in
    /|/root|/home|/opt|/usr|/var|/etc|*/../*|*/..|*/.|*//*) fail "--repo-dir 不是可清理的 OpenClaw 仓库路径：$REPO_DIR" ;;
  esac
  [[ "$REPO_DIR" == *openclaw* || "$REPO_DIR" == *clawctl* ]] || fail "--repo-dir 必须包含 openclaw 或 clawctl 路径证据：$REPO_DIR"
}

validate_cleanup_deploy_user() {
  case "$DEPLOY_USER" in
    root|daemon|bin|sys|sync|games|man|lp|mail|news|uucp|proxy|www-data|backup|list|irc|_apt|nobody)
      fail "--deploy-user 指向系统保留用户，拒绝纳入清理计划：$DEPLOY_USER"
      ;;
  esac
}

validate_ssh_port() {
  [[ -z "$SSH_PORT" ]] && return 0
  [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || fail "--ssh-port 必须是 1-65535 的整数：$SSH_PORT"
  local port_num=$((10#$SSH_PORT))
  (( port_num >= 1 && port_num <= 65535 )) || fail "--ssh-port 必须是 1-65535 的整数：$SSH_PORT"
}

validate_inputs() {
  [[ -n "$HOST" ]] || fail '必须提供 --host <ssh-target>'
  validate_simple_value '--host' "$HOST" '^[A-Za-z0-9_.:@-]+$'
  validate_simple_value '--repo-dir' "$REPO_DIR" '^/[A-Za-z0-9_./-]+$'
  validate_simple_value '--deploy-user' "$DEPLOY_USER" '^[a-z_][a-z0-9_-]{0,31}$'
  validate_ssh_port
  validate_cleanup_repo_dir
  validate_cleanup_deploy_user
}

ssh_remote_cleanup() {
  local port_args=()
  [[ -n "$SSH_PORT" ]] && port_args=(-p "$SSH_PORT")
  ssh "${port_args[@]}" "${SSH_OPTS[@]}" "$HOST" "bash -s" <<< "$(remote_cleanup_script)"
}

remote_cleanup_script() {
  cat <<EOF
set -euo pipefail
APPLY="$APPLY"
REPO_DIR="$(shell_quote "$REPO_DIR")"
DEPLOY_USER="$(shell_quote "$DEPLOY_USER")"

note() { printf '%s\n' "\$*"; }
plan() { note "[PLAN] \$*"; }
applied() { note "[APPLY] \$*"; }
warn() { note "[WARN] \$*"; }

run_apply() {
  local label="\$1"
  shift
  if [[ "\$APPLY" == '1' ]]; then
    applied "\$label"
    "\$@" || warn "未清理：\$label"
  else
    plan "\$label"
  fi
}

docker_available() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

collect_openclaw_containers() {
  docker ps -a --format '{{.ID}}|{{.Image}}|{{.Names}}|{{.Labels}}' 2>/dev/null \
    | awk -F'|' '\$3 ~ /^openclaw-/ || \$4 ~ /(openclaw|clawctl)/ { print }'
}

collect_labeled_objects() {
  local kind="\$1"
  docker "\$kind" ls --format '{{.Name}}|{{.Labels}}' 2>/dev/null \
    | awk -F'|' '\$1 ~ /(openclaw|clawctl)/ || \$2 ~ /(openclaw|clawctl)/ { print \$1 }'
}

openclaw_repo_dir_has_evidence() {
  local dir="\$1"
  [[ -d "\$dir" ]] || return 1
  [[ -f "\$dir/scripts/setup/one_click_deploy.sh" && -f "\$dir/deploy/docker-compose.yml" ]] && return 0
  [[ -f "\$dir/deploy/.env" ]] && grep -q '^OPENCLAW_' "\$dir/deploy/.env" 2>/dev/null && return 0
  [[ -f "\$dir/.git/config" ]] && grep -Eiq '(openclaw|clawctl)' "\$dir/.git/config" 2>/dev/null && return 0
  return 1
}

deploy_user_has_openclaw_evidence() {
  local user="\$1"
  local passwd_line='' home_dir='' marker_path='' repo_owner=''
  passwd_line="\$(getent passwd "\$user" 2>/dev/null || true)"
  home_dir="\$(printf '%s' "\$passwd_line" | awk -F: '{print \$6}')"
  marker_path="\$home_dir/.openclaw/deploy-user.marker"
  if [[ -n "\$home_dir" && -f "\$marker_path" ]]; then
    grep -q '^created_by_openclaw=1$' "\$marker_path" 2>/dev/null && return 0
    return 1
  fi
  if openclaw_repo_dir_has_evidence "\$REPO_DIR"; then
    repo_owner="\$(stat -c '%U' "\$REPO_DIR" 2>/dev/null || true)"
    [[ "\$user" == 'openclaw' && "\$repo_owner" == "\$user" ]] && return 0
  fi
  return 1
}

deploy_group_has_openclaw_evidence() {
  local group="\$1"
  local repo_group=''
  if openclaw_repo_dir_has_evidence "\$REPO_DIR"; then
    repo_group="\$(stat -c '%G' "\$REPO_DIR" 2>/dev/null || true)"
    [[ "\$repo_group" == "\$group" ]] && return 0
  fi
  return 1
}

cleanup_docker_objects() {
  local row='' container_id='' image_name='' container_name='' image_id=''
  local image_ids_file=''
  if ! docker_available; then
    warn 'Docker CLI 或 daemon 不可用，跳过容器、网络、卷、镜像清理计划。'
    return 0
  fi
  image_ids_file="\$(mktemp)"
  while IFS='|' read -r container_id image_name container_name _labels; do
    [[ -n "\$container_id" ]] || continue
    image_id="\$(docker inspect --format '{{.Image}}' "\$container_id" 2>/dev/null || true)"
    [[ -n "\$image_id" ]] && printf '%s\n' "\$image_id" >> "\$image_ids_file"
    run_apply "删除 OpenClaw 容器 \$container_name (\$container_id, image=\$image_name)" docker rm -f "\$container_id"
  done < <(collect_openclaw_containers)
  while IFS= read -r network_name; do
    [[ -n "\$network_name" ]] || continue
    run_apply "删除 OpenClaw 网络 \$network_name" docker network rm "\$network_name"
  done < <(collect_labeled_objects network)
  while IFS= read -r volume_name; do
    [[ -n "\$volume_name" ]] || continue
    run_apply "删除 OpenClaw 卷 \$volume_name" docker volume rm "\$volume_name"
  done < <(collect_labeled_objects volume)
  sort -u "\$image_ids_file" | while IFS= read -r image_id; do
    [[ -n "\$image_id" ]] || continue
    run_apply "删除 OpenClaw 容器关联镜像 \$image_id" docker image rm "\$image_id"
  done
  rm -f "\$image_ids_file"
}

cleanup_filesystem() {
  if [[ ! -e "\$REPO_DIR" ]]; then
    note "[SKIP] 仓库目录不存在：\$REPO_DIR"
  elif openclaw_repo_dir_has_evidence "\$REPO_DIR"; then
    run_apply "删除具备 OpenClaw 证据的仓库目录 \$REPO_DIR" sudo rm -rf -- "\$REPO_DIR"
  else
    warn "未清理：仓库目录缺少 OpenClaw 证据，拒绝递归删除 \$REPO_DIR"
  fi
  if [[ -e /tmp/openclaw-remote-first-install ]]; then
    run_apply "删除远程源码临时目录 /tmp/openclaw-remote-first-install" sudo rm -rf -- /tmp/openclaw-remote-first-install
  else
    note "[SKIP] 远程源码临时目录不存在：/tmp/openclaw-remote-first-install"
  fi
  if [[ -e /tmp/openclaw-source.tar ]]; then
    run_apply "删除远程源码临时包 /tmp/openclaw-source.tar" sudo rm -f -- /tmp/openclaw-source.tar
  else
    note "[SKIP] 远程源码临时包不存在：/tmp/openclaw-source.tar"
  fi
  if [[ "\$REPO_DIR" == /opt/openclaw/clawctl ]]; then
    if [[ ! -d /opt/openclaw ]]; then
      note '[SKIP] 父目录不存在：/opt/openclaw'
    elif [[ "\$APPLY" == '1' ]]; then
      sudo rmdir /opt/openclaw >/dev/null 2>&1 || warn '未清理：/opt/openclaw 非空'
    else
      plan '若 /opt/openclaw 在仓库目录删除后为空，则删除该父目录'
    fi
  fi
}

cleanup_user() {
  local user_evidence=0
  if getent passwd "\$DEPLOY_USER" >/dev/null 2>&1; then
    if deploy_user_has_openclaw_evidence "\$DEPLOY_USER"; then
      user_evidence=1
      run_apply "删除具备 OpenClaw 证据的部署用户 \$DEPLOY_USER 及其 home" sudo userdel -r "\$DEPLOY_USER"
    else
      warn "未清理：部署用户缺少 OpenClaw 证据，拒绝删除 \$DEPLOY_USER"
      return 0
    fi
  else
    note "[SKIP] 部署用户不存在：\$DEPLOY_USER"
  fi
  if getent group "\$DEPLOY_USER" >/dev/null 2>&1; then
    if [[ "\$user_evidence" == '1' ]] || deploy_group_has_openclaw_evidence "\$DEPLOY_USER"; then
      run_apply "删除具备 OpenClaw 证据的同名部署用户组 \$DEPLOY_USER" sudo groupdel "\$DEPLOY_USER"
    else
      warn "未清理：部署用户组缺少 OpenClaw 证据，拒绝删除 \$DEPLOY_USER"
    fi
  fi
}

cleanup_iptables_rules() {
  local cmd='iptables'
  local line='' parts=()
  command -v "\$cmd" >/dev/null 2>&1 || { warn '缺少 iptables，跳过 OPENCLAW_INGRESS_BOUNDARY 规则清理。'; return 0; }
  while IFS= read -r line; do
    [[ -n "\$line" ]] || continue
    if [[ "\$APPLY" == '1' ]]; then
      read -r -a parts <<< "\$line"
      parts[0]='-D'
      applied "删除 iptables 规则：\$line"
      sudo "\$cmd" "\${parts[@]}" >/dev/null 2>&1 || warn "未清理：\$line"
    else
      plan "删除 iptables 规则：\$line"
    fi
  done < <("\$cmd" -S DOCKER-USER 2>/dev/null | awk '/OPENCLAW_INGRESS_BOUNDARY/ {print}')
}

cleanup_nft_rules() {
  local family='' table_name='' chain_name='' handle='' raw=''
  if command -v nft >/dev/null 2>&1; then
    while IFS='|' read -r family table_name chain_name handle raw; do
      [[ -n "\$family" && -n "\$table_name" && -n "\$chain_name" && -n "\$handle" ]] || continue
      if [[ "\$APPLY" == '1' ]]; then
        applied "删除 nft 规则：\$raw"
        sudo nft delete rule "\$family" "\$table_name" "\$chain_name" handle "\$handle" >/dev/null 2>&1 || warn "未清理 nft 规则：\$raw"
      else
        plan "删除 nft 规则：\$raw"
      fi
    done < <(
      nft -a list ruleset 2>/dev/null | awk '
        \$1 == "table" { family = \$2; table_name = \$3; gsub(/[{}]/, "", table_name) }
        \$1 == "chain" { chain_name = \$2; gsub(/[{}]/, "", chain_name) }
        /OPENCLAW_INGRESS_BOUNDARY/ && /# handle / {
          handle = \$NF
          print family "|" table_name "|" chain_name "|" handle "|" \$0
        }
      '
    )
  fi
}

note "[INFO] cleanup_remote_openclaw target=\$(hostname -f 2>/dev/null || hostname) apply=\$APPLY repo_dir=\$REPO_DIR deploy_user=\$DEPLOY_USER"
cleanup_docker_objects
cleanup_iptables_rules
cleanup_nft_rules
cleanup_user
cleanup_filesystem
note "[OK] cleanup_remote_openclaw 完成；apply=\$APPLY"
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) [[ $# -ge 2 ]] || fail '--host 缺少参数'; HOST="$2"; shift 2 ;;
    --repo-dir) [[ $# -ge 2 ]] || fail '--repo-dir 缺少参数'; REPO_DIR="$2"; shift 2 ;;
    --deploy-user) [[ $# -ge 2 ]] || fail '--deploy-user 缺少参数'; DEPLOY_USER="$2"; shift 2 ;;
    --ssh-port) [[ $# -ge 2 ]] || fail '--ssh-port 缺少参数'; SSH_PORT="$2"; shift 2 ;;
    --ssh-option) [[ $# -ge 2 ]] || fail '--ssh-option 缺少参数'; SSH_OPTS+=("$2"); shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "未知参数：$1" ;;
  esac
done

validate_inputs
if [[ "$APPLY" == '1' ]]; then
  echo "[cleanup_remote_openclaw][WARN] 即将删除远端 OpenClaw 证据对象；非项目镜像与无证据对象不会删除。"
else
  echo "[cleanup_remote_openclaw][INFO] 当前为 dry-run；追加 --apply 才执行删除。"
fi

ssh_remote_cleanup
