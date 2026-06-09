#!/usr/bin/env python3
"""Render helpers for deploy_success summary surface."""
from __future__ import annotations

from typing import Any, Callable


def render_markdown(summary: dict[str, Any], *, summary_profile_fn: Callable[[str], dict[str, Any]]) -> str:
    profile = summary_profile_fn('deploy_success')
    markdown = dict(profile.get('markdown') or {})
    sections = dict(markdown.get('sections') or {})
    next_steps_heading = str(sections.get('next_steps') or '下一步').strip()
    lines = [
        f"# {str(markdown.get('title') or 'one_click_deploy 摘要').strip()}",
        '',
        f"- 时间：{summary['deploy_run']['timestamp'] or summary['generated_at']}",
        f"- 模式：{summary['deploy_run']['mode']}",
        f"- 状态：{summary['deploy_run']['status']}",
        f"- 部署后验收：{'已执行' if summary['deploy_run']['post_acceptance'] else '已跳过'}",
        f"- 日志：`{summary['deploy_run']['log_path'] or '<missing>'}`",
        f"- 机器摘要：`{summary['deploy_run']['summary_json_path'] or '<missing>'}`",
        f"- fixed latest 机器摘要：`{summary['fixed_latest_summary']['json_path'] or '<missing>'}`",
        f"- fixed latest 人工摘要：`{summary['fixed_latest_summary']['markdown_path'] or '<missing>'}`",
    ]
    if summary['deploy_run']['resume_from']:
        lines.append(f"- 恢复阶段：`{summary['deploy_run']['resume_from']}`")
    lines.extend([
        '',
        f"## {str(sections.get('input_summary') or '部署输入摘要').strip()}",
        '',
        f"- config summary：{'存在' if summary['deploy_env_summary']['exists'] else '缺失'}（status={summary['deploy_env_summary']['status'] or 'unknown'}）",
    ])
    if summary['deploy_env_summary']['unresolved_required_keys']:
        lines.append("- 未填人工项：`" + "`, `".join(summary['deploy_env_summary']['unresolved_required_keys']) + "`")
    else:
        lines.append('- 未填人工项：无')
    lines.extend([
        '',
        f"## {str(sections.get('private_ingress') or 'private ingress').strip()}",
        '',
        f"- access host：`{summary['private_ingress']['access_host'] or '<missing>'}`（source={summary['private_ingress']['access_host_source']}，role={summary['private_ingress']['access_host_role']}）",
        f"- bind IP：`{summary['private_ingress']['bind_ip'] or '<missing>'}`（source={summary['private_ingress']['bind_ip_source']}，role={summary['private_ingress']['bind_ip_role']}）",
        f"- auth mode：`{summary['private_ingress']['auth_mode']}`",
        f"- network exposure plane：`{summary['private_ingress']['network_exposure_plane']}`",
        f"- network boundary in repo：{summary['private_ingress']['network_boundary_in_repo']}",
        f"- nginx render scope：`{summary['private_ingress']['nginx_render_scope']}`",
        f"- Nginx 输出：`{summary['private_ingress']['nginx_output_path']}`",
        '',
        f"## {str(sections.get('image_contract') or '部署镜像合同').strip()}",
        '',
        f"- official gateway：`{summary['selected_images']['official_gateway_image'] or '<missing>'}`",
        f"- control plane Python：`{summary['selected_images']['control_plane_image'] or '<missing>'}`",
        f"- runtime Python：`{summary['selected_images']['runtime_python_image'] or '<missing>'}`",
        f"- Nginx runtime：`{summary['selected_images']['nginx_image'] or '<missing>'}`",
        f"- compose 运行镜像集合：`{'`，`'.join([item for item in summary['selected_images']['runtime_service_image_set'] if item]) or '<missing>'}`",
        '',
        f"## {str(sections.get('post_acceptance') or '部署后验收').strip()}",
        '',
        f"- ingress 边界证据：{'存在' if summary['ingress_boundary_evidence']['exists'] else '缺失'}（accepted={summary['ingress_boundary_evidence']['accepted']}，nginx_policy_ok={summary['ingress_boundary_evidence']['nginx_policy_ok']}，rewrite_default_deny={summary['ingress_boundary_evidence']['nginx_policy_rewrite_phase_default_deny']}，access_default_deny={summary['ingress_boundary_evidence']['nginx_policy_access_phase_default_deny']}，method={summary['ingress_boundary_evidence']['boundary_method'] or 'unknown'}）",
        f"- acceptance state：{'存在' if summary['deployment_acceptance']['exists'] else '缺失'}（eligible={summary['deployment_acceptance']['eligible']}，accepted={summary['deployment_acceptance']['accepted']}）",
    ])
    if summary['deployment_acceptance']['required_checks']:
        lines.append("- required checks：" + ', '.join(f"{item.get('id')}:{item.get('status')}" for item in summary['deployment_acceptance']['required_checks'] if isinstance(item, dict)))
    lines.extend([
        '',
        f"## {str(sections.get('runtime_evidence') or '运行验收证据').strip()}",
        '',
        f"- runtime acceptance：{summary['runtime_evidence']['runtime_acceptance_exists']}",
        f"- runtime accepted：{summary['runtime_evidence']['runtime_accepted']}",
        f"- official CLI summary：{summary['runtime_evidence']['official_cli_summary_exists']}",
        f"- official CLI doctor passed：{summary['runtime_evidence'].get('doctor_passed')}",
        '',
        f"## {next_steps_heading}",
        '',
        *[f"- `{step}`" for step in summary['next_steps']],
    ])
    return '\n'.join(lines)


