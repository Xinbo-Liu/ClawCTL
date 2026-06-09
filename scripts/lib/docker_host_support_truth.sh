#!/usr/bin/env bash
# 用途：统一读取 docker host supported policy 真源，避免 shell 入口各自维护一份 supported_centos7 取值逻辑。
set -euo pipefail

OPENCLAW_DOCKER_HOST_SUPPORT_TRUTH_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$OPENCLAW_DOCKER_HOST_SUPPORT_TRUTH_LIB_DIR/repo_root.sh"
OPENCLAW_DOCKER_HOST_SUPPORT_TRUTH_ROOT="$(openclaw_repo_root_from "$OPENCLAW_DOCKER_HOST_SUPPORT_TRUTH_LIB_DIR")"
unset OPENCLAW_DOCKER_HOST_SUPPORT_TRUTH_LIB_DIR
# shellcheck source=repo_contracts.sh
source "$OPENCLAW_DOCKER_HOST_SUPPORT_TRUTH_ROOT/scripts/lib/repo_contracts.sh"
repo_contract_assign_relpath OPENCLAW_DOCKER_HOST_SUPPORT_TRUTH_REL_PATH governance.docker_host_support

docker_host_support_truth_path() {
  printf '%s/%s\n' "$OPENCLAW_DOCKER_HOST_SUPPORT_TRUTH_ROOT" "$OPENCLAW_DOCKER_HOST_SUPPORT_TRUTH_REL_PATH"
}

docker_host_support_truth_relpath() {
  printf '%s\n' "$OPENCLAW_DOCKER_HOST_SUPPORT_TRUTH_REL_PATH"
}

docker_host_support_supported_centos7_section_value() {
  local path="$1"
  local section="$2"
  local key="$3"
  local fallback="${4-}"
  local value=''
  if command -v jq >/dev/null 2>&1 && [[ -f "$path" ]]; then
    value="$(jq -r --arg section "$section" --arg key "$key" '.policies.supported_centos7[$section][$key] // empty' "$path" 2>/dev/null || true)"
  elif [[ -f "$path" ]]; then
    value="$(awk -v section="$section" -v key="$key" '
      BEGIN {in_policy=0; in_section=0}
      index($0, "\"supported_centos7\"") {in_policy=1}
      in_policy && index($0, "\"" section "\"") {in_section=1; next}
      in_section && index($0, "\"" key "\"") {
        line=$0
        sub(/^.*:[[:space:]]*\"/, "", line)
        sub(/\".*$/, "", line)
        print line
        exit
      }
      in_section && /}/ {in_section=0}
    ' "$path")"
  fi
  if [[ -n "$value" ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$fallback"
  fi
}

docker_host_support_supported_centos7_scalar() {
  local path="$1"
  local key="$2"
  local fallback="${3-}"
  local value=''
  if command -v jq >/dev/null 2>&1 && [[ -f "$path" ]]; then
    value="$(jq -r --arg key "$key" '.policies.supported_centos7[$key] // empty' "$path" 2>/dev/null || true)"
  elif [[ -f "$path" ]]; then
    value="$(awk -v key="$key" '
      BEGIN {in_policy=0}
      index($0, "\"supported_centos7\"") {in_policy=1; next}
      in_policy && index($0, "\"" key "\"") {
        line=$0
        sub(/^.*:[[:space:]]*/, "", line)
        sub(/^[[:space:]]*\"/, "", line)
        sub(/\"[[:space:]]*,?[[:space:]]*$/, "", line)
        sub(/[[:space:]]*,?[[:space:]]*$/, "", line)
        print line
        exit
      }
    ' "$path")"
  fi
  if [[ -n "$value" ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$fallback"
  fi
}

docker_host_support_supported_centos7_docker_repo_candidates() {
  local path="$1"
  if command -v jq >/dev/null 2>&1 && [[ -f "$path" ]]; then
    jq -r '.policies.supported_centos7.docker_yum_repositories[]? | [.id, .url] | @tsv' "$path" 2>/dev/null || true
    return 0
  fi
  printf '%s\t%s\n' 'official' 'https://download.docker.com/linux/centos/docker-ce.repo'
  printf '%s\t%s\n' 'aliyun_cn' 'https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo'
  printf '%s\t%s\n' 'tuna_cn' 'https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/centos/docker-ce.repo'
}

docker_host_support_supported_centos7_vault_repo_candidates() {
  local path="$1"
  if command -v jq >/dev/null 2>&1 && [[ -f "$path" ]]; then
    jq -r '.policies.supported_centos7.centos_vault_repositories[]? | [.id, .base_url] | @tsv' "$path" 2>/dev/null || true
    return 0
  fi
  printf '%s\t%s\n' 'official' 'https://vault.centos.org/7.9.2009'
  printf '%s\t%s\n' 'aliyun_cn' 'https://mirrors.aliyun.com/centos-vault/7.9.2009'
  printf '%s\t%s\n' 'tuna_cn' 'https://mirrors.tuna.tsinghua.edu.cn/centos-vault/7.9.2009'
}

docker_host_support_supported_centos7_registry_mirrors() {
  local path="$1"
  if command -v jq >/dev/null 2>&1 && [[ -f "$path" ]]; then
    jq -r '.policies.supported_centos7.docker_daemon.registry_mirrors[]? // empty' "$path" 2>/dev/null || true
    return 0
  fi
  printf '%s\n' 'https://docker.m.daocloud.io'
  printf '%s\n' 'https://docker.nju.edu.cn'
  printf '%s\n' 'https://docker.mirrors.ustc.edu.cn'
}

docker_host_support_supported_centos7_network_profile_value() {
  local path="$1"
  local profile="$2"
  local key="$3"
  local fallback="${4-}"
  local value=''
  if command -v jq >/dev/null 2>&1 && [[ -f "$path" ]]; then
    value="$(jq -r --arg profile "$profile" --arg key "$key" '.policies.supported_centos7.network_profiles[]? | select(.id == $profile) | .[$key] // empty' "$path" 2>/dev/null || true)"
  elif [[ -f "$path" ]]; then
    case "$profile:$key" in
      cn:centos7_vault_source)
        value='aliyun_cn'
        ;;
      cn:docker_repo_source)
        value='aliyun_cn'
        ;;
      global:centos7_vault_source)
        value='official'
        ;;
      global:docker_repo_source)
        value='official'
        ;;
    esac
  fi
  if [[ -n "$value" ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$fallback"
  fi
}
