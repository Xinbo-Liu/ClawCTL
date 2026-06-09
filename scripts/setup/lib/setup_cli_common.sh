#!/usr/bin/env bash
# 用途：复用 setup 类脚本中与具体业务无关的轻量 CLI/JSON/随机值辅助函数。
set -euo pipefail

setup_cli_log() {
  local prefix="$1"
  local quiet="${2:-0}"
  shift 2 || true
  [[ "$quiet" == "1" ]] && return 0
  echo "[$prefix] $*"
}

setup_cli_fail() {
  local prefix="$1"
  shift
  echo "[$prefix][FAIL] $*" >&2
  exit 2
}

setup_cli_json_escape() {
  local s="$1"
  s=${s//\\/\\\\}
  s=${s//"/\\"}
  s=${s//$'\n'/\\n}
  printf '%s' "$s"
}

setup_cli_json_array() {
  local first=1 item
  printf '['
  for item in "$@"; do
    [[ $first -eq 1 ]] || printf ','
    first=0
    printf '"%s"' "$(setup_cli_json_escape "$item")"
  done
  printf ']'
}

setup_cli_bool_str() {
  case "$1" in
    true|1|yes|on) printf 'true' ;;
    false|0|no|off|'') printf 'false' ;;
    *) printf '%s' "$1" ;;
  esac
}

setup_cli_random_token() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
    return 0
  fi
  od -An -N24 -tx1 /dev/urandom | tr -d ' \n'
}



SETUP_CLI_COMMON_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$SETUP_CLI_COMMON_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$SETUP_CLI_COMMON_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
SETUP_CLI_COMMON_ROOT="$(openclaw_repo_root_from "$SETUP_CLI_COMMON_LIB_DIR")"
# shellcheck source=scripts/lib/repo_contracts.sh
source "$SETUP_CLI_COMMON_ROOT/scripts/lib/repo_contracts.sh"
unset SETUP_CLI_COMMON_LIB_DIR
repo_contract_assign_path SETUP_CLI_SETUP_ENTRYPOINTS_PATH governance.setup_entrypoints
repo_contract_assign_path SETUP_CLI_DEPLOY_BASELINE_PATH governance.default_deployment_flow
repo_contract_assign_path SETUP_CLI_DEPLOY_STAGE_PATH governance.deploy_stage_flow
repo_contract_assign_path SETUP_CLI_FULL_TEST_MANIFEST_PATH runtime.testing_manifest
repo_contract_assign_path SETUP_CLI_SETUP_FOLLOWUP_PATH governance.setup_followups
repo_contract_assign_path SETUP_CLI_CONTROL_PLANE_MEDIUM_PATH setup.control_plane_medium
repo_contract_assign_path SETUP_CLI_DOCKER_HOST_SUPPORT_ENTRYPOINT_PATH governance.docker_host_support
repo_contract_assign_relpath SETUP_CLI_FULL_TEST_MANIFEST_REL_PATH runtime.testing_manifest

setup_cli_truth_scalar() {
  local file="$1"
  local jq_expr="$2"
  local py_path="$3"
  if setup_cli_jq_available && [[ -f "$file" ]]; then
    jq -r "${jq_expr} // empty" "$file" 2>/dev/null || true
    return 0
  fi
}

setup_cli_truth_lines() {
  local file="$1"
  local jq_expr="$2"
  local py_path="$3"
  if setup_cli_jq_available && [[ -f "$file" ]]; then
    jq -r "${jq_expr}[]?" "$file" 2>/dev/null | sed 's/\r$//' || true
    return 0
  fi
}

setup_help_surface_guarantee_text() {
  local lines=''
  lines="$(setup_cli_truth_lines "$SETUP_CLI_SETUP_ENTRYPOINTS_PATH" '.help_surface_contract.guarantees' 'help_surface_contract.guarantees')"
  if [[ -n "$lines" ]]; then
    echo '帮助面保证：'
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      echo "  - $line"
    done <<< "$lines"
    return 0
  fi
  cat <<'EOF2'
帮助面保证：
  - --help / --explain / 未知参数 必须优先输出可阅读帮助，不得因为 Docker、Docker daemon 或控制面镜像未就绪而阻塞帮助面；
  - 动态帮助面可用时，只允许展示由控制面真源派生的阶段映射、固定路径与默认入口；动态帮助面不可用时，必须使用静态说明；
  - 帮助面只用于确认职责边界、阶段映射、常用变体与固定参考页；完整步骤以 quickstart.md、runtime-service-reference.md 与 troubleshooting.md 为准。
EOF2
}

