#!/usr/bin/env bash
# 用途：按统一镜像配置拉取项目所需的外部镜像。
# 规则：
# - 只拉取 deploy/.env（或默认值）中指定的“实际来源 tag”；
# - 外部镜像保持原始引用，不补打官方同义 tag；
# - 本地若已存在目标镜像，则默认跳过；
# - 保留重试、超时与中断恢复能力，适合弱网场景反复执行。

set -euo pipefail
export TZ=Asia/Shanghai

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/lib/deployment_images.sh"
source "$ROOT_DIR/scripts/lib/openclaw_runtime_contract.sh"
source "$ROOT_DIR/scripts/lib/pin_env_shell.sh"
source "$ROOT_DIR/scripts/lib/registry_manifest_probe.sh"

PULL_RETRIES="${PULL_RETRIES:-3}"
PULL_SLEEP_BASE="${PULL_SLEEP_BASE:-4}"
PULL_BACKOFF="${PULL_BACKOFF:-2}"
PULL_TIMEOUT="${PULL_TIMEOUT:-900}"
PULL_GATEWAY_OFFICIAL_TIMEOUT="${PULL_GATEWAY_OFFICIAL_TIMEOUT:-300}"
PULL_KILL_AFTER="${PULL_KILL_AFTER:-10}"
PULL_FORCE="${PULL_FORCE:-0}"
PULL_GATEWAY_CANDIDATE_MODE_RAW="${PULL_GATEWAY_CANDIDATE_MODE:-}"
PULL_CN_GATEWAY_CANDIDATE_FAIL_FAST_LEGACY="${PULL_CN_GATEWAY_CANDIDATE_FAIL_FAST-}"
PULL_STATE_FILE="${PULL_STATE_FILE:-$ROOT_DIR/state/image_pull/pulled_images.txt}"
PULL_RECORD_FILE="${PULL_RECORD_FILE:-$ROOT_DIR/state/image_pull/pull_records.log}"
PULL_GATEWAY_SELECTION_FILE="${PULL_GATEWAY_SELECTION_FILE:-$ROOT_DIR/state/image_pull/gateway_source_selection.json}"
PULL_GATEWAY_CANDIDATE_MODE=""
TARGET_IMAGES=()

CURRENT_PULL_PID=""

fail() {
  echo "[FAIL] $1" >&2
  exit "${2:-1}"
}

require_dir_manageable_or_creatable() {
  local path="$1"
  local label="$2"
  if [[ -d "$path" ]]; then
    [[ -r "$path" && -w "$path" && -x "$path" ]] || fail "$label 缺少读取/写入/执行权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限。" 4
    return 0
  fi
  local parent
  parent="$(dirname "$path")"
  [[ -d "$parent" ]] || fail "$label 的父目录不存在：$parent；当前脚本不会自动补建越级路径。" 4
  [[ -r "$parent" && -w "$parent" && -x "$parent" ]] || fail "$label 的父目录不可写：$parent；当前脚本不会自动提权或 chown，请先修正宿主机权限。" 4
}

require_file_manageable_or_creatable() {
  local path="$1"
  local label="$2"
  if [[ -e "$path" ]]; then
    [[ -f "$path" ]] || fail "$label 不是常规文件：$path" 4
    [[ -r "$path" && -w "$path" ]] || fail "$label 缺少读取/写入权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限。" 4
    return 0
  fi
  require_dir_manageable_or_creatable "$(dirname "$path")" "$label 的父目录"
}

usage() {
  cat <<'USAGE'
用法：
  ./scripts/images/pull_images.sh [--list]

环境变量：
  PULL_RETRIES      单个镜像最大重试次数（默认 3）
  PULL_SLEEP_BASE   首次重试等待秒数（默认 4）
  PULL_BACKOFF      退避倍率（默认 2）
  PULL_TIMEOUT      单次 docker pull 超时秒数（默认 900；0=不限制）
  PULL_GATEWAY_OFFICIAL_TIMEOUT
                    GHCR official Gateway 单次拉取超时秒数（默认 300；0=不限制）
  PULL_KILL_AFTER   中断时 TERM→KILL 等待秒数（默认 10）
  PULL_FORCE        1=即使本地已存在也重新拉取（默认 0）
  PULL_GATEWAY_CANDIDATE_MODE
                    auto-switch|fail-fast|off；默认 auto-switch。CN profile 下 official GHCR 有等值候选时，
                    auto-switch 只改写当前 deploy/.env 的 OPENCLAW_OFFICIAL_GATEWAY_IMAGE，不改 canonical pin。

说明：
  目标镜像来自 source_strategy 声明的部署镜像合同角色；实际 ref 由 deploy/.env 与 pin 真源解析。
  当前脚本不会自动提权；state/image_pull 由镜像脚本运行时创建，状态文件与记录文件由当前用户写入。
USAGE
}

