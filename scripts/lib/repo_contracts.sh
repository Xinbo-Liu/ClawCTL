#!/usr/bin/env bash
# 用途：统一桥接 repo contract id 到共享 JSON 真源，避免 shell 脚本各自拼接 repo-tracked 真源路径。
set -euo pipefail

__openclaw_repo_contracts_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$__openclaw_repo_contracts_lib_dir/repo_root.sh"
__openclaw_repo_contracts_root="$(openclaw_repo_root_from "$__openclaw_repo_contracts_lib_dir")"
if [[ "${OPENCLAW_REPO_CONTRACTS_SH_LOADED_ROOT:-}" == "$__openclaw_repo_contracts_root" ]]; then
  return 0 2>/dev/null || exit 0
fi

repo_contracts_shell_path() {
  local raw_path="${1-}"
  if command -v cygpath >/dev/null 2>&1 && [[ "$raw_path" =~ ^[A-Za-z]:[\\/].*$ ]]; then
    cygpath -u "$raw_path"
    return 0
  fi
  printf '%s\n' "$raw_path"
}

OPENCLAW_REPO_CONTRACTS_SH_LOADED_ROOT="$__openclaw_repo_contracts_root"
OPENCLAW_REPO_CONTRACTS_ROOT_DIR="$(repo_contracts_shell_path "$__openclaw_repo_contracts_root")"
unset __openclaw_repo_contracts_lib_dir __openclaw_repo_contracts_root

OPENCLAW_REPO_CONTRACTS_TRUTH_REL_PATH='config/governance/support/repo_contracts.json'
OPENCLAW_REPO_CONTRACTS_CACHE_LOADED_ROOT=''
declare -gA OPENCLAW_REPO_CONTRACT_REL_PATHS=()
declare -gA OPENCLAW_REPO_CONTRACT_FORMATS=()

repo_contracts_truth_path() {
  printf '%s/%s\n' "$OPENCLAW_REPO_CONTRACTS_ROOT_DIR" "$OPENCLAW_REPO_CONTRACTS_TRUTH_REL_PATH"
}

repo_contracts_trim_cr() {
  local value="${1-}"
  printf '%s' "${value%$'\r'}"
}

