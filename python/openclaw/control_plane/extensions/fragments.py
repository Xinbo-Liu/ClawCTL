#!/usr/bin/env python3
"""Iterators for extension-owned surface and governance fragments."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from openclaw.control_plane.extensions.api import enabled_extensions_from_config
from openclaw.control_plane.manifest_fields import fragment_group_field


def enabled_extension_ids(*, config_path: Path | None = None) -> set[str]:
    """Return enabled extension ids for the current control-plane config."""
    return {
        str(extension.get('id') or '').strip()
        for extension in enabled_extensions_from_config(config_path)
        if isinstance(extension, dict) and str(extension.get('id') or '').strip()
    }


def iter_extension_fragment_paths(
    *,
    config_path: Path | None = None,
    group: str,
    key: str,
) -> Iterator[tuple[str, Path]]:
    """Yield fragment paths declared under the requested fragment group."""
    field = fragment_group_field(group)
    for extension in enabled_extensions_from_config(config_path):
        extension_id = str(extension.get('id') or '').strip()
        fragments = extension.get(field) if isinstance(extension.get(field), dict) else {}
        fragment_path = fragments.get(key)
        if isinstance(fragment_path, Path):
            yield extension_id, fragment_path


def iter_surface_fragment_paths(
    *,
    config_path: Path | None = None,
    key: str,
) -> Iterator[tuple[str, Path]]:
    """Yield paths declared under `surfaceFragments` for one key."""
    yield from iter_extension_fragment_paths(config_path=config_path, group='surface', key=key)


def iter_governance_fragment_paths(
    *,
    config_path: Path | None = None,
    key: str,
) -> Iterator[tuple[str, Path]]:
    """Yield paths declared under `governanceSurfaces` for one key."""
    yield from iter_extension_fragment_paths(config_path=config_path, group='governance', key=key)
