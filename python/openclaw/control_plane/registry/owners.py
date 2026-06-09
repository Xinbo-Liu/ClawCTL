#!/usr/bin/env python3
"""Owner-aware registry reference helpers."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from openclaw.lib.cli.common import CliError


BASE_OWNER_ID = 'base'
QUALIFIED_REF_SEPARATOR = ':'


def normalize_owner_id(value: Any) -> str:
    """Normalize an owner id, falling back to the base registry owner."""
    text = str(value or '').strip()
    return text or BASE_OWNER_ID


def row_owner_id(row: dict[str, Any]) -> str:
    """Return the effective owner id for a materialized registry row."""
    return normalize_owner_id(row.get('ownerId') or row.get('extensionId') or row.get('sourceExtensionId'))


def split_registry_ref(value: Any) -> tuple[str, str]:
    """Split a registry ref into owner/local-id parts."""
    text = str(value or '').strip()
    if not text:
        return '', ''
    if QUALIFIED_REF_SEPARATOR not in text:
        return '', text
    owner, local_id = text.split(QUALIFIED_REF_SEPARATOR, 1)
    return normalize_owner_id(owner), local_id.strip()


def qualified_registry_id(owner_id: Any, local_id: Any) -> str:
    """Build the canonical owner-qualified registry id."""
    owner = normalize_owner_id(owner_id)
    item_id = str(local_id or '').strip()
    return f'{owner}{QUALIFIED_REF_SEPARATOR}{item_id}'


def annotate_owned_row(row: dict[str, Any], *, owner_id: Any) -> dict[str, Any]:
    """Attach owner metadata to a materialized registry row in place."""
    owner = normalize_owner_id(owner_id)
    item_id = str(row.get('id') or '').strip()
    row['ownerId'] = owner
    row['qualifiedId'] = qualified_registry_id(owner, item_id)
    if owner != BASE_OWNER_ID:
        row['sourceExtensionId'] = owner
        row['extensionId'] = str(row.get('extensionId') or owner)
    return row


def owned_index_bundle(rows: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    """Build qualified, owner, ambiguous, and unqualified-id indexes."""
    by_qualified_id: dict[str, dict[str, Any]] = {}
    by_owner: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item_id = str(row.get('id') or '').strip()
        owner = row_owner_id(row)
        qualified_id = str(row.get('qualifiedId') or '').strip() or qualified_registry_id(owner, item_id)
        row['ownerId'] = owner
        row['qualifiedId'] = qualified_id
        existing = by_qualified_id.get(qualified_id)
        if existing is not None and existing is not row:
            raise CliError(f'{label} id duplicated for owner {owner}: {item_id}', 2)
        by_qualified_id[qualified_id] = row
        by_owner[owner][item_id] = row
        buckets[item_id].append(row)
    by_id: dict[str, dict[str, Any]] = {
        item_id: bucket[0]
        for item_id, bucket in buckets.items()
        if len(bucket) == 1
    }
    ambiguous_ids: dict[str, list[str]] = {
        item_id: sorted(row_owner_id(row) for row in bucket)
        for item_id, bucket in buckets.items()
        if len(bucket) > 1
    }
    return {
        'byId': by_id,
        'byQualifiedId': by_qualified_id,
        'byOwner': {owner: dict(items) for owner, items in by_owner.items()},
        'ambiguousIds': ambiguous_ids,
    }


def resolve_owned_ref(
    ref: Any,
    *,
    by_id: dict[str, dict[str, Any]],
    by_qualified_id: dict[str, dict[str, Any]],
    ambiguous_ids: dict[str, list[str]] | None = None,
    owner_id: Any = '',
    label: str,
) -> dict[str, Any]:
    """Resolve a local or owner-qualified ref to a materialized row."""
    selector_owner, local_id = split_registry_ref(ref)
    if not local_id:
        raise CliError(f'{label} 不能为空', 2)
    if selector_owner:
        qualified_id = qualified_registry_id(selector_owner, local_id)
        row = by_qualified_id.get(qualified_id)
        if isinstance(row, dict):
            return row
        raise CliError(f'{label} 未注册：{qualified_id}', 2)
    owner = normalize_owner_id(owner_id)
    owner_qualified_id = qualified_registry_id(owner, local_id)
    owner_row = by_qualified_id.get(owner_qualified_id)
    if isinstance(owner_row, dict):
        return owner_row
    row = by_id.get(local_id)
    if isinstance(row, dict):
        return row
    owners = sorted((ambiguous_ids or {}).get(local_id) or [])
    if owners:
        raise CliError(f'{label} 存在多个 owner：{local_id} ({", ".join(owners)})；请使用 <owner>:<id> 或 --extension', 2)
    raise CliError(f'{label} 未注册：{local_id}', 2)


def resolved_owned_ref(
    ref: Any,
    *,
    by_id: dict[str, dict[str, Any]],
    by_qualified_id: dict[str, dict[str, Any]],
    ambiguous_ids: dict[str, list[str]] | None = None,
    owner_id: Any = '',
    label: str,
) -> str:
    """Resolve a local or owner-qualified ref and return its canonical qualified id."""
    row = resolve_owned_ref(
        ref,
        by_id=by_id,
        by_qualified_id=by_qualified_id,
        ambiguous_ids=ambiguous_ids,
        owner_id=owner_id,
        label=label,
    )
    return str(row.get('qualifiedId') or qualified_registry_id(row_owner_id(row), row.get('id')))


def resolve_collection_ref(
    registry_or_collections: dict[str, Any],
    collection_key: str,
    ref: Any,
    *,
    owner_id: Any = '',
    label: str | None = None,
) -> dict[str, Any]:
    """Resolve a ref against an owner-aware materialized collection."""
    prefix = collection_key
    return resolve_owned_ref(
        ref,
        by_id=registry_or_collections.get(f'{prefix}ById') if isinstance(registry_or_collections.get(f'{prefix}ById'), dict) else {},
        by_qualified_id=registry_or_collections.get(f'{prefix}ByQualifiedId') if isinstance(registry_or_collections.get(f'{prefix}ByQualifiedId'), dict) else {},
        ambiguous_ids=registry_or_collections.get(f'{prefix}AmbiguousIds') if isinstance(registry_or_collections.get(f'{prefix}AmbiguousIds'), dict) else {},
        owner_id=owner_id,
        label=label or f'{collection_key} ref',
    )


def resolved_collection_ref(
    registry_or_collections: dict[str, Any],
    collection_key: str,
    ref: Any,
    *,
    owner_id: Any = '',
    label: str | None = None,
) -> str:
    """Resolve a collection ref and return its qualified id."""
    row = resolve_collection_ref(
        registry_or_collections,
        collection_key,
        ref,
        owner_id=owner_id,
        label=label,
    )
    return str(row.get('qualifiedId') or qualified_registry_id(row_owner_id(row), row.get('id')))