# 拆分 image:tag@digest，供 Gateway source selection 复用统一解析规则。
image_ref_repo_tag_digest() {
  image_env_split_image_ref "$1"
}

# 判断 Docker daemon 是否已配置国内常见 registry mirror。
docker_daemon_has_cn_registry_mirrors() {
  docker info --format '{{range .RegistryConfig.Mirrors}}{{println .}}{{end}}' 2>/dev/null |
    grep -qiE 'daocloud|nju|ustc|aliyun|tencent|huawei|npmmirror'
}

# 解析 Gateway candidate 策略，并把已设置的同义环境变量映射到当前模式。
gateway_candidate_mode() {
  local mode="$PULL_GATEWAY_CANDIDATE_MODE_RAW"
  if [[ -z "$mode" && -n "$PULL_CN_GATEWAY_CANDIDATE_FAIL_FAST_LEGACY" ]]; then
    case "$PULL_CN_GATEWAY_CANDIDATE_FAIL_FAST_LEGACY" in
      0|false|no)
        mode='off'
        ;;
      1|true|yes|auto)
        mode='fail-fast'
        ;;
      *)
        fail "PULL_CN_GATEWAY_CANDIDATE_FAIL_FAST 仅支持 auto/1/0：$PULL_CN_GATEWAY_CANDIDATE_FAIL_FAST_LEGACY" 2
        ;;
    esac
  fi
  [[ -n "$mode" ]] || mode='auto-switch'
  case "$mode" in
    auto-switch|fail-fast|off)
      printf '%s\n' "$mode"
      ;;
    *)
      fail "PULL_GATEWAY_CANDIDATE_MODE 仅支持 auto-switch|fail-fast|off：$mode" 2
      ;;
  esac
}

# 判断当前部署是否处于需要优先使用 candidate source 的网络画像。
gateway_candidate_profile_active() {
  [[ "${OPENCLAW_DEPLOY_NETWORK_PROFILE:-}" == "cn" ]] && return 0
  docker_daemon_has_cn_registry_mirrors
}

# 写出 Gateway source selection 审计记录，记录是否改写当前 deploy env。
write_gateway_source_selection_json() {
  local reason="$1"
  local env_rewritten="$2"
  local official_ref="$3"
  local selected_ref="$4"
  local candidate_ref="$5"
  local digest="$6"
  local candidate_repo="$7"
  local mode="$8"
  local tmp_file=''
  mkdir -p "$(dirname "$PULL_GATEWAY_SELECTION_FILE")"
  tmp_file="$PULL_GATEWAY_SELECTION_FILE.tmp.$$"
  jq -n \
    --arg generatedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg reason "$reason" \
    --arg official "$official_ref" \
    --arg selected "$selected_ref" \
    --arg candidate "$candidate_ref" \
    --arg digest "$digest" \
    --arg candidateRepo "$candidate_repo" \
    --arg mode "$mode" \
    --arg deployEnv "$IMAGE_ENV_DEPLOY_ENV_PATH" \
    --argjson envRewritten "$env_rewritten" \
    '{
      schemaVersion: 1,
      kind: "openclaw_gateway_source_selection",
      generatedAt: $generatedAt,
      mode: $mode,
      reason: $reason,
      deployEnv: $deployEnv,
      official: $official,
      selected: $selected,
      candidate: $candidate,
      candidateRepo: $candidateRepo,
      digest: $digest,
      envRewritten: $envRewritten
    }' > "$tmp_file"
  chmod 600 "$tmp_file"
  mv "$tmp_file" "$PULL_GATEWAY_SELECTION_FILE"
}

