#!/usr/bin/env bash
# 用途：在不依赖宿主机 Python 的前提下查询本地工作区残留策略真源。

if [[ -n "${OPENCLAW_LOCAL_WORKSPACE_POLICY_SH_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
OPENCLAW_LOCAL_WORKSPACE_POLICY_SH_LOADED=1

openclaw_local_workspace_policy_shell_path() {
  local raw_path="${1-}"
  if command -v cygpath >/dev/null 2>&1 && [[ "$raw_path" =~ ^[A-Za-z]:[\\/].*$ ]]; then
    cygpath -u "$raw_path"
    return 0
  fi
  printf '%s\n' "$raw_path"
}

OPENCLAW_LOCAL_WORKSPACE_POLICY_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$OPENCLAW_LOCAL_WORKSPACE_POLICY_LIB_DIR/repo_root.sh"
OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR="$(openclaw_repo_root_from "$OPENCLAW_LOCAL_WORKSPACE_POLICY_LIB_DIR")"
unset OPENCLAW_LOCAL_WORKSPACE_POLICY_LIB_DIR
OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR="$(openclaw_local_workspace_policy_shell_path "$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR")"
# shellcheck source=repo_contracts.sh
source "$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR/scripts/lib/repo_contracts.sh"
repo_contract_assign_relpath OPENCLAW_LOCAL_WORKSPACE_POLICY_REL_PATH governance.local_workspace_policy
repo_contract_assign_relpath OPENCLAW_LOCAL_WORKSPACE_INSTALL_DEFAULTS_REL_PATH governance.install_defaults
OPENCLAW_LOCAL_WORKSPACE_POLICY_PATH="$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR/$OPENCLAW_LOCAL_WORKSPACE_POLICY_REL_PATH"
OPENCLAW_LOCAL_WORKSPACE_INSTALL_DEFAULTS_PATH="$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR/$OPENCLAW_LOCAL_WORKSPACE_INSTALL_DEFAULTS_REL_PATH"

openclaw_local_workspace_policy_wait_for_file() {
  local target_path="$1"
  target_path="$(openclaw_local_workspace_policy_shell_path "$target_path")"
  local attempt=0
  while (( attempt < 5 )); do
    [[ -f "$target_path" ]] && return 0
    sleep 0.1
    attempt=$(( attempt + 1 ))
  done
  return 1
}

openclaw_local_workspace_policy_trim_cr() {
  local value="${1-}"
  printf '%s\n' "${value%$'\r'}"
}

openclaw_local_workspace_policy_require_runtime() {
  openclaw_local_workspace_policy_wait_for_file "$OPENCLAW_LOCAL_WORKSPACE_POLICY_PATH" || {
    echo "[local_workspace_policy][FAIL] 缺少真源：$OPENCLAW_LOCAL_WORKSPACE_POLICY_PATH" >&2
    return 97
  }
  openclaw_local_workspace_policy_wait_for_file "$OPENCLAW_LOCAL_WORKSPACE_INSTALL_DEFAULTS_PATH" || {
    echo "[local_workspace_policy][FAIL] 缺少 install_defaults 真源：$OPENCLAW_LOCAL_WORKSPACE_INSTALL_DEFAULTS_PATH" >&2
    return 97
  }
}

openclaw_local_workspace_policy_require_jq() {
  openclaw_local_workspace_policy_require_runtime
  command -v jq >/dev/null 2>&1 || {
    echo '[local_workspace_policy][FAIL] 缺少 jq；无法读取 local_workspace_policy 真源。' >&2
    return 97
  }
}

openclaw_local_workspace_policy_use_jq() {
  [[ "${OPENCLAW_LOCAL_WORKSPACE_POLICY_FORCE_AWK:-0}" == '1' ]] && return 1
  command -v jq >/dev/null 2>&1
}

openclaw_local_workspace_policy_jq() {
  local target_rel="${1:?target_rel is required}"
  shift
  (( $# > 0 )) || {
    echo '[local_workspace_policy][FAIL] jq 调用缺少过滤参数' >&2
    return 97
  }
  local target_file="$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR/$target_rel"
  target_file="$(openclaw_local_workspace_policy_shell_path "$target_file")"
  openclaw_local_workspace_policy_require_jq || return $?
  (
    cd "$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR" || exit 97
    jq -r "$@" < "$target_file"
  )
}

openclaw_local_workspace_policy_json_string_awk() {
  local target_file="$1"
  local key="$2"
  awk -v key="$key" '
    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }
    function json_string_value(line, key,    pattern, value) {
      pattern = "\"" key "\"[[:space:]]*:[[:space:]]*\""
      if (line !~ pattern) {
        return ""
      }
      value = line
      sub("^.*" pattern, "", value)
      sub("\"[[:space:]]*,?[[:space:]]*$", "", value)
      gsub("\\\\/", "/", value)
      gsub("\\\\\\\\", "\\", value)
      gsub("\\\\\"", "\"", value)
      return value
    }
    {
      line = $0
      sub(/\r$/, "", line)
      value = json_string_value(line, key)
      if (value != "") {
        print trim(value)
        found = 1
        exit
      }
    }
    END {
      if (!found) {
        print "[local_workspace_policy][FAIL] JSON 字段缺失：" key > "/dev/stderr"
        exit 97
      }
    }
  ' "$target_file"
}

openclaw_local_workspace_policy_target_records_awk() {
  local row_sep=$'\037'
  awk -v row_sep="$row_sep" '
    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }
    function json_string_value(line, key,    pattern, value) {
      pattern = "\"" key "\"[[:space:]]*:[[:space:]]*\""
      if (line !~ pattern) {
        return ""
      }
      value = line
      sub("^.*" pattern, "", value)
      sub("\"[[:space:]]*,?[[:space:]]*$", "", value)
      gsub("\\\\/", "/", value)
      gsub("\\\\\\\\", "\\", value)
      gsub("\\\\\"", "\"", value)
      return value
    }
    function json_bool_value(line, key,    pattern, value) {
      pattern = "\"" key "\"[[:space:]]*:[[:space:]]*"
      if (line !~ pattern) {
        return ""
      }
      value = line
      sub("^.*" pattern, "", value)
      sub("[[:space:]]*,?[[:space:]]*$", "", value)
      return value
    }
    function emit_target() {
      id = trim(id)
      raw_path = trim(raw_path)
      truth_ref = trim(truth_ref)
      target_class = trim(target_class)
      cleanup_by_default = trim(cleanup_by_default)
      if (id == "" || target_class == "" || cleanup_by_default == "") {
        print "[local_workspace_policy][FAIL] targets 项缺少必填字段" > "/dev/stderr"
        exit 97
      }
      print id row_sep raw_path row_sep truth_ref row_sep target_class row_sep cleanup_by_default
      emitted += 1
    }
    {
      line = $0
      sub(/\r$/, "", line)
    }
    line ~ /"targets"[[:space:]]*:[[:space:]]*\[/ {
      in_targets = 1
      next
    }
    in_targets && line ~ /^[[:space:]]*\]/ {
      in_targets = 0
      next
    }
    !in_targets {
      next
    }
    line ~ /^[[:space:]]*\{/ {
      in_entry = 1
      id = ""
      raw_path = ""
      truth_ref = ""
      target_class = ""
      cleanup_by_default = ""
      next
    }
    !in_entry {
      next
    }
    {
      value = json_string_value(line, "id")
      if (value != "") id = value
      value = json_string_value(line, "path")
      if (value != "") raw_path = value
      value = json_string_value(line, "truthRef")
      if (value != "") truth_ref = value
      value = json_string_value(line, "class")
      if (value != "") target_class = value
      value = json_bool_value(line, "cleanupByDefault")
      if (value != "") cleanup_by_default = value
    }
    line ~ /^[[:space:]]*\}[[:space:]]*,?[[:space:]]*$/ {
      emit_target()
      in_entry = 0
      next
    }
    END {
      if (emitted == 0) {
        print "[local_workspace_policy][FAIL] local_workspace_policy.targets 为空" > "/dev/stderr"
        exit 97
      }
    }
  ' "$OPENCLAW_LOCAL_WORKSPACE_POLICY_PATH"
}

openclaw_local_workspace_policy_derived_globs_awk() {
  awk '
    function json_array_string(line,    value) {
      value = line
      sub(/^[[:space:]]*"/, "", value)
      sub(/"[[:space:]]*,?[[:space:]]*$/, "", value)
      gsub("\\\\/", "/", value)
      gsub("\\\\\\\\", "\\", value)
      gsub("\\\\\"", "\"", value)
      return value
    }
    {
      line = $0
      sub(/\r$/, "", line)
    }
    line ~ /"derivedGlobs"[[:space:]]*:[[:space:]]*\[/ {
      in_globs = 1
      next
    }
    in_globs && line ~ /^[[:space:]]*\]/ {
      in_globs = 0
      next
    }
    in_globs && line ~ /^[[:space:]]*"/ {
      value = json_array_string(line)
      if (value != "") {
        print value
        emitted += 1
      }
    }
    END {
      if (emitted == 0) {
        print "[local_workspace_policy][FAIL] local_workspace_policy.derivedGlobs 为空" > "/dev/stderr"
        exit 97
      }
    }
  ' "$OPENCLAW_LOCAL_WORKSPACE_POLICY_PATH"
}

openclaw_local_workspace_policy_normalize_rel_path() {
  local normalized=''
  openclaw_local_workspace_policy_normalize_rel_path_assign normalized "${1-}" || return $?
  printf '%s\n' "$normalized"
}

openclaw_local_workspace_policy_normalize_rel_path_assign() {
  local __target_var="${1:?target var is required}"
  local raw="${2-}"
  [[ "$__target_var" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
    echo "[local_workspace_policy][FAIL] 非法变量名：$__target_var" >&2
    return 97
  }
  raw="${raw%$'\r'}"
  raw="${raw//\\//}"
  raw="${raw#"${raw%%[![:space:]]*}"}"
  raw="${raw%"${raw##*[![:space:]]}"}"
  raw="${raw#/}"
  raw="${raw%/}"
  [[ -n "$raw" ]] || {
    echo '[local_workspace_policy][FAIL] local_workspace_policy 路径不能为空' >&2
    return 97
  }
  case "$raw" in
    ..|../*|*/../*|*/..)
      echo "[local_workspace_policy][FAIL] local_workspace_policy 路径越界：$2" >&2
      return 97
      ;;
  esac
  printf -v "$__target_var" '%s' "$raw"
}

openclaw_local_workspace_policy_resolve_truth_ref() {
  local resolved=''
  openclaw_local_workspace_policy_resolve_truth_ref_assign resolved "${1:?truth_ref is required}" || return $?
  printf '%s\n' "$resolved"
}

openclaw_local_workspace_policy_resolve_truth_ref_assign() {
  local __target_var="${1:?target var is required}"
  local truth_ref="${2:?truth_ref is required}"
  [[ "$__target_var" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
    echo "[local_workspace_policy][FAIL] 非法变量名：$__target_var" >&2
    return 97
  }
  openclaw_local_workspace_policy_require_runtime || return $?
  case "$truth_ref" in
    host_state_root)
      local raw_path=''
      if openclaw_local_workspace_policy_use_jq; then
        raw_path="$(
          openclaw_local_workspace_policy_jq "$OPENCLAW_LOCAL_WORKSPACE_INSTALL_DEFAULTS_REL_PATH" '.defaults.host_state_root // empty'
        )"
      else
        raw_path="$(
          openclaw_local_workspace_policy_json_string_awk "$OPENCLAW_LOCAL_WORKSPACE_INSTALL_DEFAULTS_PATH" host_state_root
        )"
      fi
      [[ -n "$raw_path" ]] || {
        echo '[local_workspace_policy][FAIL] install_defaults 缺少 defaults.host_state_root' >&2
        return 97
      }
      openclaw_local_workspace_policy_normalize_rel_path_assign "$__target_var" "$raw_path"
      ;;
    *)
      echo "[local_workspace_policy][FAIL] local_workspace_policy.truthRef 非法：$truth_ref" >&2
      return 97
      ;;
  esac
}

