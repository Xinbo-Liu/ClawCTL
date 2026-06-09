#!/usr/bin/env bash
# 用途：修复运行态与仓库内关键目录权限，避免部署后残留宽权限。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/fix_permissions.sh

说明：
  - 修复运行态目录、已有镜像运行时状态目录、control-plane state release/evidence 与 deploy 输入的权限；
  - 以 root 执行时必须能解析 OPENCLAW_RUNTIME_UID/GID，解析失败会中止，避免留下 root-owned runtime state；
  - 脚本不接受业务参数；如只查看说明，请使用 --help。
USAGE
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[fix_permissions][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
fi

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
runtime_permissions_assert_root_runtime_uid_gid_resolvable "$ROOT_DIR"
runtime_permissions_prepare_repo_support_dirs "$ROOT_DIR"
runtime_permissions_prepare_openclaw_state_layout "$ROOT_DIR"
runtime_permissions_prepare_image_state_layout "$ROOT_DIR"
runtime_permissions_prepare_release_evidence_layout "$ROOT_DIR"
runtime_permissions_align_openclaw_runtime_owner_only "$ROOT_DIR" "$(runtime_permissions_host_state_root "$ROOT_DIR")"
runtime_permissions_align_repo_local_state_owner_only "$ROOT_DIR" "$ROOT_DIR/state/image_pull"
runtime_permissions_align_repo_local_state_owner_only "$ROOT_DIR" "$ROOT_DIR/state/image_artifacts"
runtime_permissions_harden_deploy_inputs "$ROOT_DIR"
runtime_permissions_align_release_evidence_readable "$ROOT_DIR"
runtime_permissions_mark_shebang_executable "$ROOT_DIR"
runtime_permissions_harden_certs "$ROOT_DIR"
runtime_permissions_prepare_runtime_bind_mount_selinux_contexts "$ROOT_DIR"
runtime_permissions_prepare_ingress_cap_drop_mount_access "$ROOT_DIR"