# 从 runtime contract 声明的加速仓库中寻找 digest 等值的 candidate repo。
resolve_equal_gateway_candidate_repo() {
  local tag="$1"
  local expected_digest="$2"
  local repo=''
  local digest=''
  openclaw_runtime_contract_load "$ROOT_DIR" >/dev/null || return 1
  while IFS= read -r repo; do
    [[ -n "$repo" ]] || continue
    [[ "$repo" == "$OPENCLAW_RUNTIME_CONTRACT_DEFAULT_OFFICIAL_GATEWAY_IMAGE_REPO" ]] && continue
    digest="$(registry_manifest_probe_resolve_digest "$repo" "$tag" 2>/dev/null || true)"
    [[ "$digest" == "$expected_digest" ]] || continue
    printf '%s\n' "$repo"
    return 0
  done < <(openclaw_runtime_contract_gateway_acceleration_repos)
  return 1
}

# 在 CN profile 下自动选择等值 candidate，并只更新当前 deploy env。
maybe_select_gateway_candidate_source() {
  local mode="$1"
  local image="$OPENCLAW_OFFICIAL_GATEWAY_IMAGE"
  local repo='' tag='' digest='' candidate_repo='' candidate_ref='' default_repo=''
  local profile_active=0
  mapfile -t __gateway_ref_parts < <(image_ref_repo_tag_digest "$image")
  repo="${__gateway_ref_parts[0]}"
  tag="${__gateway_ref_parts[1]}"
  digest="${__gateway_ref_parts[2]}"

  openclaw_runtime_contract_load "$ROOT_DIR" >/dev/null || return 1
  default_repo="$OPENCLAW_RUNTIME_CONTRACT_DEFAULT_OFFICIAL_GATEWAY_IMAGE_REPO"

  if [[ "$mode" == "off" ]]; then
    write_gateway_source_selection_json 'mode_off' false "$default_repo:$tag@$digest" "$image" "" "$digest" "" "$mode"
    return 0
  fi
  if [[ "$image" != "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE" || "$repo" != "$default_repo" ]]; then
    write_gateway_source_selection_json 'selected_source_not_default_official' false "$default_repo:$tag@$digest" "$image" "" "$digest" "" "$mode"
    return 0
  fi
  if gateway_candidate_profile_active; then
    profile_active=1
  fi
  if [[ "$mode" == "auto-switch" && "$profile_active" != "1" ]]; then
    write_gateway_source_selection_json 'non_cn_profile' false "$default_repo:$tag@$digest" "$image" "" "$digest" "" "$mode"
    return 0
  fi

  candidate_repo="$(resolve_equal_gateway_candidate_repo "$tag" "$digest" || true)"
  if [[ -z "$candidate_repo" ]]; then
    write_gateway_source_selection_json 'no_equal_candidate' false "$default_repo:$tag@$digest" "$image" "" "$digest" "" "$mode"
    return 0
  fi
  candidate_ref="$candidate_repo:$tag@$digest"

  if [[ "$mode" == "fail-fast" ]]; then
    write_gateway_source_selection_json 'fail_fast_candidate_available' false "$default_repo:$tag@$digest" "$image" "$candidate_ref" "$digest" "$candidate_repo" "$mode"
    echo "[FAIL] 已确认等值 Gateway candidate source 可用：$candidate_ref" >&2
    echo "[FAIL] 当前模式 PULL_GATEWAY_CANDIDATE_MODE=fail-fast，未改写 deploy env。" >&2
    echo "[FAIL] 改为自动切换可执行：PULL_GATEWAY_CANDIDATE_MODE=auto-switch bash ./scripts/images/pull_images.sh" >&2
    return 12
  fi

  [[ "$profile_active" == "1" ]] || return 0
  mkdir -p "$(dirname "$IMAGE_ENV_DEPLOY_ENV_PATH")"
  [[ -f "$IMAGE_ENV_DEPLOY_ENV_PATH" ]] || : > "$IMAGE_ENV_DEPLOY_ENV_PATH"
  pin_env_upsert_key "$IMAGE_ENV_DEPLOY_ENV_PATH" OPENCLAW_OFFICIAL_GATEWAY_IMAGE "$candidate_ref"
  IMAGE_ENV_LOADED=0
  image_env_load
  write_gateway_source_selection_json 'auto_switched_equal_candidate' true "$default_repo:$tag@$digest" "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE" "$candidate_ref" "$digest" "$candidate_repo" "$mode"
  echo "[INFO] Gateway official GHCR 在当前 CN profile 下已自动切换到等值 candidate source：$candidate_ref"
  echo "[INFO] 仅更新当前 deploy env：$IMAGE_ENV_DEPLOY_ENV_PATH；canonical pin 未改写。"
  echo "[INFO] source selection 记录：$PULL_GATEWAY_SELECTION_FILE"
}

