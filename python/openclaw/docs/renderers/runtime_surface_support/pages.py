#!/usr/bin/env python3
"""Page assembly for runtime_surface renderer."""
from __future__ import annotations

from typing import Any, Callable

from openclaw.docs.renderers.runtime_surface_support import sections as runtime_sections


def render_doc(
    manifest: dict[str, Any],
    *,
    managed_note_text: str,
    acceptance_summary_cmd: str,
    agent_group_acceptance_bindings_cmd: str,
    format_markdown_tables_fn: Callable[[str], str],
) -> str:
    lines: list[str] = [
        f"# {manifest['title']}",
        '',
    ]
    if managed_note_text.strip():
        lines.extend([managed_note_text.strip(), ''])
    lines.extend([
        '## 本页解决什么问题',
        '',
        '本页覆盖三类运行态任务：运行状态查看、deployment acceptance / runtime acceptance 默认顺序、以及最终交付前的证据归档。',
        '',
        '详细对象解释、路径合同、dispatch 观察与恢复说明统一查看 `../architecture/control-plane-baseline.md`、`dispatch-targets.md` 与仓库根路径 `agent/README.md`。',
        '',
        '## 适用范围',
        '',
    ])
    for paragraph in manifest.get('intro') or []:
        lines.append(f'- {paragraph}')
    lines.append('')
    lines.append('- 需要部署主链时回到 `../getting-started/quickstart.md`。')
    lines.append('- 需要统一排障时回到 `troubleshooting.md`。')
    lines.append('')

    lines.append('## 最短路径')
    lines.append('')
    lines.append('这组命令用于默认部署后的运行态核对；若 full test 尚未通过，最后一步证据导出会失败。')
    lines.append('')
    runtime_sections.append_steps(lines, [
        'bash ./scripts/runtime/show_runtime_service_status.sh',
        'sudo bash ./scripts/setup/apply_ingress_boundary_rules.sh --env-file deploy/.env',
        'sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy',
        'bash ./scripts/runtime/export_runtime_acceptance_evidence.sh',
    ])
    lines.append('')
    lines.append(f'- `show_runtime_service_status.sh` 用于确认 {runtime_sections.format_target_list(manifest)} 是否在线。')
    lines.append('- `apply_ingress_boundary_rules.sh` 用于物化 host_firewall 来源限制并写出 root 侧 evidence。')
    lines.append('- `check_ingress_boundary_evidence.sh --require-nginx-policy` 用于确认 private ingress 边界证据已成立。')
    lines.append('- `export_runtime_acceptance_evidence.sh` 用于在 full test 完成后导出当前机器真实运行证据。')
    lines.append('')

    lines.append('## 常用动作')
    lines.append('')
    lines.append('| 场景 | 默认入口 | 下一步 |')
    lines.append('| --- | --- | --- |')
    entrypoints = manifest.get('entrypoints') or []
    for entry in entrypoints:
        title = str(entry.get('title') or '').strip()
        command = str(entry.get('command') or '').strip()
        when = str(entry.get('when') or '').strip()
        lines.append(f'| {title} | `{command}` | {when} |')
    lines.append('')

    lines.append('## runtime target / service / container 对照')
    lines.append('')
    lines.append('| target | compose service | docker container |')
    lines.append('| --- | --- | --- |')
    for item in manifest.get('targets') or []:
        lines.append(f"| {item['target']} | `{item['service']}` | `{item['container']}` |")
    lines.append('')

    lines.append('## 运行镜像来源与 source strategy')
    lines.append('')
    lines.append('runtime contract 与 source strategy 的正式事实统一记录在本节。')
    lines.append('')
    lines.append('| object | canonical source | selected env | selected pin |')
    lines.append('| --- | --- | --- | --- |')
    for object_name, canonical_source, selected_env, pin_file in runtime_sections.runtime_source_rows(manifest):
        lines.append(f'| `{object_name}` | `{canonical_source}` | `{selected_env}` | `{pin_file}` |')
    lines.append('')
    lines.append(
        '- host readiness、部署镜像准备与运行时 compose 只接受当前 selected source；selected source 由 '
        '`config/governance/support/repo_contracts.json` 注册的 runtime contract / source strategy 与 image pin 共同定义，并与 '
        '`docs/getting-started/deployment-inputs.md` 保持一致。'
    )
    lines.append('- canonical source 负责供应链规范表达；acceleration source 只负责区域加速；selected source 才是当前仓库实际默认值。')
    lines.append('')
    lines.extend(runtime_sections.runtime_contract_reference_lines(manifest))

    post_checks = dict(manifest.get('manual_post_deploy_checks') or {})
    lines.append('<a id="manual-post-deploy-checks"></a>')
    lines.append(f"## {post_checks.get('title', '首次部署后的人工补充核对')}")
    lines.append('')
    intro = str(post_checks.get('intro') or '').strip()
    if intro:
        lines.append(intro)
        lines.append('')
    lines.append('### 最短人工核对顺序')
    lines.append('')
    runtime_sections.append_steps(lines, [str(step) for step in (post_checks.get('steps') or []) if str(step).strip()])
    lines.append('')
    lines.append('### 关键运行产物')
    lines.append('')
    for artifact in post_checks.get('artifacts') or []:
        lines.append(f'- `{artifact}`')
    lines.append('')
    lines.append('### 需要同时核对的点')
    lines.append('')
    for point in post_checks.get('points') or []:
        lines.append(f'- {point}')
    lines.append('')
    pairing_note = str(post_checks.get('pairing_note') or '').strip()
    if pairing_note:
        lines.append('### 首次访问 Control UI')
        lines.append('')
        lines.append(pairing_note)
        lines.append('')

    acceptance = dict(manifest.get('acceptance_reference') or {})
    lines.append('## deployment acceptance 与 runtime acceptance')
    lines.append('')
    lines.append('本节是当前仓库对“先形成 deployment acceptance，再导出 runtime acceptance 证据”的唯一固定口径。')
    lines.append('')
    lines.append('<a id="deployment-acceptance-default-flow"></a>')
    lines.append('### deployment acceptance 默认顺序')
    lines.append('')
    runtime_sections.append_steps(lines, [
        'bash ./scripts/setup/one_click_deploy.sh',
        acceptance_summary_cmd,
    ])
    lines.append('')
    lines.append('- 若当前 profile / extension 声明 `required_run_ledger_jobs`，`one_click_deploy.sh` 会在 full test 前自动执行 `run_control_plane_run_all_once.sh` 生成当前机器真实 run ledger；发送动作按当前 target 配置执行。当前 target 配置不允许发送时，使用 `--skip-acceptance` 仅启动服务，并把 deployment acceptance / runtime acceptance evidence 未闭合作为显式交接状态。')
    lines.append('')
    lines.append('使用 `--skip-acceptance` 或 `--prepare-only` 后，先确认 runtime 服务已启动，再按 run ledger 状态闭合 deployment acceptance 与 runtime evidence：')
    lines.append('')
    lines.append('- required run ledger jobs 缺失或失败时，使用 `post_deploy_acceptance` 执行 required jobs、full test 与 runtime evidence；发送动作按当前 target 配置执行。')
    lines.append('- required run ledger jobs 已 accepted，仅 full test 或 runtime evidence 未闭合时，使用 `post_deploy_full_acceptance` 执行 full test 与 runtime evidence，且跳过 run_all_once。')
    lines.append('')
    runtime_sections.append_steps(lines, [
        'bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_acceptance',
        'bash ./scripts/setup/one_click_deploy.sh --resume-from post_deploy_full_acceptance',
        acceptance_summary_cmd,
    ])
    lines.append('')
    lines.append('需要单独查看结构化 full test 摘要时，执行 `bash ./scripts/setup/one_click_test_full.sh --json`；该命令不替代部署恢复入口。')
    lines.append('')
    lines.append('需要候选实例对照时，只在导出前额外执行：')
    lines.append('')
    runtime_sections.append_steps(lines, [
        'bash ./scripts/gateway/run_shadow_upgrade_verify.sh --require-candidate-runtime',
        'bash ./scripts/runtime/export_runtime_acceptance_evidence.sh',
    ])
    lines.append('')
    lines.append('- `run_shadow_upgrade_verify.sh` 的摘要渲染固定通过控制面容器执行；Docker daemon 或控制面镜像未就绪时脚本直接失败。')
    lines.append('')

    lines.append('<a id="deployment-acceptance-pass-criteria"></a>')
    lines.append('### deployment acceptance 通过标准（不含 runtime evidence）')
    lines.append('')
    lines.append(f'- {runtime_sections.format_target_list(manifest)} 全部在线且 healthy。')
    lines.append('- full test 默认 required checks 没有 blocking FAIL。')
    lines.append('- required run ledger jobs 可采集且 execution / artifact evidence 均通过；执行失败、job 缺失、artifact root 缺失或声明输出没有可接受 evidence 都会阻断 deployment acceptance。')
    lines.append('- `deployment_acceptance.json` 同时满足 `eligible=true` 与 `accepted=true`。')
    lines.append('- run ledger、dispatch runtime、shadow verify 与 official CLI 深查属于 runtime acceptance 证据范围，不并入 deployment acceptance state 本身。')
    lines.append('')

    lines.append('<a id="deployment-acceptance-artifacts"></a>')
    lines.append('### deployment acceptance 与 runtime acceptance 证据产物')
    lines.append('')
    lines.append('| 路径 | 写出者 | 作用 |')
    lines.append('| --- | --- | --- |')
    for item in acceptance.get('artifacts') or []:
        lines.append(f"| `{item['path']}` | `{item['owner']}` | {item['meaning']} |")
    lines.append('')
    lines.append('- runtime evidence 统一写入 `<current-host-state-root>/control_plane/release/evidence/`，该目录属于 control-plane state 的 owner-only 运行验收面。')
    lines.append('- runtime acceptance 以最新有效运行事实为准；control-plane run ledger 同时使用 executionAccepted / effectiveExecutionAccepted 与 artifactAccepted / artifactEffectiveAccepted 判断 required job 闭合，artifact manifest 固定记录 artifactRoot、evidenceSources、observedEntries 与 schedulerEntries。')
    lines.append('- secrets / 私钥 / 可写运行态状态不得导出到 runtime evidence；敏感物料继续留在受控 state / certs / env 面。')
    lines.append('')
    lines.append('### required checks')
    lines.append('')
    for check_id in acceptance.get('required_checks') or []:
        lines.append(f'- `{check_id}`')
    lines.append('')
    lines.append(
        f'group 级发布门禁若需要对齐 deployment acceptance required checks，统一通过 `{agent_group_acceptance_bindings_cmd}` 与 '
        '`/v1/control-plane/agent-group-acceptance-bindings` 查看正式映射摘要。'
    )
    lines.append('')

    lines.append('## 失败分流')
    lines.append('')
    lines.append('| 当前现象 | 先跳哪里 |')
    lines.append('| --- | --- |')
    lines.append('| 服务不在线、健康异常、日志异常 | `troubleshooting.md#runtime-与-ingress-问题` |')
    lines.append('| ingress 边界证据不通过 | `troubleshooting.md#runtime-与-ingress-问题` |')
    lines.append('| full test 未闭合或 acceptance state 不通过 | `troubleshooting.md#full-test-与-deployment-acceptance-问题` |')
    lines.append('| runtime evidence / clean release 导出失败 | `troubleshooting.md#验收归档与交付导出问题` |')
    lines.append('| 需要对象路径、run ledger、dispatch observability 长表 | `agent/README.md`、`dispatch-targets.md` |')
    lines.append('')

    lines.append('## 下一步')
    lines.append('')
    lines.append('- 排障总入口：`troubleshooting.md`')
    lines.append('- dispatch target 首次接入：`dispatch-targets.md`')
    lines.append('- deployment 主链回看：`../getting-started/quickstart.md`')
    lines.append('')
    return format_markdown_tables_fn('\n'.join(lines).rstrip() + '\n')