openclaw_local_workspace_policy_dedupe_lines() {
  awk '!seen[$0]++'
}

openclaw_local_workspace_policy_target_rows() {
  openclaw_local_workspace_policy_require_runtime || return $?
  local row_sep=$'\037'
  local target_id=''
  local raw_path=''
  local truth_ref=''
  local target_class=''
  local cleanup_by_default=''
  local resolved_path=''
  while IFS="$row_sep" read -r target_id raw_path truth_ref target_class cleanup_by_default; do
    target_id="${target_id%$'\r'}"
    raw_path="${raw_path%$'\r'}"
    truth_ref="${truth_ref%$'\r'}"
    target_class="${target_class%$'\r'}"
    cleanup_by_default="${cleanup_by_default%$'\r'}"
    [[ -n "$target_id" ]] || continue
    if [[ -n "$raw_path" && -n "$truth_ref" ]]; then
      echo "[local_workspace_policy][FAIL] target $target_id 同时声明 path 与 truthRef" >&2
      return 97
    fi
    if [[ -n "$truth_ref" ]]; then
      openclaw_local_workspace_policy_resolve_truth_ref_assign resolved_path "$truth_ref" || return $?
    else
      openclaw_local_workspace_policy_normalize_rel_path_assign resolved_path "$raw_path" || return $?
    fi
    printf '%s\t%s\t%s\t%s\t/%s/\n' \
      "$target_id" \
      "$resolved_path" \
      "$target_class" \
      "$([[ "$cleanup_by_default" == 'true' ]] && printf 'yes' || printf 'no')" \
      "$resolved_path"
  done < <(
    if openclaw_local_workspace_policy_use_jq; then
      openclaw_local_workspace_policy_jq "$OPENCLAW_LOCAL_WORKSPACE_POLICY_REL_PATH" \
        --arg row_sep "$row_sep" \
        '.targets[]? | [(.id // ""), (.path // ""), (.truthRef // ""), (.class // ""), ((.cleanupByDefault // false) | tostring)] | join($row_sep)'
    else
      openclaw_local_workspace_policy_target_records_awk
    fi
  )
}