pull_timeout_for_image() {
  local image="$1"
  if [[ "$image" == "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE" ]]; then
    printf '%s\n' "$PULL_GATEWAY_OFFICIAL_TIMEOUT"
    return 0
  fi
  printf '%s\n' "$PULL_TIMEOUT"
}

print_pull_failure_next_steps() {
  local image="$1"
  {
    echo "[HINT] 镜像拉取失败后的固定分流："
    echo "  1. 先执行 bash ./scripts/doctor/check_docker_host_readiness.sh，确认 Docker daemon、registry-mirrors 与 selected / candidate 镜像来源连通性。"
    echo "  2. 中国国内网络首轮部署执行 sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn；若仅补 daemon 镜像加速，执行 sudo bash ./scripts/setup/prepare_docker_host.sh --configure-daemon。"
    echo "  3. 若目标机持续无法访问当前 selected source，改走离线归档：bash ./scripts/images/export_deployment_images.sh -> load_deployment_images.sh。"
    if [[ "$image" == "$OPENCLAW_OFFICIAL_GATEWAY_IMAGE" ]]; then
      echo "  4. 若仅 GHCR official Gateway 不可达，默认由 PULL_GATEWAY_CANDIDATE_MODE=auto-switch 在 CN profile 下选择等值 candidate；查看 state/image_pull/gateway_source_selection.json。"
    elif [[ "$image" == "$OPENCLAW_CONTROL_PLANE_IMAGE" || "$image" == "$OPENCLAW_RUNTIME_PYTHON_IMAGE" || "$image" == "$NGINX_IMAGE" ]]; then
      echo "  4. Python / Nginx 默认 pin 已使用 Daocloud tag@digest；仍不可达时优先使用企业内网 registry 或离线归档，不要改成 mutable tag。"
    fi
  } >&2
}

if [[ "${1:-}" == "--list" || "${1:-}" == "-l" ]]; then
  target_images_text="$(deployment_images_list_images)" || fail '无法解析部署镜像合同集合' 2
  mapfile -t TARGET_IMAGES <<< "$target_images_text"
  printf '%s\n' "${TARGET_IMAGES[@]}"
  exit 0
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ -n "${1:-}" ]]; then
  echo "[FAIL] 不支持的参数：${1}" >&2
  usage >&2
  exit 2
fi

require_dir_manageable_or_creatable "$(dirname "$PULL_STATE_FILE")" "镜像拉取状态目录"
require_dir_manageable_or_creatable "$(dirname "$PULL_RECORD_FILE")" "镜像拉取记录目录"
# state/image_pull 是镜像脚本运行时状态，只在真实镜像操作前创建。
mkdir -p "$(dirname "$PULL_STATE_FILE")"
mkdir -p "$(dirname "$PULL_RECORD_FILE")"
require_file_manageable_or_creatable "$PULL_STATE_FILE" "镜像拉取状态文件"
require_file_manageable_or_creatable "$PULL_RECORD_FILE" "镜像拉取记录文件"
touch "$PULL_RECORD_FILE"

TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
fi

if ! deployment_images_require_docker_cli; then
  echo "        无法执行镜像拉取；请先安装 Docker / Docker Compose，再重试 ./scripts/images/pull_images.sh" >&2
  exit 20
fi
if ! deployment_images_require_docker_daemon; then
  exit 21
fi

PULL_GATEWAY_CANDIDATE_MODE="$(gateway_candidate_mode)"
maybe_select_gateway_candidate_source "$PULL_GATEWAY_CANDIDATE_MODE" || exit $?
target_images_text="$(deployment_images_list_images)" || fail '无法解析部署镜像合同集合' 2
mapfile -t TARGET_IMAGES <<< "$target_images_text"

declare -A PULLED=()
if [[ -f "$PULL_STATE_FILE" ]]; then
  while IFS= read -r line; do
    [[ -n "$line" ]] && PULLED["$line"]=1
  done < "$PULL_STATE_FILE"
fi