def render_text(summary: dict[str, Any], *, summary_profile_fn: Callable[[str], dict[str, Any]]) -> str:
    profile = summary_profile_fn('deploy_success')
    text_profile = dict(profile.get('text') or {})
    prefix = str(text_profile.get('prefix') or '[deploy_success]').strip()
    next_steps_heading = str(text_profile.get('next_steps_heading') or '[deploy_success] 下一步动作：').strip()
    return '\n'.join([
        f"{prefix} 模式={summary['deploy_run']['mode']} prepare_only={summary['deploy_run']['prepare_only']} post_acceptance={summary['deploy_run']['post_acceptance']} acceptance={summary['deployment_acceptance']['accepted']} exposure_plane={summary['private_ingress']['network_exposure_plane']}",
        f"{prefix} latest_json={summary['fixed_latest_summary']['json_path'] or '<missing>'}",
        f"{prefix} latest_markdown={summary['fixed_latest_summary']['markdown_path'] or '<missing>'}",
        f"{prefix} official_gateway={summary['selected_images']['official_gateway_image'] or '<missing>'}",
        f"{prefix} control_plane={summary['selected_images']['control_plane_image'] or '<missing>'}",
        f"{prefix} runtime_python={summary['selected_images']['runtime_python_image'] or '<missing>'}",
        f"{prefix} nginx={summary['selected_images']['nginx_image'] or '<missing>'}",
        f"{prefix} runtime_service_image_set={','.join([item for item in summary['selected_images']['runtime_service_image_set'] if item]) or '<missing>'}",
        f"{prefix} config_summary={summary['deploy_env_summary']['exists']} unresolved_required={len(summary['deploy_env_summary']['unresolved_required_keys'])}",
        f"{prefix} ingress_boundary_evidence={summary['ingress_boundary_evidence']['exists']} accepted={summary['ingress_boundary_evidence']['accepted']} nginx_policy_ok={summary['ingress_boundary_evidence']['nginx_policy_ok']} rewrite_default_deny={summary['ingress_boundary_evidence']['nginx_policy_rewrite_phase_default_deny']} access_default_deny={summary['ingress_boundary_evidence']['nginx_policy_access_phase_default_deny']} method={summary['ingress_boundary_evidence']['boundary_method'] or '<unknown>'}",
        f"{prefix} runtime_acceptance={summary['runtime_evidence']['runtime_acceptance_exists']} accepted={summary['runtime_evidence']['runtime_accepted']} official_cli={summary['runtime_evidence']['official_cli_summary_exists']}",
        next_steps_heading,
        *[f"  - {step}" for step in summary['next_steps']],
    ])
