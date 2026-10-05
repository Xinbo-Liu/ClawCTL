#!/usr/bin/env bash
# 用途：为静态容器入口准备同一份受管文档清单参数，显式 BOM 优先于 Git 快照。

if [[ -n "${OPENCLAW_DOCS_INVENTORY_ENV_SH_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
OPENCLAW_DOCS_INVENTORY_ENV_SH_LOADED=1
OPENCLAW_DOCS_INVENTORY_RUNNER_ARGS=()

# 职责：规范化仓库/BOM 路径，捕获或核对 Git NUL 清单，并组装静态容器的 env/mount 参数。
# 输入：现存仓库根目录；可选 BOM，或继承的 B64 清单及其仓库 ROOT 绑定。
# 输出：OPENCLAW_DOCS_INVENTORY_RUNNER_ARGS 数组；非法输入返回 2。
# 副作用：只读路径及 Git 元信息；不读取部署 env、文档正文或运行态内容。
openclaw_docs_inventory_prepare_runner_args() {
  local requested_root="${1:-}"
  local root_dir="" bom_path="" bom_parent="" snapshot="" snapshot_root=""
  OPENCLAW_DOCS_INVENTORY_RUNNER_ARGS=()
  if [[ -z "$requested_root" ]] || ! root_dir="$(cd -- "$requested_root" && pwd -P)"; then
    echo '[docs_inventory_env][FAIL] 文档清单的仓库根目录不存在。' >&2
    return 2
  fi

  if [[ -n "${OPENCLAW_DOCS_INVENTORY_BOM_PATH:-}" ]]; then
    bom_path="$OPENCLAW_DOCS_INVENTORY_BOM_PATH"
    [[ "$bom_path" = /* ]] || bom_path="$PWD/$bom_path"
    if [[ ! -f "$bom_path" ]] || ! bom_parent="$(cd -- "$(dirname -- "$bom_path")" && pwd -P)"; then
      echo '[docs_inventory_env][FAIL] 显式文档 BOM 不是现存文件。' >&2
      return 2
    fi
    bom_path="$bom_parent/$(basename -- "$bom_path")"
    OPENCLAW_DOCS_INVENTORY_RUNNER_ARGS=(
      --mount "$bom_path"
      --env "OPENCLAW_DOCS_INVENTORY_BOM_PATH=$bom_path"
      --env 'OPENCLAW_DOCS_TRACKED_FILES_B64='
      --env 'OPENCLAW_DOCS_TRACKED_FILES_ROOT='
    )
    return 0
  fi

  if [[ -e "$root_dir/.git" ]] && command -v git >/dev/null 2>&1; then
    # NUL 保留空格、中文和换行；icase 与 Python 的 .md 大小写合同一致。
    if ! snapshot="$(set -o pipefail; git -C "$root_dir" ls-files -z --cached --others --exclude-standard -- ':(icase)*.md' | base64 | tr -d '\r\n')"; then
      echo '[docs_inventory_env][FAIL] 当前仓库的 Git 文档清单读取失败。' >&2
      return 2
    fi
  elif [[ -n "${OPENCLAW_DOCS_TRACKED_FILES_B64+x}" || -n "${OPENCLAW_DOCS_TRACKED_FILES_ROOT:-}" ]]; then
    if [[ -z "${OPENCLAW_DOCS_TRACKED_FILES_B64+x}" || -z "${OPENCLAW_DOCS_TRACKED_FILES_ROOT:-}" ]]; then
      echo '[docs_inventory_env][FAIL] 继承的 Git 文档清单必须同时声明 B64 和仓库 ROOT 绑定。' >&2
      return 2
    fi
    if ! snapshot_root="$(cd -- "$OPENCLAW_DOCS_TRACKED_FILES_ROOT" && pwd -P)" || [[ "$snapshot_root" != "$root_dir" ]]; then
      echo '[docs_inventory_env][FAIL] Git 文档清单属于另一仓库，拒绝复用。' >&2
      return 2
    fi
    snapshot="$OPENCLAW_DOCS_TRACKED_FILES_B64"
  else
    # 未选择清单时，交由 Python inventory 检查 Git/BOM 输入并报告错误。
    return 0
  fi
  OPENCLAW_DOCS_INVENTORY_RUNNER_ARGS=(
    --env "OPENCLAW_DOCS_TRACKED_FILES_B64=$snapshot"
    --env "OPENCLAW_DOCS_TRACKED_FILES_ROOT=$root_dir"
  )
}
