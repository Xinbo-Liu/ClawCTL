"""从默认平台对象族和作业策略生成运行产物参考，不读取部署输入或现场状态。"""

from __future__ import annotations

import argparse
import posixpath
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

from openclaw.control_plane.artifact_policies import build_summary
from openclaw.control_plane.governance_surfaces import load_docs_registry
from openclaw.docs.support.doc_targets import resolve_target_from_config
from openclaw.docs.support.docs_registry import require_pages
from openclaw.docs.support.generated_output import add_output_modes, publish_document, read_document
from openclaw.docs.support.markdown_tables import format_markdown_tables
from openclaw.lib.control_plane.object_families import load_contract, resolve_entry_path
from openclaw.lib.repo.layout import DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH, resolve_repo_root
from openclaw.lib.repo.contracts import repo_contract_path

ROOT_DIR = resolve_repo_root(Path(__file__))


def resolve_target(*, root_dir: Path = ROOT_DIR) -> Path:
    """从已登记的两份真源解析同一生成目标，拒绝各自声明指向不同页面。

    参数：
        root_dir（Path）：合同路径登记、对象族和作业策略配置所属的仓库根。
    返回：
        Path：两份合同共同声明的输出文档绝对路径。
    异常：
        ValueError：两份合同的目标声明不一致，路径登记无效或配置源解析到仓库外。
        KeyError：真源没有登记对应的仓库合同 ID。
        SystemExit：配置不是对象，或 generated_artifacts 目标字段为空。
        OSError：真源不可读取。
    副作用：
        每次按当前仓库登记解析配置路径并复用只读 JSON 缓存，不维护额外目标常量。
    """
    root_dir = Path(root_dir).resolve()
    object_source = repo_contract_path('control_plane.object_families', root_dir=root_dir).relative_to(root_dir).as_posix()
    policy_source = repo_contract_path('control_plane.job_artifact_policy_surface', root_dir=root_dir).relative_to(root_dir).as_posix()
    target, object_relative = resolve_target_from_config(object_source, ['generated_artifacts', 'object_family_doc'], prefix='runtime_artifacts', label=object_source, root_dir=root_dir)
    _, policy_relative = resolve_target_from_config(policy_source, ['generated_artifacts', 'artifact_policy_doc'], prefix='runtime_artifacts', label=policy_source, root_dir=root_dir)
    if object_relative != policy_relative:
        raise ValueError(f'运行产物生成目标不一致：{object_source} 声明 {object_relative}；{policy_source} 声明 {policy_relative}；请同步两份合同')
    return target


def _navigation_line(root_dir: Path, target: Path) -> str:
    """按注册页的现有合同身份定位导航目标，并相对于当前生成目录形成链接。

    参数：
        root_dir（Path）：注册表合同和基座页面所在的仓库根。
        target（Path）：从生成合同解析的输出文档绝对路径。
    返回：
        str：运行、维护和排障页面的 Markdown 导航行，路径按 POSIX 规则相对化并编码 URL。
    异常：
        ValueError：导航身份没有唯一基座登记，或合同解析到仓库外。
        SystemExit：注册表页面结构或路径无效。
        OSError：注册表不可读取。
    副作用：
        通过既有 loader 只读取当前仓库和固定平台的注册真源，不维护页面文件清单。
    """
    registry = load_docs_registry(
        repo_contract_path('governance.docs_registry', root_dir=root_dir),
        config_path=root_dir / DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH,
    )
    pages = require_pages(registry)
    parent = target.relative_to(root_dir).parent.as_posix()
    links: list[str] = []
    for label, identity in (
        ('运行与验收入口', 'manual-post-deploy-checks'),
        ('维护地图', 'maintenance_facts_overview'),
        ('排障分流', 'control-ui-first-pairing'),
    ):
        matches: list[dict[str, Any]] = []
        for page in pages:
            contract = page.get('entrypointContract')
            if page.get('extensionId') or not isinstance(contract, dict):
                continue
            if any(isinstance(ref, dict) and ref.get('kind') == 'literal' and ref.get('id') == identity for ref in contract.get('requiredRefs') or []):
                matches.append(page)
        if len(matches) != 1:
            raise ValueError(f'运行产物导航身份 {identity} 必须唯一登记在基座文档中，当前匹配 {len(matches)} 项')
        relative = posixpath.relpath(matches[0]['path'], parent)
        links.append(f'[{label}]({quote(relative, safe="/")})')
    return '- ' + '、'.join(links) + '。'


def _cell(value: Any) -> str:
    """把真源字段转为单行 Markdown 表格内容。

    参数：
        value（Any）：对象说明或路径字段。

    返回：
        str：转义竖线并折叠换行后的表格单元格。

    副作用：
        只在内存中把给定字段转换为新的单元格字符串。
    """
    return str('-' if value is None or value == '' else value).replace('|', '\\|').replace('\r', '').replace('\n', '<br>')