mark_pulled() {
  local image="$1"
  if [[ -z "${PULLED["$image"]+x}" ]]; then
    printf '%s\n' "$image" >> "$PULL_STATE_FILE"
    PULLED["$image"]=1
  fi
}

image_exists_locally() {
  local image="$1"
  deployment_images_image_present "$image"
}

kill_process_tree() {
  local root_pid="$1"
  local signal="$2"

  if command -v pgrep >/dev/null 2>&1; then
    local children
    children=$(pgrep -P "$root_pid" 2>/dev/null || true)
    for child in $children; do
      kill -"$signal" "$child" 2>/dev/null || true
    done
  fi

  kill -"$signal" "$root_pid" 2>/dev/null || true
}

on_interrupt() {
  echo "" >&2
  echo "[WARN] 收到中断信号。已完成的镜像会保留，本脚本可稍后继续执行。" >&2

  if [[ -n "$CURRENT_PULL_PID" ]]; then
    echo "[WARN] 正在终止当前 docker pull 进程：PID=$CURRENT_PULL_PID" >&2
    kill_process_tree "$CURRENT_PULL_PID" TERM
    sleep "$PULL_KILL_AFTER" || true
    kill_process_tree "$CURRENT_PULL_PID" KILL
  fi

  exit 130
}

trap on_interrupt INT TERM

inspect_image_id() {
  local image="$1"
  docker image inspect "$image" --format '{{.Id}}'
}

inspect_image_digests() {
  local image="$1"
  docker image inspect "$image" --format '{{range .RepoDigests}}{{println .}}{{end}}' 2>/dev/null | paste -sd ',' -
}

record_pull_result() {
  local image="$1"
  local result="$2"
  local image_id=""
  local digests=""

  if image_exists_locally "$image"; then
    image_id="$(inspect_image_id "$image")"
    digests="$(inspect_image_digests "$image")"
  fi

  printf '%s | %s | %s | %s | %s\n' \
    "$(date +"%Y-%m-%dT%H:%M:%S%:z")" \
    "$result" \
    "$image" \
    "${image_id:-<none>}" \
    "${digests:-<none>}" \
    >> "$PULL_RECORD_FILE"
}

run_pull_once() {
  local image="$1"
  local pull_timeout=''
  pull_timeout="$(pull_timeout_for_image "$image")"

  if [[ -n "$TIMEOUT_BIN" && "$pull_timeout" -gt 0 ]]; then
    "$TIMEOUT_BIN" --foreground -k "$PULL_KILL_AFTER" "$pull_timeout" docker pull "$image" &
  else
    docker pull "$image" &
  fi

  CURRENT_PULL_PID=$!
  wait "$CURRENT_PULL_PID"
}

pull_with_retry() {
  local image="$1"
  local attempt=1
  local wait_seconds="$PULL_SLEEP_BASE"

  while (( attempt <= PULL_RETRIES )); do
    echo "[INFO] 拉取镜像（$attempt/$PULL_RETRIES）：$image"

    if run_pull_once "$image"; then
      CURRENT_PULL_PID=""
      record_pull_result "$image" "pulled"
      return 0
    fi

    local exit_code=$?
    CURRENT_PULL_PID=""
    echo "[WARN] 拉取失败：$image（退出码：$exit_code）" >&2

    if (( attempt == PULL_RETRIES )); then
      record_pull_result "$image" "failed"
      return "$exit_code"
    fi

    echo "[INFO] ${wait_seconds}s 后重试：$image"
    sleep "$wait_seconds"
    wait_seconds=$(( wait_seconds * PULL_BACKOFF ))
    attempt=$(( attempt + 1 ))
  done
}

for image in "${TARGET_IMAGES[@]}"; do
  if [[ "$PULL_FORCE" != "1" ]] && image_exists_locally "$image"; then
    echo "[SKIP] 本地已存在：$image"
    mark_pulled "$image"
    record_pull_result "$image" "skipped-existing"
    continue
  fi

  if pull_with_retry "$image"; then
    mark_pulled "$image"
    echo "[OK] 镜像已就绪：$image"
  else
    echo "[FAIL] 镜像拉取失败：$image" >&2
    print_pull_failure_next_steps "$image"
    exit 1
  fi
done

echo "[OK] 外部镜像拉取完成。"
echo "[INFO] 拉取记录：$PULL_RECORD_FILE"
echo "[INFO] 可继续执行：./scripts/images/verify_gateway_browser.sh"