setup_help_surface_reference_text() {
  local lines=''
  lines="$(setup_cli_truth_lines "$SETUP_CLI_SETUP_ENTRYPOINTS_PATH" '.help_surface_contract.references' 'help_surface_contract.references')"
  if [[ -n "$lines" ]]; then
    echo '统一参考：'
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      echo "  - $line"
    done <<< "$lines"
    return 0
  fi
  cat <<'EOF2'
统一参考：
  - 入口职责边界、默认主路径、帮助面与执行面边界统一查看 docs/getting-started/quickstart.md
  - 成功后的默认衔接动作统一查看 docs/getting-started/quickstart.md
  - basic gate / full test 失败后的默认修复入口统一查看 docs/operations/troubleshooting.md
EOF2
}

setup_cli_control_plane_medium_scalar_from_truth() {
  local jq_expr="$1"
  local py_path="$2"
  setup_cli_truth_scalar "$SETUP_CLI_CONTROL_PLANE_MEDIUM_PATH" "$jq_expr" "$py_path"
}

setup_cli_control_plane_medium_lines_from_truth() {
  local jq_expr="$1"
  local py_path="$2"
  setup_cli_truth_lines "$SETUP_CLI_CONTROL_PLANE_MEDIUM_PATH" "$jq_expr" "$py_path"
}

setup_cli_docker_host_scalar_from_truth() {
  local jq_expr="$1"
  local py_path="$2"
  setup_cli_truth_scalar "$SETUP_CLI_DOCKER_HOST_SUPPORT_ENTRYPOINT_PATH" "$jq_expr" "$py_path"
}

setup_cli_docker_host_lines_from_truth() {
  local jq_expr="$1"
  local py_path="$2"
  setup_cli_truth_lines "$SETUP_CLI_DOCKER_HOST_SUPPORT_ENTRYPOINT_PATH" "$jq_expr" "$py_path"
}

setup_cli_jq_available() {
  command -v jq >/dev/null 2>&1
}