openclaw_local_workspace_policy_targets() {
  openclaw_local_workspace_policy_target_rows
}

openclaw_local_workspace_policy_host_state_root() {
  openclaw_local_workspace_policy_resolve_truth_ref host_state_root
}

openclaw_local_workspace_policy_truth_path() {
  local truth_ref="${1:?truth_ref is required}"
  openclaw_local_workspace_policy_resolve_truth_ref "$truth_ref"
}

openclaw_local_workspace_policy_default_cleanup_targets() {
  openclaw_local_workspace_policy_require_runtime || return $?
  local rel_path=''
  local raw_path=''
  local truth_ref=''
  local cleanup_by_default=''
  local row_sep=$'\037'
  if openclaw_local_workspace_policy_use_jq; then
    openclaw_local_workspace_policy_jq "$OPENCLAW_LOCAL_WORKSPACE_POLICY_REL_PATH" \
      --arg row_sep "$row_sep" \
      '.targets[]? | select((.cleanupByDefault // false) == true) | [(.path // ""), (.truthRef // "")] | join($row_sep)' \
      | while IFS="$row_sep" read -r raw_path truth_ref; do
        raw_path="${raw_path%$'\r'}"
        truth_ref="${truth_ref%$'\r'}"
        if [[ -n "$raw_path" ]]; then
          local resolved_path=''
          openclaw_local_workspace_policy_normalize_rel_path_assign resolved_path "$raw_path" || return $?
          printf '%s\n' "$resolved_path"
        else
          local resolved_path=''
          openclaw_local_workspace_policy_resolve_truth_ref_assign resolved_path "$truth_ref" || return $?
          printf '%s\n' "$resolved_path"
        fi
      done
    return $?
  fi
  while IFS="$row_sep" read -r _target_id raw_path truth_ref _target_class cleanup_by_default; do
    raw_path="${raw_path%$'\r'}"
    truth_ref="${truth_ref%$'\r'}"
    cleanup_by_default="${cleanup_by_default%$'\r'}"
    [[ "$cleanup_by_default" == 'true' ]] || continue
    if [[ -n "$raw_path" ]]; then
      local resolved_path=''
      openclaw_local_workspace_policy_normalize_rel_path_assign resolved_path "$raw_path" || return $?
      printf '%s\n' "$resolved_path"
    else
      local resolved_path=''
      openclaw_local_workspace_policy_resolve_truth_ref_assign resolved_path "$truth_ref" || return $?
      printf '%s\n' "$resolved_path"
    fi
  done < <(openclaw_local_workspace_policy_target_records_awk)
}

