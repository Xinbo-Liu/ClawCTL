"""路径索引派生产物。"""
from __future__ import annotations

import json
from typing import Dict

from openclaw.lib.runtime.path_resolver import PathResolver

from .constants import RENDER_GENERATED_RUNTIME_PATHS_CMD
from .io import write_text

def _view_column_label(view: str, resolver: PathResolver) -> str:
    return str(resolver.public_view_names.get(view) or view)


def build_path_index_outputs(resolver: PathResolver) -> Dict[str, str]:
    import json

    path_index = resolver.build_index()
    outputs = {'path-index.json': json.dumps(path_index, ensure_ascii=False, indent=2) + '\n'}
    view_columns = list(resolver.internal_views)
    labels = [_view_column_label(view, resolver) for view in view_columns]
    lines = [
        '# Path Index',
        '',
        f'由 `{RENDER_GENERATED_RUNTIME_PATHS_CMD}` 生成；用于人工查看逻辑路径对象、逻辑分组与运行视角映射。gateway 为 official Gateway 运行视角；scheduler 为唯一业务执行视角；额外视角只会在启用相应 extension 时出现。',
        '',
        '| 逻辑对象 | 逻辑分组 | ' + ' | '.join(labels) + ' |',
        '|---|---|' + '---|' * len(labels),
    ]
    logical_groups = resolver.logical_groups
    for entry_id, entry in resolver.resolve_all().items():
        logical_group = str(entry.get('logical_group') or '-')
        if logical_group != '-':
            logical_group = str((logical_groups.get(logical_group) or {}).get('label') or logical_group)
        values = [entry['paths'].get(view) or '-' for view in view_columns]
        lines.append('| ' + ' | '.join([f'`{entry_id}`', f'`{logical_group}`', *[f'`{value}`' for value in values]]) + ' |')
    outputs['path-index.md'] = '\n'.join(lines) + '\n'
    return outputs


def render_path_index(resolver: PathResolver) -> None:
    targets = {
        'path-index.json': resolver.absolute_host_path('path_index_json'),
        'path-index.md': resolver.absolute_host_path('path_index_markdown'),
    }
    for name, content in build_path_index_outputs(resolver).items():
        write_text(targets[name], content)
