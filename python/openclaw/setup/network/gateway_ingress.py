#!/usr/bin/env python3
"""private ingress 渲染控制面：负责官方 Gateway 的 TLS、HSTS、来源 allowlist 与反向代理配置。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.setup.network.private_ingress import validate_private_ingress_bind_ip
from openclaw.setup.network.tls_hostname import validate_tls_hostname
from openclaw.lib.repo.static_truth import host_gateway_file
from openclaw.doctor.platform.ingress_boundary.normalization import normalize_source_cidrs

ROOT_DIR = resolve_repo_root(Path(__file__))
TEMPLATE_PATH = ROOT_DIR / "deploy" / "nginx" / "nginx.conf.template"
DEFAULT_OUTPUT_PATH = ROOT_DIR / host_gateway_file("nginx.gateway.conf")
SOURCE_MAP_PLACEHOLDER = "__OPENCLAW_INGRESS_SOURCE_MAP__"
SOURCE_REWRITE_GUARD_PLACEHOLDER = "__OPENCLAW_INGRESS_REWRITE_GUARD__"
SOURCE_ACCESS_GUARD_PLACEHOLDER = "__OPENCLAW_INGRESS_ACCESS_GUARD__"
INTERNAL_API_AUTHORIZATION_PLACEHOLDER = "__OPENCLAW_INTERNAL_API_AUTHORIZATION__"
CONTROL_PLANE_READ_AUTH_MAP_PLACEHOLDER = "__OPENCLAW_CONTROL_PLANE_READ_AUTH_MAP__"
CONTROL_PLANE_READ_AUTH_GUARD_PLACEHOLDER = "__OPENCLAW_CONTROL_PLANE_READ_AUTH_GUARD__"
SOURCE_PLACEHOLDERS = (
    SOURCE_MAP_PLACEHOLDER,
    SOURCE_REWRITE_GUARD_PLACEHOLDER,
    SOURCE_ACCESS_GUARD_PLACEHOLDER,
    INTERNAL_API_AUTHORIZATION_PLACEHOLDER,
    CONTROL_PLANE_READ_AUTH_MAP_PLACEHOLDER,
    CONTROL_PLANE_READ_AUTH_GUARD_PLACEHOLDER,
)


def fail(prefix: str, message: str, exit_code: int = 2) -> NoReturn:
    sys.stderr.write(f"[{prefix}][FAIL] {message}\n")
    raise SystemExit(exit_code)


def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def parse_env_file(path: str | Path) -> dict[str, str]:
    env: dict[str, str] = {}
    file_path = Path(path)
    if not file_path.exists():
        return env
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def validate_non_negative_int(raw: str, *, name: str) -> int:
    text = str(raw or "").strip()
    if not text.isdigit():
        fail("gateway_ingress_control_plane", f"{name} 必须为非负整数", 3)
    return int(text)


def validate_non_empty(raw: str, *, name: str) -> str:
    text = str(raw or "").strip()
    if not text:
        fail("gateway_ingress_control_plane", f"{name} 必须为非空字符串", 3)
    return text


def validate_tls_mode(raw: str) -> str:
    text = str(raw or "").strip()
    if text not in {"self_signed", "provided_files"}:
        fail("gateway_ingress_control_plane", "OPENCLAW_TLS_MODE 只允许 self_signed 或 provided_files", 3)
    return text


def validate_tls_cn(raw: str) -> str:
    text = validate_non_empty(raw, name="OPENCLAW_TLS_CN")
    error = validate_tls_hostname(text)
    if error:
        fail("gateway_ingress_control_plane", f"OPENCLAW_TLS_CN: {error}", 3)
    return text


def validate_header_token(raw: str, *, name: str) -> str:
    text = validate_non_empty(raw, name=name)
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", text):
        fail("gateway_ingress_control_plane", f"{name} 包含 Nginx header 不安全字符", 3)
    return text


def validate_internal_api_token(raw: str) -> str:
    return validate_header_token(raw, name="OPENCLAW_INTERNAL_API_TOKEN")


def validate_gateway_token(raw: str) -> str:
    return validate_header_token(raw, name="OPENCLAW_GATEWAY_TOKEN")


def validate_source_cidrs(raw: str) -> list[str]:
    payload, exit_code = normalize_source_cidrs(raw)
    if exit_code != 0:
        issues = "；".join(str(item) for item in list(payload.get("issues") or []))
        fail("gateway_ingress_control_plane", f"OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS 无效：{issues}", 3)
    return [str(item) for item in list(payload.get("source_cidrs") or [])]


def render_source_map(source_cidrs: list[str]) -> str:
    lines = [
        "    geo $openclaw_ingress_source_allowed {",
        "        default 0;",
    ]
    lines.extend(f"        {cidr} 1;" for cidr in source_cidrs)
    lines.append("    }")
    return "\n".join(lines)


def render_source_rewrite_guard() -> str:
    return "\n".join([
        "        # rewrite 阶段先拒绝未授权来源，避免重定向绕过 access 阶段。",
        "        if ($openclaw_ingress_source_allowed = 0) {",
        "            return 403;",
        "        }",
    ])


def render_source_access_guard(source_cidrs: list[str]) -> str:
    lines = [
        "        # 访问来源限制由 OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS 派生；",
        "        # host_firewall / external_acl 证据是部署硬门禁。",
    ]
    lines.extend(f"        allow {cidr};" for cidr in source_cidrs)
    lines.append("        deny all;")
    return "\n".join(lines)


def render_control_plane_read_auth_map(gateway_token: str) -> str:
    return "\n".join([
        "    map $http_authorization $openclaw_control_plane_bearer_allowed {",
        "        default 0;",
        f"        \"Bearer {gateway_token}\" 1;",
        "    }",
        "",
        "    map $http_x_openclaw_gateway_token $openclaw_control_plane_header_allowed {",
        "        default 0;",
        f"        \"{gateway_token}\" 1;",
        "    }",
        "",
        "    map \"$openclaw_control_plane_bearer_allowed$openclaw_control_plane_header_allowed\" $openclaw_control_plane_read_allowed {",
        "        default 0;",
        "        ~1 1;",
        "    }",
    ])


def render_control_plane_read_auth_guard() -> str:
    return "\n".join([
        "            # 只读控制面 API 先校验 Gateway token，再注入 internal-api token。",
        "            if ($openclaw_control_plane_read_allowed = 0) {",
        "                return 401;",
        "            }",
    ])


def build_plan(env_file: str) -> dict[str, Any]:
    env = parse_env_file(env_file)
    hsts = validate_non_negative_int(env.get("OPENCLAW_INGRESS_HSTS_MAX_AGE", ""), name="OPENCLAW_INGRESS_HSTS_MAX_AGE")
    tls_mode = validate_tls_mode(env.get("OPENCLAW_TLS_MODE", ""))
    tls_cn = validate_tls_cn(env.get("OPENCLAW_TLS_CN", ""))
    internal_api_token = validate_internal_api_token(env.get("OPENCLAW_INTERNAL_API_TOKEN", ""))
    gateway_token = validate_gateway_token(env.get("OPENCLAW_GATEWAY_TOKEN", ""))
    bind_ip = validate_non_empty(env.get("OPENCLAW_INGRESS_LISTEN_IP", ""), name="OPENCLAW_INGRESS_LISTEN_IP")
    bind_ip_error = validate_private_ingress_bind_ip(bind_ip)
    if bind_ip_error:
        fail("gateway_ingress_control_plane", f"OPENCLAW_INGRESS_LISTEN_IP: {bind_ip_error}", 3)
    source_cidrs = validate_source_cidrs(env.get("OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS", ""))
    rendered = read_text(TEMPLATE_PATH)
    missing_placeholders = [item for item in SOURCE_PLACEHOLDERS if item not in rendered]
    if missing_placeholders:
        fail("gateway_ingress_control_plane", f"Nginx 模板缺少来源限制占位符：{','.join(missing_placeholders)}", 3)
    rendered = rendered.replace("__OPENCLAW_INGRESS_HSTS_MAX_AGE__", str(hsts))
    rendered = rendered.replace("__OPENCLAW_TLS_CN__", tls_cn)
    rendered = rendered.replace(INTERNAL_API_AUTHORIZATION_PLACEHOLDER, f"Bearer {internal_api_token}")
    rendered = rendered.replace(CONTROL_PLANE_READ_AUTH_MAP_PLACEHOLDER, render_control_plane_read_auth_map(gateway_token))
    rendered = rendered.replace(CONTROL_PLANE_READ_AUTH_GUARD_PLACEHOLDER, render_control_plane_read_auth_guard())
    rendered = rendered.replace(SOURCE_MAP_PLACEHOLDER, render_source_map(source_cidrs))
    rendered = rendered.replace(SOURCE_REWRITE_GUARD_PLACEHOLDER, render_source_rewrite_guard())
    rendered = rendered.replace(SOURCE_ACCESS_GUARD_PLACEHOLDER, render_source_access_guard(source_cidrs))
    return {
        "rendered_nginx": rendered,
        "output_path": str(DEFAULT_OUTPUT_PATH),
        "ingress": {
            "accessHost": tls_cn,
            "bindIp": bind_ip,
            "authMode": "official_gateway_token",
            "controlPlaneApiProjection": "private_ingress_readonly_proxy",
            "exposurePolicyPlane": "nginx_allowlist_and_infra_boundary",
            "networkBoundaryInRepo": True,
            "tlsMode": tls_mode,
            "sourceCidrs": source_cidrs,
            "sourceGuard": {
                "envKey": "OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS",
                "defaultDeny": True,
                "rewritePhaseDefaultDeny": True,
                "accessPhaseDefaultDeny": True,
                "plane": "nginx_rewrite_and_access_defense_in_depth",
            },
        },
        "nginx": {
            "templatePath": str(TEMPLATE_PATH.relative_to(ROOT_DIR)),
            "outputPath": str(DEFAULT_OUTPUT_PATH.relative_to(ROOT_DIR)),
            "hstsMaxAge": hsts,
            "tlsMode": tls_mode,
            "sourceCidrs": source_cidrs,
            "sourceGuardDefaultDeny": True,
            "rewritePhaseDefaultDeny": True,
            "accessPhaseDefaultDeny": True,
            "controlPlaneReadGatewayTokenRequired": True,
        },
    }


def command_render_nginx(env_file: str, output_path: str | None) -> None:
    plan = build_plan(env_file)
    target = Path(output_path or plan["output_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plan["rendered_nginx"], encoding="utf-8")
    target.chmod(0o644)
    print(json.dumps({
        "output": str(target),
        "hsts": plan["nginx"]["hstsMaxAge"],
        "access_host": plan["ingress"]["accessHost"],
        "tls_mode": plan["nginx"]["tlsMode"],
        "source_cidrs": plan["nginx"]["sourceCidrs"],
        "default_deny": plan["nginx"]["sourceGuardDefaultDeny"],
        "rewrite_phase_default_deny": plan["nginx"]["rewritePhaseDefaultDeny"],
        "access_phase_default_deny": plan["nginx"]["accessPhaseDefaultDeny"],
    }, ensure_ascii=False))


def command_summary(env_file: str) -> None:
    plan = build_plan(env_file)
    print(json.dumps({
        "ingress": plan["ingress"],
        "nginx": plan["nginx"],
    }, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else ""
    env_file = str(ROOT_DIR / "deploy" / ".env")
    output = None
    index = 1
    while index < len(args):
        current = args[index]
        if current == "--env-file":
            if index + 1 >= len(args):
                fail("gateway_ingress_control_plane", "--env-file 缺少参数", 2)
            env_file = args[index + 1]
            index += 2
            continue
        if current == "--output":
            if index + 1 >= len(args):
                fail("gateway_ingress_control_plane", "--output 缺少参数", 2)
            output = args[index + 1]
            index += 2
            continue
        index += 1
    if command == "summary":
        command_summary(env_file)
        return 0
    if command == "render-nginx":
        command_render_nginx(env_file, output)
        return 0
    if command == "check-nginx":
        plan = build_plan(env_file)
        target = Path(output or plan["output_path"])
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != plan["rendered_nginx"]:
            fail("gateway_ingress_control_plane", f"Nginx 配置与控制面不一致：{target}", 4)
        print(json.dumps({
            "status": "ok",
            "output": str(target),
            "source_cidrs": plan["nginx"]["sourceCidrs"],
            "default_deny": plan["nginx"]["sourceGuardDefaultDeny"],
            "rewrite_phase_default_deny": plan["nginx"]["rewritePhaseDefaultDeny"],
            "access_phase_default_deny": plan["nginx"]["accessPhaseDefaultDeny"],
        }, ensure_ascii=False))
        return 0
    fail("gateway_ingress_control_plane", "用法：summary | render-nginx | check-nginx", 2)

if __name__ == "__main__":
    raise SystemExit(main())
