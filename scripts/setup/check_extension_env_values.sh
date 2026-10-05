#!/usr/bin/env bash
# 用途：按 active profile 检查扩展 env 与共享 site env 缺项，并输出分组修复命令。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/check_extension_env_values.sh --profile <profile_id> [--format text|json]
  bash ./scripts/setup/check_extension_env_values.sh --extension <id> [--format text|json]

说明：
  - Python 真源会读取 active profile、*.deploy_env_schema.json、组合 profile sharedDeployEnvFields 与 dispatch registry。
  - 组合 profile 中的 OLLAMA_BASE_URL / OLLAMA_MODEL_REF 等共享模型键由 deploy/site.env 提供，扩展自有键由 extension.env 提供。
  - 输出只包含字段名、分组、scope、owner、是否 secret 与 fixCommand；不输出 secret 值。
  - 执行前需已准备 host 控制面介质：bash ./scripts/setup/prepare_control_plane_medium.sh。
USAGE
}

if [[ "${1:-}" == '-h' || "${1:-}" == '--help' ]]; then
  usage
  exit 0
fi

exec bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" setup env input-values check-extension-env "$@"