setup_cli_deploy_stage_order_from_truth() {
  local mode="${DEPLOY_MODE:-online}"
  local release_check="${RUN_RELEASE_CHECK:-1}"
  local browser_verify="${RUN_BROWSER_VERIFY:-1}"
  local start_services="${START_SERVICES:-1}"
  if setup_cli_jq_available && [[ -f "$SETUP_CLI_DEPLOY_BASELINE_PATH" ]]; then
    jq -r --arg mode "$mode" --arg releaseCheck "$release_check" --arg browserVerify "$browser_verify" --arg startServices "$start_services" '
      .deploy_flow.stage_order as $order |
      ($order.common // [])[]?,
      (if $mode == "online" then
         (if $releaseCheck == "1" then ($order.online[0:1] // [])[]? else empty end),
         ($order.online[1:] // [])[]?
       else
         ($order.offline // [])[]?
       end),
      ($order.image_contract // empty),
      ($order.compose_contract // empty),
      (if $browserVerify == "1" then ($order.browser_verify // empty) else empty end),
      ($order.compose_config // empty),
      (if $startServices == "1" then ($order.service // [])[]? else empty end)
    ' "$SETUP_CLI_DEPLOY_BASELINE_PATH" 2>/dev/null || true
    return 0
  fi
}

setup_cli_deploy_stage_label_from_truth() {
  local stage="$1"
  if setup_cli_jq_available && [[ -f "$SETUP_CLI_DEPLOY_STAGE_PATH" ]]; then
    jq -r --arg stage "$stage" '.stages[$stage].explain_label // empty' "$SETUP_CLI_DEPLOY_STAGE_PATH" 2>/dev/null || true
    return 0
  fi
}

setup_cli_post_deploy_command_from_truth() {
  if setup_cli_jq_available && [[ -f "$SETUP_CLI_DEPLOY_BASELINE_PATH" ]]; then
    jq -r '
      .entry_relations.post_deploy_default_command //
      (.entry_relations.post_deploy_default_entry_id as $post |
        .default_flow.steps[] | select(.entry_id == $post) | .command) // empty
    ' "$SETUP_CLI_DEPLOY_BASELINE_PATH" 2>/dev/null || true
    return 0
  fi
}

setup_cli_followup_doc_from_truth() {
  if setup_cli_jq_available && [[ -f "$SETUP_CLI_SETUP_FOLLOWUP_PATH" ]]; then
    jq -r '.generated_artifacts.setup_followup_doc // empty' "$SETUP_CLI_SETUP_FOLLOWUP_PATH" 2>/dev/null || true
    return 0
  fi
}

setup_cli_full_test_group_order_from_truth() {
  if setup_cli_jq_available && [[ -f "$SETUP_CLI_FULL_TEST_MANIFEST_PATH" ]]; then
    jq -r '.execution_order[]?' "$SETUP_CLI_FULL_TEST_MANIFEST_PATH" 2>/dev/null | sed 's/\r$//' || true
    return 0
  fi
}

setup_cli_full_test_valid_groups_from_truth() {
  if setup_cli_jq_available && [[ -f "$SETUP_CLI_FULL_TEST_MANIFEST_PATH" ]]; then
    jq -r '.valid_groups[]?' "$SETUP_CLI_FULL_TEST_MANIFEST_PATH" 2>/dev/null | sed 's/\r$//' || true
    return 0
  fi
}

setup_cli_join_lines() {
  local separator="$1"
  local fallback="$2"
  local joined='' line=''
  while IFS= read -r line; do
    line="${line%$'\r'}"
    [[ -n "$line" ]] || continue
    if [[ -n "$joined" ]]; then
      joined+="$separator$line"
    else
      joined="$line"
    fi
  done
  [[ -n "$joined" ]] && printf '%s\n' "$joined" || printf '%s\n' "$fallback"
}

setup_cli_full_test_group_order_text() {
  setup_cli_full_test_group_order_from_truth | setup_cli_join_lines ' -> ' 'service'
}

setup_cli_full_test_valid_groups_text() {
  setup_cli_full_test_valid_groups_from_truth | setup_cli_join_lines '/' 'all/service'
}


# 用途：集中维护 one_click_deploy / one_click_test_basic / one_click_test_full 的静态 help / explain 文案。

deploy_flow_static_help_text() {
  cat <<'EOF2'
用法：
  bash ./scripts/setup/one_click_deploy.sh [选项]

默认行为（在线模式）：
  前置门禁：当前入口会校验同一 env/mode 的 latest basic gate proof；若 proof 缺失或已过期，默认自动补跑 one_click_test_basic.sh
  部署闭环：runtime 服务启动后，若当前 profile/extension 声明 required run ledger jobs，会先受控执行 run_control_plane_run_all_once.sh，再自动执行 one_click_test_full.sh 并导出 runtime acceptance evidence
  实际执行：通过统一容器化 Python 控制面进入 Docker；若缺少 Docker / Docker daemon / docker compose，或当前用户无权访问 Docker daemon（如 /var/run/docker.sock 权限不足），主路径会失败；部署摘要由控制面统一写出
  用户边界：正式部署主链拒绝 root 执行；root 仅用于 prepare_docker_host、prepare_deploy_user、apply_ingress_boundary_rules、fix_permissions 等宿主机步骤
  权限前置：真正进入控制面前，会先检查仓库 / deploy / state 路径的读写执行权限
  权限边界：当前脚本不会自动 sudo；以 root 执行 bootstrap/fix_permissions 时必须能解析 OPENCLAW_RUNTIME_UID/GID，解析失败会中止；deploy/.env、deploy/site.env、启用扩展内部 agent/extensions/<extension-id>/deploy/extension.env、deploy/targets.d、state/ 与当前 host state root（默认值由 runtime_paths 真源派生）必须由当前部署用户可管理
  用户真源：runtime 服务用户固定取 deploy/.env 中的 OPENCLAW_RUNTIME_UID / OPENCLAW_RUNTIME_GID；默认应与当前部署用户一致
  默认部署阶段由 deploy flow 控制面派生

离线模式：
  - 不执行 release 检查与在线 pull；
  - 从 OpenClaw deployment image bundle 执行 docker load；deployment_images_*.tar 必须覆盖 source_strategy 声明的部署镜像合同角色；
  - 默认会从 state/image_artifacts/ 自动选择最新归档；归档在继续执行前会先按当前 pin 校验部署镜像合同与 compose 运行镜像集合未漂移，也可显式传入路径。

可选项：
  --offline                      进入离线模式
  --image-archive <path>         指定 deployment_images_*.tar；仅 --offline 下有效
  --env-file <path>              覆盖默认 deploy/.env；必须与 latest basic gate proof 使用同一文件
  --prepare-only                 只执行准备阶段，不启动 runtime 服务
  --resume-from <stage>          从指定阶段继续执行；会跳过它之前的阶段
                                post_deploy_acceptance 执行 required run ledger jobs、full test 与 runtime evidence；发送动作按当前 target 配置执行
                                post_deploy_full_acceptance 执行范围限定为 full test 与 runtime evidence，跳过 run_all_once
                                后置验收 resume 不能与 --prepare-only 或 --skip-acceptance 同用
  --explain                      只打印一键入口与分阶段手工命令映射，不执行任何动作
  --skip-release-check           跳过 OpenClaw 上游版本检查
  --strict-release-check         使用 strict_release 策略；上游 latest 高于当前 pin 时阻断
  --skip-browser-verify          跳过浏览器能力校验
  --require-basic-gate-proof     不自动补跑 basic gate；latest proof 缺失或过期时直接失败
  --skip-acceptance              跳过部署后 full test 与 runtime evidence 导出；deployment acceptance 保持未闭合
  -h, --help                     显示帮助

示例：
  bash ./scripts/setup/one_click_deploy.sh
  bash ./scripts/setup/one_click_deploy.sh --prepare-only
  bash ./scripts/setup/one_click_deploy.sh --resume-from docker_compose_up
  bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance
  bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance
  bash ./scripts/setup/one_click_deploy.sh --offline --image-archive <local-path>
EOF2
  setup_help_surface_guarantee_text
  setup_help_surface_reference_text
}

deploy_flow_static_stage_order() {
  local lines=''
  lines="$(setup_cli_deploy_stage_order_from_truth)"
  [[ -n "$lines" ]] && printf '%s
' "$lines"
}

deploy_flow_static_explain_label() {
  local label=''
  label="$(setup_cli_deploy_stage_label_from_truth "$1")"
  [[ -n "$label" ]] && printf '%s
' "$label" || printf '%s
' "$1"
}

deploy_flow_static_explain_text() {
  local index=0
  local stage=''
  local post_deploy_command='bash ./scripts/setup/one_click_test_full.sh'
  local followup_doc='docs/getting-started/quickstart.md'
  post_deploy_command="$(setup_cli_post_deploy_command_from_truth)"
  [[ -n "$post_deploy_command" ]] || post_deploy_command='bash ./scripts/setup/one_click_test_full.sh'
  followup_doc="$(setup_cli_followup_doc_from_truth)"
  [[ -n "$followup_doc" ]] || followup_doc='docs/getting-started/quickstart.md'
  echo 'one_click_deploy 默认阶段映射'
  echo
  if [[ "${DEPLOY_MODE:-online}" == 'online' ]]; then
    echo '在线模式：'
    echo '  前置门禁：若 latest basic gate proof 与当前 env/mode 不匹配，默认自动补跑 one_click_test_basic.sh'
  else
    echo '离线模式（--offline）：'
    echo '  前置门禁：若 latest basic gate proof 与当前 env/mode/image archive 不匹配，默认自动补跑 one_click_test_basic.sh --offline'
  fi
  while IFS= read -r stage; do
    [[ -n "$stage" ]] || continue
    index=$((index + 1))
    printf ' %s. %s
' "$index" "$(deploy_flow_static_explain_label "$stage")"
  done < <(deploy_flow_static_stage_order)
  cat <<EOF2

部署成功后的默认 follow-up：
  - 统一查看 \`${followup_doc}\`；
  - deployment acceptance 默认顺序、通过标准与证据产物统一看 \`docs/operations/runtime-service-reference.md\`；
  - 若当前 profile / extension 声明 required_run_ledger_jobs，默认部署链会先执行 run_all_once，再执行 \`${post_deploy_command}\` 并导出 runtime acceptance evidence；
  - 服务已启动但 required run ledger jobs 缺失或失败时，使用 --resume-from post_deploy_acceptance 执行 required jobs、full test 与 runtime evidence；发送动作按当前 target 配置执行；
  - required run ledger jobs 已 accepted、仅 full test 或 runtime evidence 缺失时，使用 --resume-from post_deploy_full_acceptance 执行 full test 与 runtime evidence，且跳过 run_all_once；
  - 需要快速恢复或只做 compose 修复时，可加 --skip-acceptance；该模式只代表服务启动链执行完成，deployment acceptance 与 runtime evidence 均未闭合；
  - dispatch preflight / send/retry dry-run 只在需要确认调度上线或排查分发问题时再补做。

恢复执行：
  - 可用 --resume-from <stage> 从中间阶段继续执行，例如：
    bash ./scripts/setup/one_click_deploy.sh --resume-from pull_images
    bash ./scripts/setup/one_click_deploy.sh --offline --resume-from load_deployment_images
    bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance
    bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance
EOF2
  if [[ "${START_SERVICES:-1}" != '1' ]]; then
    cat <<'EOF2'

如果使用 --prepare-only：
  - 流程会停在 compose 渲染检查，不执行 runtime 服务启动；不能视为调度已上线。
EOF2
  fi
  echo
  setup_help_surface_guarantee_text
  setup_help_surface_reference_text
}

one_click_test_basic_static_help_text() {
  cat <<'EOF2'
用法：
  bash ./scripts/setup/one_click_test_basic.sh [选项]

说明：
  one_click_test_basic 是默认 one_click 主链中的唯一部署前门禁，只负责：
  1) 检查 deploy/.env 是否存在；
  2) 检查 deploy/.env 中是否仍有 __REQUIRED__ 未完成项；
  3) 执行 run_openclaw_python_tool.sh setup env validate --env-file deploy/.env；
  4) 检查本地文件系统合同（运行态目录、预创建输出目录、脚本执行位）；
  5) 执行 Docker 宿主机 readiness 检查；
  6) 检查部署镜像就绪性（离线模式可附带 deployment_images_*.tar）；
  7) 检查部署镜像覆盖合同；
  8) 检查运行态 compose 合同；
  9) 检查 runtime 服务可写 bind mount 的 UID/GID 合同；
 10) 执行 OpenClaw release 对齐检查；默认 relaxed_install 策略下，当前 pin digest 可验证但 upstream latest 更高时只记 WARN；--strict-release-check 才阻断。

执行前提：
  - basic gate 拒绝 root 执行；root 仅用于宿主机准备、部署用户准备、ingress 边界规则物化与权限修复；
  - 实际执行仍要求 Docker 可用，因为静态 preflight 与控制面默认值加载都通过容器化 Python 完成；
  - 进入 host 控制面前，必须先显式执行 bash ./scripts/setup/prepare_control_plane_medium.sh；离线场景使用 --offline --image-archive <local-path>；
  - 若缺少 Docker / Docker daemon / docker compose，或当前用户无权访问 Docker daemon（如 /var/run/docker.sock 权限不足），应先修复宿主机基础环境与 daemon 访问权限；摘要只由控制面写出；
  - 需要拆分定位宿主机层时，请在 one_click_config.sh 已生成 deploy/.env 后执行 bash ./scripts/doctor/check_docker_host_readiness.sh（离线机追加 --offline）。

可选项：
  --env-file <path>              覆盖默认 env 文件路径（默认：deploy/.env）
  --offline                      离线模式；跳过联网 release 检查，并把宿主机 readiness 切到离线模式
  --image-archive <path>         离线模式下附带 deployment_images_*.tar，用于镜像就绪性预检；仅 --offline 下有效
  --skip-release-check           跳过 OpenClaw 上游版本对齐联网检查
  --strict-release-check         使用 strict_release 策略；当前 pin 可验证但 upstream latest 更高时仍返回 FAIL
  --json                         以 JSON 输出摘要
  --quiet                        仅输出结果，不打印过程日志
  --explain                      只打印 basic gate 的检查范围与通过标准，不执行任何动作
  -h, --help                     显示帮助
EOF2
  setup_help_surface_guarantee_text
  setup_help_surface_reference_text
}

one_click_test_basic_static_explain_text() {
  cat <<'EOF2'
one_click_test_basic 检查范围

config 组：
  1. env_file_exists                deploy/.env 是否存在
  2. env_required_placeholders      是否仍包含 __REQUIRED__
  3. verify_required_deploy_env     关键配置是否齐全且格式正确

host 组：
  4. local_runtime_fs_contract      本地文件系统合同（运行态目录、预创建输出目录、脚本执行位）是否满足要求
  5. check_docker_host_readiness    Docker / Compose / 宿主机支持策略是否满足要求
  6. runtime_image_readiness        运行时镜像是否已在本机就绪，或离线归档是否已备齐
  7. deploy_image_coverage          compose 运行镜像与 build/load 准备链路是否完全覆盖
  8. runtime_compose_contract       当前唯一运行路径的 compose 渲染合同是否仍一致
  9. runtime_bind_user_contract     runtime 服务可写 bind mount 的 UID/GID / owner 合同是否满足要求

release 组：
 10. openclaw_release_alignment     OpenClaw release 对齐是否通过；默认 relaxed_install 只把 upstream latest 更新记为 WARN，strict_release 才阻断

边界说明：
  - basic gate 只判断“是否具备进入 bootstrap / 部署阶段的基础条件”；
  - 不执行 bootstrap、compose 渲染、runtime 启动、dispatch doctor 或 full 验证；
  - 若本地文件系统合同失败，先执行 `bash ./scripts/setup/fix_permissions.sh`，再按输出收口 owner 与预创建输出目录权限；
  - 若缺少 Docker / Docker daemon / docker compose，或当前用户无权访问 Docker daemon，应先修复宿主机基础环境与 daemon 访问权限，再回到本门禁；
  - 若 runtime bind mount UID/GID 合同失败，说明容器运行用户与宿主机 owner-only 路径不匹配；应先收口 owner/UID/GID 合同，而不是放宽 700/600 以外的长期安全边界；
  - `OPENCLAW_RUNTIME_UID` / `OPENCLAW_RUNTIME_GID` 是 runtime 用户的固定配置；默认应与当前部署用户一致，手工改成其他 UID/GID 前必须先处理宿主机 bind mount 所有权；
  - basic gate 通过后的默认衔接动作统一查看 `docs/getting-started/quickstart.md`。
  - basic gate 失败后的默认修复入口统一查看 `docs/operations/troubleshooting.md`。
EOF2
  echo
  setup_help_surface_guarantee_text
  setup_help_surface_reference_text
}

one_click_test_full_static_help_text() {
  local order_text='' valid_groups=''
  order_text="$(setup_cli_full_test_group_order_text)"
  valid_groups="$(setup_cli_full_test_valid_groups_text)"
  cat <<'EOF2'
用法：
  bash ./scripts/setup/one_click_test_full.sh [选项]

说明：
  one_click_test_full 是部署完成后的默认统一验证入口；
EOF2
  printf '  默认检查组顺序由 %s 统一派生，当前默认为 %s；\n' "$SETUP_CLI_FULL_TEST_MANIFEST_REL_PATH" "$order_text"
  cat <<'EOF2'
  并写出 deployment acceptance 状态、full test 摘要与最近一次 latest summary。
  完整检查组目录、固定摘要路径与 latest summary 统一查看 `docs/operations/runtime-service-reference.md`。

执行前提：
  - deploy/.env 必须存在且不含 __REQUIRED__；
  - deployment_acceptance.json、当前 host state root 下的 logs/ 与 one_click_test_full.latest.summary.* 的固定写出路径必须具备本地读写执行权限；
  - 当前脚本不会自动 sudo 或提权；以 root 执行 bootstrap/fix_permissions 时必须能解析 OPENCLAW_RUNTIME_UID/GID，解析失败会中止；帮助面仍可通过 --help / --explain 纯静态查看。

可选项：
  --env-file <path>             覆盖默认 env 文件路径（默认：deploy/.env）
EOF2
  printf '  --group <name>                只运行指定检查组（%s）\n' "$valid_groups"
  cat <<'EOF2'
  --only <csv>                  仅运行指定检查项（逗号分隔）
  --skip <csv>                  跳过指定检查项（逗号分隔）
  --json                        以 JSON 输出摘要
  --strict                      有 WARN 时也返回失败
  --quiet                       仅输出结果，不打印过程日志
  --explain                     只打印 full test 的检查组顺序与用途，不执行任何动作
  -h, --help                    显示帮助
EOF2
  setup_help_surface_guarantee_text
  setup_help_surface_reference_text
}

one_click_test_full_static_explain_text() {
  local index=0
  local group=''
  echo 'one_click_test_full 默认检查组顺序'
  echo
  while IFS= read -r group; do
    [[ -n "$group" ]] || continue
    index=$((index + 1))
    printf '  %s. %s
' "$index" "$group"
  done < <(setup_cli_full_test_group_order_from_truth)
  cat <<'EOF2'

统一参考：
  - 完整检查组目录、固定摘要路径、latest summary 与 full test surface helper 统一查看 `docs/operations/runtime-service-reference.md`；
  - deployment acceptance 默认顺序、通过标准与证据产物统一查看 `docs/operations/runtime-service-reference.md`；
  - 帮助面与执行面边界统一查看 `docs/getting-started/quickstart.md`。
  - full test 失败后的默认修复入口统一查看 `docs/operations/troubleshooting.md`。

边界说明：
  - full test 可由 one_click_deploy 自动调用，也可在服务已启动后独立执行 deployment acceptance；
  - 它会写出 deployment acceptance 状态、full test summary 与最近一次 latest summary；摘要只由控制面写出；
  - 若 deployment_acceptance.json、日志目录或 latest summary 固定路径不可写，会在正式执行前直接失败；
  - 需要只看帮助面时，可直接执行 `--help` 或 `--explain`，不依赖 Docker。
EOF2
  echo
  setup_help_surface_guarantee_text
  setup_help_surface_reference_text
}

# 用途：维护 one_click_test_basic 的结果归并、终端摘要与 JSON 输出 helper。


basic_test_named_array_append() {
  local target_name="$1"
  local value="$2"
  case "$target_name" in
    PASS_IDS)
      PASS_IDS+=("$value")
      ;;
    FAIL_IDS)
      FAIL_IDS+=("$value")
      ;;
    WARN_IDS)
      WARN_IDS+=("$value")
      ;;
    SKIP_IDS)
      SKIP_IDS+=("$value")
      ;;
    *)
      echo "[setup_cli_common][FAIL] 未知 basic test 结果数组：$target_name" >&2
      return 2
      ;;
  esac
}

basic_test_consume_duration_seconds() {
  local duration="${SETUP_GATE_LAST_DURATION_SECONDS:-}"
  SETUP_GATE_LAST_DURATION_SECONDS=""
  [[ "$duration" =~ ^[0-9]+$ ]] || duration=""
  printf '%s' "$duration"
}

basic_test_record_result() {
  local bucket_name="$1"
  local status="$2"
  local check_id="$3"
  local detail="${4:-}"
  local group="${5:-}"
  local duration_seconds=''
  duration_seconds="$(basic_test_consume_duration_seconds)"
  if [[ -n "$duration_seconds" ]]; then
    if [[ -n "$detail" ]]; then
      detail+=" [setup_gate_duration_seconds=${duration_seconds}]"
    else
      detail="[setup_gate_duration_seconds=${duration_seconds}]"
    fi
  fi
  detail="${detail//$'\r'/ }"
  detail="${detail//$'\n'/; }"
  detail="${detail//|//}"
  basic_test_named_array_append "$bucket_name" "$check_id"
  RESULT_LINES+=("$status|$check_id|$detail|$group")
}

basic_test_record_pass() { basic_test_record_result PASS_IDS PASS "$@"; }
basic_test_record_fail() { basic_test_record_result FAIL_IDS FAIL "$@"; }
basic_test_record_warn() { basic_test_record_result WARN_IDS WARN "$@"; }
basic_test_record_skip() { basic_test_record_result SKIP_IDS SKIP "$@"; }

basic_test_print_line() {
  local status="$1" check_id="$2" detail="${3:-}" group="${4:-}"
  if [[ -n "$group" ]]; then
    echo "[$status] $check_id ($group)"
  else
    echo "[$status] $check_id"
  fi
  if [[ -n "$detail" ]]; then
    echo "[detail] $detail"
  fi
}

basic_test_emit_terminal_summary() {
  local line='' status='' check_id='' detail='' group='' idx=0 action=''
  for line in "${RESULT_LINES[@]}"; do
    IFS='|' read -r status check_id detail group <<<"$line"
    basic_test_print_line "$status" "$check_id" "$detail" "$group"
  done
  echo "=== one_click_test_basic 汇总 ==="
  echo "PASS: ${#PASS_IDS[@]}"
  echo "FAIL: ${#FAIL_IDS[@]}"
  echo "WARN: ${#WARN_IDS[@]}"
  echo "SKIP: ${#SKIP_IDS[@]}"
  if ((${#FAIL_IDS[@]} > 0)); then
    echo
    echo "失败项:"
    printf -- '- %s\n' "${FAIL_IDS[@]}"
  fi
  if ((${#WARN_IDS[@]} > 0)); then
    echo
    echo "警告项:"
    printf -- '- %s\n' "${WARN_IDS[@]}"
  fi
  if ((${#SKIP_IDS[@]} > 0)); then
    echo
    echo "跳过项:"
    printf -- '- %s\n' "${SKIP_IDS[@]}"
  fi
  if ((${#NEXT_ACTIONS[@]} > 0)); then
    echo
    echo "下一步动作:"
    idx=1
    for action in "${NEXT_ACTIONS[@]}"; do
      echo "$idx. $action"
      idx=$((idx+1))
    done
  fi
}

basic_test_calculate_exit_code() {
  local config_failure="$1"
  if ((${#FAIL_IDS[@]} == 0)); then
    printf '0\n'
    return 0
  fi
  if [[ "$config_failure" == '1' ]]; then
    printf '2\n'
    return 0
  fi
  printf '1\n'
}

basic_test_emit_json_summary() {
  local json_escape_func="$1"
  local first=1 line='' status='' check_id='' detail='' group='' item=''
  printf '{'
  printf '"suite":"one_click_test_basic",'
  printf '"generated_at":"%s",' "$("$json_escape_func" "$GENERATED_AT")"
  printf '"env_file":"%s",' "$("$json_escape_func" "$ENV_FILE")"
  printf '"mode":{"offline":%s},' "$([[ "$OFFLINE_MODE" == "1" ]] && echo true || echo false)"
  printf '"summary":{"pass":%s,"fail":%s,"warn":%s,"skip":%s},' "${#PASS_IDS[@]}" "${#FAIL_IDS[@]}" "${#WARN_IDS[@]}" "${#SKIP_IDS[@]}"
  printf '"checks":['
  first=1
  for line in "${RESULT_LINES[@]}"; do
    IFS='|' read -r status check_id detail group <<<"$line"
    [[ $first -eq 1 ]] || printf ','
    first=0
    printf '{"id":"%s","group":"%s","status":"%s","detail":"%s"}' \
      "$("$json_escape_func" "$check_id")" \
      "$("$json_escape_func" "$group")" \
      "$("$json_escape_func" "$status")" \
      "$("$json_escape_func" "$detail")"
  done
  printf '],'
  printf '"blocking_checks":['
  first=1
  for check_id in "${FAIL_IDS[@]}"; do
    [[ $first -eq 1 ]] || printf ','
    first=0
    printf '"%s"' "$("$json_escape_func" "$check_id")"
  done
  printf '],'
  printf '"warning_checks":['
  first=1
  for check_id in "${WARN_IDS[@]}"; do
    [[ $first -eq 1 ]] || printf ','
    first=0
    printf '"%s"' "$("$json_escape_func" "$check_id")"
  done
  printf '],'
  printf '"skipped_checks":['
  first=1
  for check_id in "${SKIP_IDS[@]}"; do
    [[ $first -eq 1 ]] || printf ','
    first=0
    printf '"%s"' "$("$json_escape_func" "$check_id")"
  done
  printf '],'
  printf '"next_actions":['
  first=1
  for item in "${NEXT_ACTIONS[@]}"; do
    [[ $first -eq 1 ]] || printf ','
    first=0
    printf '"%s"' "$("$json_escape_func" "$item")"
  done
  printf '],'
  printf '"return_code":%s' "$FINAL_EXIT_CODE"
  printf '}\n'
}