def render_doc(*, root_dir: Path = ROOT_DIR, target: Path | None = None) -> str:
    """使用固定 agent_platform 配置渲染对象族、生产者和作业产物策略。

    参数：
        root_dir（Path）：包含平台配置和对象合同的仓库根目录。
        target（Path | None）：命令入口已解析的实际输出目标；直接渲染时从当前合同解析。

    返回：
        str：无生成时间、绝对宿主机路径或业务 profile 快照的完整 Markdown。

    异常：
        ValueError：对象路径超出当前仓库，生成目标不一致或导航身份不唯一。
        KeyError：合同路径引用无法由平台路径解析器解析。
        SystemExit：对象族或文档注册表结构无效。

    副作用：
        读取默认平台的仓库配置、对象族和 job 真源并使用共享 loader 缓存；生成目标的写入由命令入口处理。
    """
    root_dir = Path(root_dir).resolve()
    navigation = _navigation_line(root_dir, resolve_target(root_dir=root_dir) if target is None else target)
    config = root_dir / DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH
    contract = load_contract(config_path=config, root_dir=root_dir)
    lines = [
        '# 运行产物参考', '',
        '本页说明默认 `agent_platform` 声明的运行产物位置、生产者、验收用途与作业产物策略。', '',
        '内容依据对象族合同和 job artifactPolicy 生成。生成时不读取 `.env` 或运行态 state；实际部署是否通过验收，以运行证据和验收结果为准。', '',
        '## 阅读与定位', '',
        '- 下表路径使用默认宿主机视角；部署机的状态根目录可配置。使用对象解析命令获取现场路径，避免把默认路径当作每台机器的绝对地址。',
        '- 人工维护配置真源；运行产物由生产者生成。缺失、陈旧或失败的 evidence 应先修复生产阶段，再重新验收。',
        navigation, '',
        '```bash',
        'bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane objects json',
        'bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane objects entry-path --family <family-id> --entry <entry-id>',
        'bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane artifacts json',
        '```', '',
        '## 对象族与固定产物', '',
    ]
    for family in contract['families']:
        lines.extend([f"### {_cell(family.get('label') or family['id'])}", '',
                      str(family.get('purpose') or ''), '',
                      '| 对象 | 默认宿主机路径 | 生产者 | 用途 |', '| --- | --- | --- | --- |'])
        for entry in family.get('entries') or []:
            resolved = Path(resolve_entry_path(entry, root_dir, config_path=config, environment={}))
            if resolved.is_absolute():
                resolved = resolved.relative_to(root_dir)
            cells = [entry.get('id'), resolved.as_posix(), entry.get('producer'), entry.get('usage')]
            lines.append('| ' + ' | '.join(_cell(value) for value in cells) + ' |')
        lines.append('')
    summary = build_summary(config_path=config, base_root=root_dir)
    scheduler_runs_root = str(summary['schedulerRunsRoot']).replace('\\', '/')
    lines.extend(['## 作业产物策略', '',
                  '策略来自默认平台已注册 job 的 `artifactPolicy` 和输入输出声明；路径由所选 profile 的 runtime paths 解析。', '',
                  f'默认 scheduler run 根目录：`{_cell(scheduler_runs_root)}`。', '',
                  '每次运行的 `run.json`、`result.json`、`artifacts.json` 与 `stdout.log` 位于 scheduler run 目录。run ledger 的接受结果由实际执行和产物检查产生，不能仅凭进程退出码判断验收通过。', '',
                  '| job | 产物根入口 | latest alias | 保留天数 | run manifest 模式 |',
                  '| --- | --- | --- | --- | --- |'])
    for job in summary['items']:
        cells = [job['id'], job['runArtifactRootEntry'], job['latestAlias'], job['retentionDays'], str(job['schedulerRunManifestPathPattern']).replace('\\', '/')]
        lines.append('| ' + ' | '.join(_cell(value) for value in cells) + ' |')
    if not summary['items']:
        lines.extend(['', '默认平台没有已注册 job。查询业务扩展的产物策略时，先选择对应 profile。'])
    lines.extend(['', '## 扩展与现场查询', '',
                  '扩展的运行产物由其对象族、runtime paths 和 job 合同声明。先选择扩展 profile；查询同名对象族时，在对象命令中显式传 `--extension <id>`。业务输出的含义与使用方式见扩展 README。', '',
                  '```bash',
                  'OPENCLAW_CONTROL_PLANE_PROFILE=<profile-id> bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane objects json',
                  'OPENCLAW_CONTROL_PLANE_PROFILE=<profile-id> bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane artifacts json',
                  '```', ''])
    return format_markdown_tables('\n'.join(lines))


def render_entry(argv: list[str] | None = None) -> int:
    """执行运行产物文档生成命令，检查和 stdout 模式不写文件。

    参数：
        argv（list[str] | None）：命令参数；为空时读取当前进程参数。

    返回：
        int：成功为 0，生成漂移为 1，合同或写入校验失败为 2。

    副作用：
        默认模式原子写入运行产物参考；只读模式仅输出检查结果或 Markdown。
    """
    parser = argparse.ArgumentParser(prog='docs render-runtime-artifacts')
    add_output_modes(parser)
    args = parser.parse_args(argv)
    try:
        target = resolve_target(root_dir=ROOT_DIR)
        snapshot = read_document(ROOT_DIR, target)
        return publish_document(ROOT_DIR, target, render_doc(root_dir=ROOT_DIR, target=target), snapshot, check=args.check, stdout=args.stdout, label='runtime_artifacts')
    except (ValueError, OSError, KeyError, RuntimeError, SystemExit) as exc:
        sys.stderr.write(f'[runtime_artifacts][FAIL] {exc}\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(render_entry())
