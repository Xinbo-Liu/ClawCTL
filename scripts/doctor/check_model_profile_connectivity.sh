#!/usr/bin/env bash
# 用途：校验当前 active control-plane profile 声明的模型 env 与运行通道是否可解析。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$SCRIPT_DIR/../lib/repo_root.sh"
ROOT_DIR="${ROOT_DIR:-$(openclaw_repo_root_from "$SCRIPT_DIR")}"
# shellcheck source=../lib/repo_python_env.sh
source "$ROOT_DIR/scripts/lib/repo_python_env.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=../setup/lib/deploy_env_shell.sh
source "$ROOT_DIR/scripts/setup/lib/deploy_env_shell.sh"

CONFIG_PATH="${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}"
CONFIG_PATH_EXPLICIT=0
CONTROL_PLANE_PROFILE="${OPENCLAW_CONTROL_PLANE_PROFILE:-agent_platform}"
CONTROL_PLANE_PROFILE_EXPLICIT=0
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deploy/.env}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config-path)
      [[ $# -ge 2 ]] || { echo "[check_model_profile_connectivity][FAIL] --config-path 缺少路径参数" >&2; exit 2; }
      CONFIG_PATH="$2"
      CONFIG_PATH_EXPLICIT=1
      shift
      ;;
    --env-file)
      [[ $# -ge 2 ]] || { echo "[check_model_profile_connectivity][FAIL] --env-file 缺少路径参数" >&2; exit 2; }
      ENV_FILE="$2"
      shift
      ;;
    *)
      echo "[check_model_profile_connectivity][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
  shift
done

if [[ -n "${OPENCLAW_CONTROL_PLANE_PROFILE:-}" ]]; then
  CONTROL_PLANE_PROFILE_EXPLICIT=1
fi

openclaw_control_plane_apply_env_file_active_selection \
  "$ENV_FILE" \
  CONFIG_PATH \
  CONTROL_PLANE_PROFILE \
  CONTROL_PLANE_PROFILE_EXPLICIT \
  "$CONFIG_PATH_EXPLICIT" \
  "$ENV_FILE" || {
    echo "[check_model_profile_connectivity][FAIL] 无法从 env 文件解析 control-plane profile 配置：$ENV_FILE" >&2
    exit 2
  }

CONFIG_PATH="$(openclaw_control_plane_resolve_config_path "$CONTROL_PLANE_PROFILE" "$CONFIG_PATH" "$CONTROL_PLANE_PROFILE_EXPLICIT")" || {
  echo "[check_model_profile_connectivity][FAIL] 无法解析 control-plane profile 配置" >&2
  exit 2
}

python_runner="${PYTHON_RUNNER:-$ROOT_DIR/scripts/runtime/run_python_container.sh}"
repo_python_env_args=()
while IFS= read -r -d '' item; do
  repo_python_env_args+=("$item")
done < <(openclaw_repo_python_env_args "$ROOT_DIR")

specs_json="$(
  OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$CONFIG_PATH" \
    bash "$python_runner" \
      --workdir "$ROOT_DIR" \
      "${repo_python_env_args[@]}" \
      --env "OPENCLAW_REPO_ROOT=$ROOT_DIR" \
      --env "OPENCLAW_TOOLS_ROOT=$ROOT_DIR" \
      --env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$CONFIG_PATH" \
      -- - "$CONFIG_PATH" <<'PY'
import json
import sys
from pathlib import Path

from openclaw.control_plane.registry import load_registry
from openclaw.lib.models.env import model_env_specs_from_registry

registry = load_registry(Path(sys.argv[1]))
specs = model_env_specs_from_registry(registry, scheduler_scope=True)
print(json.dumps({
    "envSpecs": [
        {
            "name": spec.name,
            "required": spec.required,
            "secret": spec.secret,
            "validator": spec.validator,
            "purpose": spec.purpose,
            "sourceModelRef": spec.source_model_ref,
        }
        for spec in specs.values()
    ]
}, ensure_ascii=False))
PY
)" || {
  echo "[check_model_profile_connectivity][FAIL] 无法解析当前 active profile 的模型 env 需求" >&2
  exit 1
}

command -v jq >/dev/null 2>&1 || {
  echo "[check_model_profile_connectivity][FAIL] 缺少 jq，无法解析模型 env 需求" >&2
  exit 1
}

mapfile -t env_names < <(jq -r '.envSpecs[]?.name // empty' <<<"$specs_json")
if [[ -f "$ENV_FILE" && ${#env_names[@]} -gt 0 ]]; then
  deploy_env_shell_load_keys "$ENV_FILE" "${env_names[@]}" >/dev/null 2>&1 || true
fi

total="$(jq -r '(.envSpecs // []) | length' <<<"$specs_json")"
if [[ "$total" == '0' ]]; then
  echo "当前 active profile 未声明需要运行态模型 env 的作业"
  exit 0
fi

accept_http_code() {
  local code="$1"
  [[ "$code" =~ ^2[0-9][0-9]$ || "$code" =~ ^3[0-9][0-9]$ || "$code" == "401" || "$code" == "403" ]]
}

missing=()
invalid=()
http_failures=()
local_failures=()
checked=()

while IFS=$'\t' read -r name required validator purpose; do
  [[ -n "$name" ]] || continue
  value="${!name:-}"
  if [[ "$required" == 'true' && -z "$value" ]]; then
    missing+=("$name")
    continue
  fi
  [[ -n "$value" ]] || continue
  case "$validator" in
    http_url)
      if [[ "$value" != http://* && "$value" != https://* ]]; then
        invalid+=("$name 必须是 http/https URL")
        continue
      fi
      if ! command -v curl >/dev/null 2>&1; then
        http_failures+=("$name: 缺少 curl")
        continue
      fi
      set +e
      code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 8 --max-time 25 "$value")"
      rc=$?
      set -e
      if [[ $rc -eq 0 ]] && accept_http_code "$code"; then
        checked+=("$name=HTTP $code")
      else
        http_failures+=("$name: code=${code:-N/A}, rc=$rc")
      fi
      ;;
    non_empty)
      if [[ "$purpose" == 'local_model_command' ]]; then
        command_text="$value"
        first_word="${command_text%% *}"
        if [[ -z "$first_word" || ! -x "$first_word" ]] && ! command -v "$first_word" >/dev/null 2>&1; then
          local_failures+=("$name: 本地模型命令不可执行")
        else
          checked+=("$name=local_process")
        fi
      else
        checked+=("$name=${purpose:-configured}")
      fi
      ;;
    secret_like)
      checked+=("$name=<secret>")
      ;;
    *)
      checked+=("$name=configured")
      ;;
  esac
done < <(jq -r '.envSpecs[] | [.name, (.required|tostring), .validator, .purpose] | @tsv' <<<"$specs_json")

if ((${#missing[@]} > 0)) || ((${#invalid[@]} > 0)) || ((${#http_failures[@]} > 0)) || ((${#local_failures[@]} > 0)); then
  summary=''
  ((${#missing[@]} > 0)) && summary+="缺少必填：${missing[*]}；"
  ((${#invalid[@]} > 0)) && summary+="非法值：${invalid[*]}；"
  ((${#http_failures[@]} > 0)) && summary+="HTTP 探测失败：${http_failures[*]}；"
  ((${#local_failures[@]} > 0)) && summary+="本地模型命令失败：${local_failures[*]}；"
  echo "[check_model_profile_connectivity][FAIL] $summary" >&2
  exit 1
fi

echo "模型 env 与渠道探测通过：${checked[*]}"
