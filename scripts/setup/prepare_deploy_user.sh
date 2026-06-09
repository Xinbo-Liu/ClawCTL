#!/usr/bin/env bash
# 用途：在 root 侧创建固定部署用户、补齐 docker 组成员关系，并把当前仓库交接给部署用户。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
DEPLOY_USER="openclaw"
REPO_DIR="$ROOT_DIR"
CHOWN_REPO=1
VERIFY_DOCKER=1
DEPLOY_USER_CREATED=0

usage() {
  cat <<'USAGE'
用法：
  sudo bash ./scripts/setup/prepare_deploy_user.sh [--user <name>] [--repo-dir <path>] [--skip-repo-chown] [--skip-docker-check]

说明：
  - 本脚本只负责 root 阶段到固定部署用户阶段的交接，不会自动切换当前 shell。
  - 若用户不存在，会创建带 home 目录的普通 shell 用户。
  - --user 只接受小写 Linux 普通用户名格式，避免 root 脚本参数产生歧义。
  - 若 docker 组存在，会把部署用户加入 docker 组。
  - 默认把仓库目录递归交接给部署用户；仓库必须包含 OpenClaw 部署入口文件，脚本会拒绝根目录等危险路径。
  - 会在部署用户 home 下写入 `.openclaw/deploy-user.marker`，并记录用户是否由 OpenClaw 创建，供远程清理入口判定能否删除该用户。
  - 完成后按输出的 sudo -iu/su 命令切换到部署用户，再执行 one_click_config、basic gate 与 one_click_deploy。
  - 若当前保留 root SSH 会话，可按输出的 runuser 命令以部署用户执行后续非特权步骤；脚本不会改变当前 shell 身份。
  - 本脚本不会复制 root 或当前登录用户的 SSH 公钥；若需要直接 SSH 登录部署用户，请按目标机安全策略显式配置该用户 authorized_keys。
USAGE
}

note() { printf '[prepare_deploy_user][INFO] %s\n' "$*"; }
warn() { printf '[prepare_deploy_user][WARN] %s\n' "$*"; }
fail() {
  printf '[prepare_deploy_user][FAIL] %s\n' "$*" >&2
  exit "${2:-2}"
}

require_root() {
  [[ "$(id -u)" == "0" ]] || fail '当前脚本需要 root 权限；请使用 sudo bash ./scripts/setup/prepare_deploy_user.sh ...' 30
}