repo_contracts_trim_text() {
  local value=''
  value="$(repo_contracts_trim_cr "${1-}")"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

repo_contracts_require_jq() {
  command -v jq >/dev/null 2>&1 && return 0
  echo '[repo_contracts][FAIL] missing jq' >&2
  return 127
}

repo_contracts_shell_quote() {
  local value="${1-}"
  printf '%q' "$value"
}

repo_contracts_truth_records_awk() {
  local truth_path="$1"
  awk '
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
    function fail(message) {
      print "[repo_contracts][FAIL] " message > "/dev/stderr"
      exit 97
    }
    function emit_entry(    id_trimmed, rel_trimmed, format_trimmed) {
      id_trimmed = trim(id)
      rel_trimmed = trim(relative_path)
      format_trimmed = trim(format)
      if (id_trimmed == "" || rel_trimmed == "" || format_trimmed == "") {
        fail("repo contracts truth entry " entry_index " is invalid")
      }
      print id_trimmed "\t" rel_trimmed "\t" format_trimmed
      emitted += 1
    }
    /^[[:space:]]*(#.*)?$/ { next }
    {
      line = $0
      sub(/\r$/, "", line)
    }
    line ~ /"contracts"[[:space:]]*:[[:space:]]*\[/ {
      in_contracts = 1
      next
    }
    !in_contracts { next }
    line ~ /^[[:space:]]*\]/ {
      in_contracts = 0
      next
    }
    line ~ /^[[:space:]]*\{/ {
      in_entry = 1
      entry_index += 1
      id = ""
      relative_path = ""
      format = ""
      next
    }
    !in_entry { next }
    {
      value = json_string_value(line, "id")
      if (value != "") {
        id = value
      }
      value = json_string_value(line, "relative_path")
      if (value != "") {
        relative_path = value
      }
      value = json_string_value(line, "format")
      if (value != "") {
        format = value
      }
    }
    line ~ /^[[:space:]]*\}[[:space:]]*,?[[:space:]]*$/ {
      emit_entry()
      in_entry = 0
      next
    }
    END {
      if (emitted == 0) {
        print "[repo_contracts][FAIL] repo contracts truth 为空" > "/dev/stderr"
        exit 97
      }
    }
  ' "$truth_path"
}

repo_contracts_truth_assignments() {
  local truth_path='' output=''
  truth_path="$(repo_contracts_truth_path)"
  [[ -f "$truth_path" ]] || {
    echo "[repo_contracts][FAIL] missing truth file: $truth_path" >&2
    return 97
  }
  if [[ "${OPENCLAW_REPO_CONTRACTS_FORCE_AWK:-0}" != "1" ]] && command -v jq >/dev/null 2>&1; then
    if ! output="$(jq -erRs '
    def trim: gsub("^\\s+|\\s+$"; "");
    def load_truth:
      sub("^\uFEFF"; "")
      | split("\n")
      | map(sub("\r$"; ""))
      | map(select(test("^\\s*(#.*)?$") | not))
      | if length == 0 then error("repo contracts truth is empty") else join("\n") end
      | fromjson;
    def validate_entry($entry_index):
      if type != "object" then
        error("repo contracts truth contract 项必须为对象")
      else
        .
      end
      | if ((keys_unsorted - ["id", "relative_path", "format"]) | length) != 0 then
          error("repo contracts truth entry \($entry_index) is invalid")
        else
          .
        end
      | {
          id: ((.id // "") | tostring | trim),
          relative_path: ((.relative_path // "") | tostring | trim | gsub("\\\\"; "/")),
          format: ((.format // "") | tostring | trim)
        }
      | if (.id == "" or .relative_path == "" or .format == "") then
          error("repo contracts truth entry \($entry_index) is invalid")
        else
          .
        end
      | if (.format == "json" or .format == "env" or .format == "text") then
          .
        else
          error("repo contract \(.id) 使用了不支持的 format：\(.format)")
        end
      | if ((.relative_path | test("^(/|[A-Za-z]:[/\\\\])")) or (.relative_path | test("(^|/)\\.\\.(/|$)"))) then
          error("repo contract \(.id) relative_path 非法：\(.relative_path)")
        else
          .
        end;

    load_truth as $truth
    | if ($truth | type) != "object" then error("repo contracts truth 顶层必须为对象") else $truth end
    | ($truth.contracts // error("repo contracts truth 缺少 contracts")) as $contracts
    | if ($contracts | type) != "array" then error("repo contracts truth contracts 必须为数组") else $contracts end
    | if ($contracts | length) == 0 then error("repo contracts truth 为空") else $contracts end
    | ($contracts | to_entries | map(.value | validate_entry(.key + 1))) as $entries
    | ($entries | group_by(.id) | map(select(length > 1) | .[0].id)) as $duplicate_ids
    | if ($duplicate_ids | length) > 0 then
        error("duplicate repo contract id: \($duplicate_ids[0])")
      else
        $entries[]
      end
    | "OPENCLAW_REPO_CONTRACT_REL_PATHS[\(.id | @sh)]=\(.relative_path | @sh)\nOPENCLAW_REPO_CONTRACT_FORMATS[\(.id | @sh)]=\(.format | @sh)"
  ' < "$truth_path" 2>&1)"; then
      output="${output//$'\n'/ }"
      output="$(repo_contracts_trim_text "$output")"
      output="${output#jq: error (at $truth_path:0): }"
      output="${output#jq: error (at $truth_path:1): }"
      echo "[repo_contracts][FAIL] ${output:-repo contracts truth JSON 无法解析}" >&2
      return 97
    fi
    printf '%s\n' "$output"
    return 0
  fi

  local awk_output='' line='' id='' relative_path='' format=''
  local quoted_id='' quoted_relative_path='' quoted_format=''
  local record_count=0
  local -A seen_ids=()
  if ! awk_output="$(repo_contracts_truth_records_awk "$truth_path")"; then
    return 97
  fi
  while IFS=$'\t' read -r id relative_path format; do
    [[ -n "$id$relative_path$format" ]] || continue
    record_count=$((record_count + 1))
    id="$(repo_contracts_trim_text "$id")"
    relative_path="$(repo_contracts_trim_text "$relative_path")"
    relative_path="${relative_path//\\//}"
    format="$(repo_contracts_trim_text "$format")"
    [[ -n "$id" && -n "$relative_path" && -n "$format" ]] || {
      echo '[repo_contracts][FAIL] repo contracts truth entry is invalid' >&2
      return 97
    }
    [[ -z "${seen_ids[$id]:-}" ]] || {
      echo "[repo_contracts][FAIL] duplicate repo contract id: $id" >&2
      return 97
    }
    seen_ids["$id"]=1
    case "$format" in
      json|env|text) ;;
      *)
        echo "[repo_contracts][FAIL] repo contract $id 使用了不支持的 format：$format" >&2
        return 97
        ;;
    esac
    if [[ "$relative_path" == /* || "$relative_path" =~ ^[A-Za-z]:[/\\] || "$relative_path" == *"/../"* || "$relative_path" == ../* || "$relative_path" == *"/.." ]]; then
      echo "[repo_contracts][FAIL] repo contract $id relative_path 非法：$relative_path" >&2
      return 97
    fi
    quoted_id="$(repo_contracts_shell_quote "$id")"
    quoted_relative_path="$(repo_contracts_shell_quote "$relative_path")"
    quoted_format="$(repo_contracts_shell_quote "$format")"
    printf 'OPENCLAW_REPO_CONTRACT_REL_PATHS[%s]=%s\nOPENCLAW_REPO_CONTRACT_FORMATS[%s]=%s\n' \
      "$quoted_id" "$quoted_relative_path" "$quoted_id" "$quoted_format"
  done <<< "$awk_output"
  ((record_count > 0)) || {
    echo '[repo_contracts][FAIL] repo contracts truth 为空' >&2
    return 97
  }
}

repo_contracts_load_cache() {
  local assignments=''
  if [[ "${OPENCLAW_REPO_CONTRACTS_CACHE_LOADED_ROOT:-}" == "$OPENCLAW_REPO_CONTRACTS_ROOT_DIR" ]]; then
    return 0
  fi
  if assignments="$(repo_contracts_truth_assignments)"; then
    :
  else
    return $?
  fi

  OPENCLAW_REPO_CONTRACT_REL_PATHS=()
  OPENCLAW_REPO_CONTRACT_FORMATS=()
  eval "$assignments"

  [[ "${#OPENCLAW_REPO_CONTRACT_REL_PATHS[@]}" -gt 0 ]] || {
    echo "[repo_contracts][FAIL] repo contracts truth is empty: $(repo_contracts_truth_path)" >&2
    return 97
  }
  OPENCLAW_REPO_CONTRACTS_CACHE_LOADED_ROOT="$OPENCLAW_REPO_CONTRACTS_ROOT_DIR"
}

repo_contract_validate_var_name() {
  local target_var="${1-}"
  [[ "$target_var" != __openclaw_repo_contract_* ]] || {
    echo "[repo_contracts][FAIL] 变量名前缀受保留保护：$target_var" >&2
    return 97
  }
  [[ "$target_var" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
    echo "[repo_contracts][FAIL] 非法变量名：${target_var:-<empty>}" >&2
    return 97
  }
}

repo_contract_assign_relpath() {
  local __openclaw_repo_contract_id="$2"
  local __openclaw_repo_contract_rel_path=''
  repo_contract_validate_var_name "${1-}" || return $?
  repo_contracts_load_cache || return $?
  __openclaw_repo_contract_rel_path="${OPENCLAW_REPO_CONTRACT_REL_PATHS[$__openclaw_repo_contract_id]:-}"
  [[ -n "$__openclaw_repo_contract_rel_path" ]] || {
    echo "[repo_contracts][FAIL] 未知 repo contract id：$__openclaw_repo_contract_id" >&2
    return 2
  }
  printf -v "$1" '%s' "$__openclaw_repo_contract_rel_path"
}

repo_contract_assign_path() {
  local __openclaw_repo_contract_id="$2"
  local __openclaw_repo_contract_rel_path=''
  repo_contract_validate_var_name "${1-}" || return $?
  repo_contracts_load_cache || return $?
  __openclaw_repo_contract_rel_path="${OPENCLAW_REPO_CONTRACT_REL_PATHS[$__openclaw_repo_contract_id]:-}"
  [[ -n "$__openclaw_repo_contract_rel_path" ]] || {
    echo "[repo_contracts][FAIL] 未知 repo contract id：$__openclaw_repo_contract_id" >&2
    return 2
  }
  printf -v "$1" '%s' "$OPENCLAW_REPO_CONTRACTS_ROOT_DIR/$__openclaw_repo_contract_rel_path"
}

repo_contract_default_relpath() {
  local target_var="$1"
  local contract_id="$2"
  repo_contract_validate_var_name "$target_var" || return $?
  [[ -n "${!target_var:-}" ]] && return 0
  repo_contract_assign_relpath "$target_var" "$contract_id"
}

repo_contract_default_path() {
  local target_var="$1"
  local contract_id="$2"
  repo_contract_validate_var_name "$target_var" || return $?
  [[ -n "${!target_var:-}" ]] && return 0
  repo_contract_assign_path "$target_var" "$contract_id"
}

repo_contract_relpath() {
  local rel_path=''
  repo_contract_assign_relpath rel_path "$1" || return $?
  printf '%s\n' "$rel_path"
}

repo_contract_path() {
  local abs_path=''
  repo_contract_assign_path abs_path "$1" || return $?
  printf '%s\n' "$abs_path"
}
