"""one_click_deploy 成功态摘要控制面。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.summary.io import (
    relative_or_self,
    summary_output_profile as summary_profile,
    utc_now_iso,
    write_json,
    write_text,
)
from openclaw.lib.testing.acceptance_surface import build_runtime_evidence_status
from openclaw.setup.flow.deploy_success_support import inputs as deploy_inputs
from openclaw.setup.flow.deploy_success_support import next_steps as deploy_next_steps
from openclaw.setup.flow.deploy_success_support import render as deploy_render
from openclaw.setup.flow.deploy_success_support import summary as deploy_summary

ROOT_DIR = resolve_repo_root(Path(__file__))
from openclaw.setup.network import gateway_ingress as gateway_ingress_control_plane
from openclaw.setup.surface import followup as followup_surface

def fail(message: str, code: int = 2) -> NoReturn:
    sys.stderr.write(f"[deploy_success_control_plane][FAIL] {message}\n")
    raise SystemExit(code)


def parse_bool(raw: object, name: str) -> bool:
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    fail(f"{name} 只接受 true/false/1/0，收到：{raw}")

    raise AssertionError("unreachable")

def parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "env_file": ROOT_DIR / "deploy" / ".env",
        "config_summary": "",
        "acceptance_state": "",
        "release_root": ROOT_DIR / "release",
        "release_root_explicit": False,
        "out_json": "",
        "out_md": "",
        "format": "text",
        "mode": "online",
        "status": "success",
        "timestamp": "",
        "resume_from": "",
        "log_path": "",
        "summary_json_path": "",
        "summary_md_path": "",
        "start_services": True,
        "post_acceptance": True,
        "image_archive_path": "",
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if not arg.startswith("--"):
            fail(f"未知参数：{arg}")
        index += 1

        def need() -> str:
            nonlocal index
            if index >= len(argv):
                fail(f"{arg} 缺少参数值")
            value = argv[index]
            index += 1
            return value

        if arg == "--env-file":
            opts["env_file"] = Path(need()).resolve()
        elif arg == "--config-summary":
            opts["config_summary"] = Path(need()).resolve()
        elif arg == "--acceptance-state":
            opts["acceptance_state"] = Path(need()).resolve()
        elif arg == "--release-root":
            opts["release_root"] = Path(need()).resolve()
            opts["release_root_explicit"] = True
        elif arg == "--out-json":
            opts["out_json"] = Path(need()).resolve()
        elif arg == "--out-md":
            opts["out_md"] = Path(need()).resolve()
        elif arg == "--format":
            opts["format"] = need()
        elif arg == "--mode":
            opts["mode"] = need()
        elif arg == "--status":
            opts["status"] = need()
        elif arg == "--timestamp":
            opts["timestamp"] = need()
        elif arg == "--resume-from":
            opts["resume_from"] = need()
        elif arg == "--log-path":
            opts["log_path"] = need()
        elif arg == "--summary-json-path":
            opts["summary_json_path"] = need()
        elif arg == "--summary-md-path":
            opts["summary_md_path"] = need()
        elif arg == "--start-services":
            opts["start_services"] = parse_bool(need(), "--start-services")
        elif arg == "--post-acceptance":
            opts["post_acceptance"] = parse_bool(need(), "--post-acceptance")
        elif arg == "--image-archive-path":
            opts["image_archive_path"] = need()
        else:
            fail(f"未知参数：{arg}")
    return opts


def read_json(path: Path) -> Any | None:
    return deploy_inputs.read_json(path)


def parse_env_file(path: Path) -> dict[str, str]:
    return deploy_inputs.parse_env_file(path, fail=fail)


def build_private_ingress_plan(env_file: Path) -> dict[str, Any]:
    return gateway_ingress_control_plane.build_plan(str(env_file))


def summary_manifest_profile(profile_id: str) -> dict[str, Any]:
    return deploy_inputs.summary_manifest_profile(profile_id, fail=fail)


def default_path(key: str, profile_id: str = "one_click_deploy") -> Path:
    return deploy_inputs.default_path(key, root_dir=ROOT_DIR, profile_id=profile_id)


def next_steps(summary: dict[str, Any]) -> list[str]:
    return deploy_next_steps.build_next_steps(
        summary,
        scenario_info=followup_surface.scenario_info,
        list_str=followup_surface.list_str,
    )


def build_summary(options: dict[str, Any]) -> dict[str, Any]:
    return deploy_summary.build_summary(
        options,
        root_dir=ROOT_DIR,
        parse_env_file_fn=parse_env_file,
        read_json_fn=read_json,
        build_private_ingress_plan_fn=build_private_ingress_plan,
        build_runtime_evidence_status_fn=build_runtime_evidence_status,
        default_path_fn=default_path,
        next_steps_fn=next_steps,
        relative_or_self_fn=relative_or_self,
        utc_now_iso_fn=utc_now_iso,
    )


def render_markdown(summary: dict[str, Any]) -> str:
    return deploy_render.render_markdown(summary, summary_profile_fn=summary_profile)


def render_text(summary: dict[str, Any]) -> str:
    return deploy_render.render_text(summary, summary_profile_fn=summary_profile)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail("缺少命令")
    command = args.pop(0)
    options = parse_args(args)
    if command == "success-summary":
        summary = build_summary(options)
        if options["format"] == "json":
            sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write(render_text(summary) + "\n")
        return 0
    if command == "write-success-summary":
        if not options["out_json"]:
            fail("write-success-summary 缺少 --out-json")
        if not options["out_md"]:
            fail("write-success-summary 缺少 --out-md")
        summary = build_summary(options)
        write_json(Path(options["out_json"]), summary)
        markdown = render_markdown(summary)
        write_text(Path(options["out_md"]), markdown)
        write_json(default_path("latest_json"), summary)
        write_text(default_path("latest_markdown"), markdown)
        return 0
    fail(f"未知命令：{command}")
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