validate_deploy_user() {
  local user="$1"
  [[ -n "$user" ]] || fail '--user 不能为空'
  if [[ ! "$user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
    fail "--user 只接受 1-32 位 Linux 普通用户名格式：小写字母或下划线开头，后续只能包含小写字母、数字、下划线或连字符。当前值：$user"
  fi
}

shell_quote() {
  printf '%q' "$1"
}

resolve_repo_dir() {
  local input="$1"
  [[ -n "$input" ]] || fail '--repo-dir 不能为空'
  [[ -d "$input" ]] || fail "仓库目录不存在：$input"
  cd "$input" && pwd -P
}

assert_openclaw_repo_dir() {
  local repo_dir="$1"
  case "$repo_dir" in
    /|/root|/home|/opt|/usr|/var|/etc)
      fail "拒绝把过宽目录作为仓库交接目标：$repo_dir"
      ;;
  esac
  [[ -f "$repo_dir/scripts/setup/one_click_deploy.sh" ]] || fail "仓库目录缺少 scripts/setup/one_click_deploy.sh：$repo_dir"
  [[ -f "$repo_dir/deploy/docker-compose.yml" ]] || fail "仓库目录缺少 deploy/docker-compose.yml：$repo_dir"
}

ensure_deploy_user() {
  local user="$1"
  if id "$user" >/dev/null 2>&1; then
    note "部署用户已存在：$user"
    DEPLOY_USER_CREATED=0
  else
    useradd -m -s /bin/bash "$user"
    DEPLOY_USER_CREATED=1
    note "已创建部署用户：$user"
  fi
  if getent group docker >/dev/null 2>&1; then
    usermod -aG docker "$user"
    note "已确保 $user 属于 docker 组"
  else
    warn '未检测到 docker 组；请先完成 prepare_docker_host.sh --install-docker 或手工修复 Docker 安装。'
  fi
}

mark_deploy_user_evidence() {
  local user="$1"
  local repo_dir="$2"
  local home_dir='' group='' marker_dir='' marker_path='' created_flag=''
  home_dir="$(getent passwd "$user" | awk -F: '{print $6}')"
  [[ -n "$home_dir" && "$home_dir" == /* && "$home_dir" != "/" ]] || fail "无法解析部署用户 home，拒绝写入 OpenClaw 用户证据：$user"
  group="$(deploy_user_group "$user")"
  marker_dir="$home_dir/.openclaw"
  marker_path="$marker_dir/deploy-user.marker"
  created_flag="$DEPLOY_USER_CREATED"
  if [[ "$created_flag" != "1" && -f "$marker_path" ]] && grep -q '^created_by_openclaw=1$' "$marker_path" 2>/dev/null; then
    created_flag=1
  fi
  mkdir -p "$marker_dir"
  cat > "$marker_path" <<EOF
kind=openclaw_deploy_user
user=$user
repo_dir=$repo_dir
created_by_openclaw=$created_flag
created_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  chown -R "$user:$group" "$marker_dir"
  chmod 700 "$marker_dir"
  chmod 600 "$marker_path"
  note "已写入 OpenClaw 部署用户证据：$marker_path"
}

deploy_user_group() {
  local user="$1"
  id -gn "$user"
}

chown_repo_to_deploy_user() {
  local user="$1"
  local repo_dir="$2"
  local group=""
  group="$(deploy_user_group "$user")"
  chown -R "$user:$group" "$repo_dir"
  note "已把仓库目录交接给 $user:$group：$repo_dir"
}

verify_deploy_user_docker_access() {
  local user="$1"
  if ! command -v docker >/dev/null 2>&1; then
    warn '未检测到 docker 命令，跳过部署用户 docker 访问验证。'
    return 0
  fi
  if su - "$user" -c 'docker info >/dev/null 2>&1'; then
    note "部署用户可访问 Docker daemon：$user"
  else
    warn "部署用户暂不能访问 Docker daemon；若刚加入 docker 组，请重新登录该用户后再执行 docker info。"
  fi
}

emit_handoff() {
  local user="$1"
  local repo_dir="$2"
  local quoted_repo=""
  quoted_repo="$(shell_quote "$repo_dir")"
  cat <<EOF
[prepare_deploy_user][NEXT]
runuser -u $user -- bash -lc "cd $quoted_repo && id && docker info"
runuser -u $user -- bash -lc "cd $quoted_repo && bash ./scripts/setup/prepare_control_plane_medium.sh && bash ./scripts/setup/one_click_config.sh"
sudo -iu $user
# 若当前 shell 是 root 且目标机没有 sudo，可改用：su - $user
cd $quoted_repo
id
docker info
bash ./scripts/setup/prepare_control_plane_medium.sh
bash ./scripts/setup/one_click_config.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      [[ $# -ge 2 ]] || fail '--user 缺少参数'
      DEPLOY_USER="$2"
      shift 2
      ;;
    --repo-dir)
      [[ $# -ge 2 ]] || fail '--repo-dir 缺少参数'
      REPO_DIR="$2"
      shift 2
      ;;
    --skip-repo-chown)
      CHOWN_REPO=0
      shift
      ;;
    --skip-docker-check)
      VERIFY_DOCKER=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1"
      ;;
  esac
done

require_root
validate_deploy_user "$DEPLOY_USER"
REPO_DIR="$(resolve_repo_dir "$REPO_DIR")"
assert_openclaw_repo_dir "$REPO_DIR"
ensure_deploy_user "$DEPLOY_USER"
mark_deploy_user_evidence "$DEPLOY_USER" "$REPO_DIR"
if [[ "$CHOWN_REPO" == "1" ]]; then
  chown_repo_to_deploy_user "$DEPLOY_USER" "$REPO_DIR"
else
  warn "已按要求跳过仓库 chown：$REPO_DIR"
fi
if [[ "$VERIFY_DOCKER" == "1" ]]; then
  verify_deploy_user_docker_access "$DEPLOY_USER"
fi
emit_handoff "$DEPLOY_USER" "$REPO_DIR"