openclaw_local_workspace_policy_target_paths() {
  local rel_path=''
  while IFS=$'\t' read -r _target_id rel_path _target_class _cleanup_by_default _gitignore_pattern; do
    [[ -n "$rel_path" ]] || continue
    printf '%s\n' "$rel_path"
  done < <(openclaw_local_workspace_policy_target_rows)
}

openclaw_local_workspace_policy_iter_derived_globs() {
  openclaw_local_workspace_policy_require_runtime || return $?
  if openclaw_local_workspace_policy_use_jq; then
    openclaw_local_workspace_policy_jq "$OPENCLAW_LOCAL_WORKSPACE_POLICY_REL_PATH" '.derivedGlobs[]? // empty' | tr -d '\r'
    return $?
  fi
  openclaw_local_workspace_policy_derived_globs_awk | tr -d '\r'
}

openclaw_local_workspace_policy_gitignore_patterns() {
  local pattern=''
  while IFS=$'\t' read -r _target_id _rel_path _target_class _cleanup_by_default pattern; do
    [[ -n "$pattern" ]] || continue
    printf '%s\n' "$pattern"
  done < <(openclaw_local_workspace_policy_target_rows)
  openclaw_local_workspace_policy_iter_derived_globs
}

openclaw_local_workspace_policy_bundle_excludes() {
  local rel_path=''
  while IFS=$'\t' read -r _target_id rel_path _target_class _cleanup_by_default _pattern; do
    [[ -n "$rel_path" ]] || continue
    printf '%s/**\n' "${rel_path%/}"
  done < <(openclaw_local_workspace_policy_target_rows)
  openclaw_local_workspace_policy_iter_derived_globs
}

