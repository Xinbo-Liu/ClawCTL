"""统一规格/文档渲染真源。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openclaw.docs.support.reference_specs_support import io as reference_io
from openclaw.docs.support.reference_specs_support import router as reference_router
from openclaw.docs.support.reference_specs_support import scripts as reference_scripts
from openclaw.docs.support.reference_specs_support import workspace as reference_workspace

ROOT_DIR = reference_io.ROOT_DIR
AUTO_NOTICE = reference_io.AUTO_NOTICE
EMBEDDED_SPECS = reference_io.EMBEDDED_SPECS
SURFACE_LEVELS = reference_io.SURFACE_LEVELS
SURFACE_LABELS = reference_io.SURFACE_LABELS
SURFACE_TITLES = reference_io.SURFACE_TITLES
WORKSPACE_USER_TARGETS = reference_io.WORKSPACE_USER_TARGETS


def read_json(path: Path) -> Any:
    return reference_io.read_json(path)


def load_specs(name: str) -> Any:
    return reference_io.load_specs(name)


def normalize_spacing(text: str) -> str:
    return reference_io.normalize_spacing(text)


def escape_regexp(value: str) -> str:
    return reference_io.escape_regexp(value)


def router_route_specs() -> dict[str, Any]:
    return reference_router.router_route_specs(load_specs_fn=load_specs)


render_explicit_route_list = reference_router.render_explicit_route_list
render_automatic_route_list = reference_router.render_automatic_route_list
render_health_aware_list = reference_router.render_health_aware_list


def render_router_workspace_section(*, config_path=None) -> str:
    return reference_router.render_router_workspace_section(
        config_path=config_path,
        render_explicit_route_list_fn=render_explicit_route_list,
        render_automatic_route_list_fn=render_automatic_route_list,
        render_health_aware_list_fn=render_health_aware_list,
    )


def render_router_route_reference_doc(*, config_path=None) -> str:
    return reference_router.render_router_route_reference_doc(
        config_path=config_path,
        router_route_specs_fn=router_route_specs,
        render_explicit_route_list_fn=render_explicit_route_list,
        render_automatic_route_list_fn=render_automatic_route_list,
        render_health_aware_list_fn=render_health_aware_list,
    )


def _render_workspace_user_target(root_dir: Path, target: dict[str, object], *, config_path: Path | None = None) -> tuple[Path, str]:
    return reference_workspace._render_workspace_user_target(
        root_dir,
        target,
        config_path=config_path,
        replace_workspace_managed_block_fn=replace_workspace_managed_block,
    )


def render_workspace_command_section(target_id: str, *, config_path: Path | None = None) -> str:
    return reference_workspace.render_workspace_command_section(target_id, config_path=config_path)


def begin_marker(target: dict[str, object]) -> str:
    return reference_workspace.begin_marker(target)


def end_marker(target: dict[str, object]) -> str:
    return reference_workspace.end_marker(target)


def render_workspace_managed_block(target: dict[str, object], *, config_path: Path | None = None) -> str:
    return reference_workspace.render_workspace_managed_block(
        target,
        config_path=config_path,
        render_router_workspace_section_fn=render_router_workspace_section,
        render_workspace_command_section_fn=render_workspace_command_section,
        begin_marker_fn=begin_marker,
        end_marker_fn=end_marker,
    )


def replace_workspace_managed_block(content: str, target: dict[str, object], *, config_path: Path | None = None) -> str:
    pattern = re.compile(rf"{escape_regexp(begin_marker(target))}[\s\S]*?{escape_regexp(end_marker(target))}", re.M)
    if not pattern.search(content):
        raise ValueError(f"缺少自动生成标记：{target['relativePath']}")
    return pattern.sub(render_workspace_managed_block(target, config_path=config_path), content)


def render_workspace_user_targets(root_dir: Path = ROOT_DIR, *, config_path: Path | None = None) -> dict[Path, str]:
    return reference_workspace.render_workspace_user_targets(
        root_dir,
        config_path=config_path,
        workspace_user_targets=WORKSPACE_USER_TARGETS,
        render_workspace_user_target_fn=_render_workspace_user_target,
    )


def render_workspace_user_targets_for_repo(root_dir: Path = ROOT_DIR) -> dict[Path, str]:
    return reference_workspace.render_workspace_user_targets_for_repo(
        root_dir,
        render_workspace_user_targets_fn=render_workspace_user_targets,
    )


def script_groups() -> list[dict[str, Any]]:
    return reference_scripts.script_groups(load_specs_fn=load_specs)


def get_script_group(group_id: str) -> dict[str, Any] | None:
    return reference_scripts.get_script_group(group_id, script_groups_fn=script_groups)


def get_all_script_entries() -> list[dict[str, Any]]:
    return reference_scripts.get_all_script_entries(script_groups_fn=script_groups)


def get_script_entry_map() -> dict[str, dict[str, Any]]:
    return reference_scripts.get_script_entry_map(get_all_script_entries_fn=get_all_script_entries)


def get_surface_manifest() -> dict[str, Any]:
    return reference_scripts.get_surface_manifest(load_specs_fn=load_specs)


def get_script_surface_map() -> dict[str, str]:
    return reference_scripts.get_script_surface_map(get_surface_manifest_fn=get_surface_manifest)


def validate_script_surface_manifest() -> list[str]:
    return reference_scripts.validate_script_surface_manifest(
        get_script_entry_map_fn=get_script_entry_map,
        get_surface_manifest_fn=get_surface_manifest,
    )


def get_surface_entries(level: str, group_id: str | None = None) -> list[dict[str, Any]]:
    return reference_scripts.get_surface_entries(
        level,
        group_id,
        get_surface_manifest_fn=get_surface_manifest,
        get_all_script_entries_fn=get_all_script_entries,
    )


def render_script_file_list(entries: list[dict[str, Any]], with_prefix: bool = False, with_surface: bool = True) -> list[str]:
    return reference_scripts.render_script_file_list(
        entries,
        with_prefix=with_prefix,
        with_surface=with_surface,
        get_script_surface_map_fn=get_script_surface_map,
    )


def render_scripts_index_readme() -> str:
    return reference_scripts.render_scripts_index_readme(
        script_groups_fn=script_groups,
        get_surface_entries_fn=get_surface_entries,
        render_script_file_list_fn=render_script_file_list,
    )


def render_group_surface_section(group_id: str, level: str) -> list[str]:
    return reference_scripts.render_group_surface_section(
        group_id,
        level,
        get_surface_entries_fn=get_surface_entries,
        render_script_file_list_fn=render_script_file_list,
    )


def render_scripts_group_readme(group_id: str) -> str:
    return reference_scripts.render_scripts_group_readme(
        group_id,
        get_script_group_fn=get_script_group,
        render_group_surface_section_fn=render_group_surface_section,
    )


def get_script_catalog_doc_layout() -> dict[str, str]:
    return reference_scripts.get_script_catalog_doc_layout(read_json_fn=read_json)


def get_script_doc_targets(root_dir: Path | None = None) -> dict[Path | str, str]:
    return reference_scripts.get_script_doc_targets(
        root_dir,
        get_script_catalog_doc_layout_fn=get_script_catalog_doc_layout,
        render_scripts_index_readme_fn=render_scripts_index_readme,
        script_groups_fn=script_groups,
        render_scripts_group_readme_fn=render_scripts_group_readme,
    )


def get_expected_script_files() -> list[dict[str, Any]]:
    return reference_scripts.get_expected_script_files(script_groups_fn=script_groups)
