#!/usr/bin/env python3
"""Full-test control-plane public exports."""
from __future__ import annotations

from openclaw.lib.testing.full_test.acceptance import (
    build_acceptance_status,
    check_by_id,
    check_catalog,
    execution_order,
    group_by_id,
    group_catalog,
    normalize_check_csv,
    normalize_required_acceptance_ids,
    normalize_scalar_list,
    parse_bool,
    parse_required_check_pairs,
    parse_result_line,
    read_lines,
    render_acceptance_kv_lines,
    render_acceptance_shell,
    required_acceptance_ids,
    required_run_ledger_jobs,
    selectable_groups,
    shell_escape,
    summarize_required_run_ledger,
    validate_check_records,
    validate_group_name,
    write_acceptance_state,
    write_deployment_acceptance_state,
)
from openclaw.lib.testing.full_test.cli import main, parse_args, usage, write_scalar_list
from openclaw.lib.testing.full_test.io import (
    MANIFEST_PATH,
    ROOT_DIR,
    SUMMARY_MANIFEST_PATH,
    SUMMARY_OUTPUT_SURFACE_PATH,
    SURFACE_PATH,
    default_path,
    fail,
    generated_doc_path,
    read_json,
    read_manifest,
    read_summary_manifest,
    read_summary_output_surface,
    read_surface,
    safe_read_json,
    summary_output_profile,
    write_json,
    write_text,
)
from openclaw.lib.testing.full_test.render import (
    append_steps,
    build_summary,
    render_doc,
    render_markdown,
    render_text,
    write_summary,
)


if __name__ == '__main__':
    raise SystemExit(main())