openclaw_local_workspace_policy_expand_glob() {
  local pattern="${1:?pattern is required}"
  local expr="$pattern"
  local match=''
  local rel_path=''
  if [[ "$expr" == */** ]]; then
    expr="${expr%/**}"
  fi
  [[ -n "$expr" ]] || return 0
  (
    shopt -s nullglob dotglob globstar
    for match in "$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR"/$expr; do
      [[ -e "$match" ]] || continue
      rel_path="${match#"$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR"/}"
      rel_path="${rel_path#"$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR"}"
      rel_path="${rel_path#/}"
      rel_path="${rel_path%/}"
      [[ -n "$rel_path" ]] || continue
      printf '%s\n' "${rel_path//\\//}"
    done | LC_ALL=C sort
  )
}

openclaw_local_workspace_policy_find_pruned() {
  (
    cd "$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR" || exit 97
    find . -mindepth 1 \
      \( \
        -path '*/.git' \
        -o -path '*/.venv' \
        -o -path '*/venv' \
        -o -path '*/extension_envs' \
        -o -path '*/wheelhouse/extensions' \
        -o -path '*/pip-cache' \
        -o -path '*/__pycache__' \
        -o -path '*/.pytest_cache' \
        -o -path '*/.mypy_cache' \
        -o -path '*/.ruff_cache' \
        -o -path '*/.nox' \
        -o -path '*/.tox' \
        -o -path '*/.cache' \
        -o -path '*/htmlcov' \
        -o -path '*/dist' \
        -o -path '*/build' \
      \) -print -prune -o -print
  )
}

openclaw_local_workspace_policy_find_recursive_suffix_matches() {
  (( $# > 0 )) || return 0
  (
    cd "$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR" || exit 97
    local suffix=''
    local -a match_args=()
    local first=1
    for suffix in "$@"; do
      [[ -n "$suffix" ]] || continue
      if (( first == 0 )); then
        match_args+=('-o')
      fi
      first=0
      if [[ "$suffix" == */* ]]; then
        match_args+=('-path' "./$suffix" '-o' '-path' "*/$suffix")
      else
        match_args+=('-name' "$suffix")
      fi
    done
    (( ${#match_args[@]} > 0 )) || return 0
    find . -mindepth 1 \
      \( \( "${match_args[@]}" \) -print -prune \) -o \
      \( \
        -path '*/.git' \
        -o -path '*/.venv' \
        -o -path '*/venv' \
        -o -path '*/extension_envs' \
        -o -path '*/wheelhouse/extensions' \
        -o -path '*/pip-cache' \
        -o -path '*/__pycache__' \
        -o -path '*/.pytest_cache' \
        -o -path '*/.mypy_cache' \
        -o -path '*/.ruff_cache' \
        -o -path '*/.nox' \
        -o -path '*/.tox' \
        -o -path '*/.cache' \
        -o -path '*/htmlcov' \
        -o -path '*/dist' \
        -o -path '*/build' \
      \) -prune -o -false
  )
}

openclaw_local_workspace_policy_derived_residue_paths() {
  local pattern=''
  local expr=''
  local suffix=''
  local -a recursive_suffixes=()
  local -a direct_patterns=()
  while IFS= read -r pattern; do
    pattern="${pattern%$'\r'}"
    [[ -n "$pattern" ]] || continue
    expr="$pattern"
    if [[ "$expr" == */** ]]; then
      expr="${expr%/**}"
    fi
    if [[ "$expr" == \*\*/* ]]; then
      suffix="${expr#**/}"
      [[ -n "$suffix" ]] && recursive_suffixes+=("$suffix")
    else
      direct_patterns+=("$pattern")
    fi
  done < <(openclaw_local_workspace_policy_iter_derived_globs)

  {
    if (( ${#recursive_suffixes[@]} > 0 )); then
      openclaw_local_workspace_policy_find_recursive_suffix_matches "${recursive_suffixes[@]}" | while IFS= read -r rel_path; do
        rel_path="${rel_path#./}"
        [[ -n "$rel_path" ]] || continue
        printf '%s\n' "${rel_path//\\//}"
      done
    fi

    for pattern in "${direct_patterns[@]}"; do
      openclaw_local_workspace_policy_expand_glob "$pattern"
    done
  } | LC_ALL=C sort -u
}

openclaw_local_workspace_policy_disposable_paths() {
  openclaw_local_workspace_policy_require_runtime || return $?
  local cleanup_target=''
  local policy_target=''
  local rel_path=''
  local -a policy_targets=()
  local -a cleanup_targets=()
  local cleanup_by_default=''
  while IFS=$'\t' read -r _target_id rel_path _target_class cleanup_by_default _gitignore_pattern; do
    rel_path="${rel_path%$'\r'}"
    cleanup_by_default="${cleanup_by_default%$'\r'}"
    [[ -n "$rel_path" ]] || continue
    policy_targets+=("$rel_path")
    [[ "$cleanup_by_default" == 'yes' ]] && cleanup_targets+=("$rel_path")
  done < <(openclaw_local_workspace_policy_target_rows)

  for cleanup_target in "${cleanup_targets[@]}"; do
    if [[ -e "$OPENCLAW_LOCAL_WORKSPACE_POLICY_ROOT_DIR/$cleanup_target" ]]; then
      printf '%s\n' "$cleanup_target"
    fi
  done

  while IFS= read -r rel_path; do
    rel_path="${rel_path%$'\r'}"
    [[ -n "$rel_path" ]] || continue
    local covered=0
    for policy_target in "${policy_targets[@]}"; do
      if [[ "$rel_path" == "$policy_target" || "$rel_path" == "$policy_target/"* ]]; then
        covered=1
        break
      fi
    done
    (( covered == 0 )) || continue
    printf '%s\n' "$rel_path"
  done < <(openclaw_local_workspace_policy_derived_residue_paths)
}
