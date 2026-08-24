#!/usr/bin/env python3
"""Hardened Phase 6 selective disclosure entrypoint.

The original Phase 6 implementation is retained in ``routing_legacy.py`` for audit and
compatibility. This module enforces the additional fail-closed invariants discovered in
Codex review without changing canonical memory or curation semantics.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import ai_context_legacy as core
import routing_legacy as legacy
from routing_legacy import *  # noqa: F401,F403


# Preserve the original functions before monkey-patching the compatibility module.
_ORIGINAL_DIRECT_SELECTOR = legacy._direct_selector
_ORIGINAL_PLAN_BUNDLE = legacy.plan_bundle

# Python's Unicode ``\w`` includes letters and digits outside ASCII. Requiring a
# non-underscore word character at the start keeps the previous token shape while making
# Cyrillic, Arabic, CJK, and other scripts routable deterministically.
TOKEN_RE = re.compile(r"[^\W_][\w+.-]*", re.UNICODE)


def tokenize(value: str) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_RE.findall(value)
        if token.casefold() not in legacy.STOPWORDS
    }


def task_terms(task: str, selector: dict[str, Any]) -> set[str]:
    if not selector.get("enabled", True):
        raise RoutingError("profile disables task selection")
    base = tokenize(task)
    if not base:
        raise RoutingError("task selector produced no meaningful terms")
    expanded = set(base)
    for phrase, aliases in selector["aliases"].items():
        key_terms = tokenize(phrase)
        if key_terms and key_terms.issubset(base):
            for alias in aliases:
                expanded.update(tokenize(alias))
    return expanded


def _validated_application_rows(workspace: Path) -> list[dict[str, Any]]:
    """Return Phase 5 applications only after their complete authority chain validates."""
    path = workspace / "curation" / "applications.jsonl"
    if not path.exists():
        return []

    # This validates application protocol/schema/id, candidate/decision references,
    # human-or-policy approval authority, memory hashes, and mutation chains. Routing must
    # never promote a syntactically convenient JSON row into disclosure authority.
    import validate_curation

    validate_curation.cmd_validate(workspace)
    rows = core.read_jsonl(path)
    return rows


def _application_memory_ids(workspace: Path) -> set[str]:
    return {
        row["memory_id"]
        for row in _validated_application_rows(workspace)
        if isinstance(row.get("memory_id"), str)
    }


def _direct_selector(
    record: dict[str, Any],
    *,
    profile: dict[str, Any],
    extra_tags: set[str],
    task: str | None,
    terms: set[str],
) -> tuple[bool, list[str], list[str], list[str]]:
    # Dependency relationship records are routing metadata by default. They must not leak
    # into a baseline/tag/task bundle merely because they themselves match a selector.
    content = record.get("content")
    if (
        record.get("record_type") == "relationship"
        and isinstance(content, dict)
        and content.get("relation") in legacy.DEPENDENCY_RELATIONS
        and not profile["dependency_policy"]["include_relationship_records"]
    ):
        return False, [], [], []
    return _ORIGINAL_DIRECT_SELECTOR(
        record,
        profile=profile,
        extra_tags=extra_tags,
        task=task,
        terms=terms,
    )


def _semantic_key_map(
    workspace: Path,
    active_records: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    """Use Phase 5 explicit semantic keys as authority; derive only as a fallback."""
    result: dict[str, set[str]] = {}
    explicit_by_memory: dict[str, set[str]] = {}
    for row in _validated_application_rows(workspace):
        memory_id = row.get("memory_id")
        key = row.get("semantic_key")
        if (
            isinstance(memory_id, str)
            and memory_id in active_records
            and isinstance(key, str)
            and key
        ):
            explicit_by_memory.setdefault(memory_id, set()).add(key)

    for memory_id, keys in sorted(explicit_by_memory.items()):
        if len(keys) != 1:
            raise RoutingError(
                f"memory {memory_id} has conflicting explicit semantic keys: {sorted(keys)}"
            )
        key = next(iter(keys))
        result.setdefault(key, set()).add(memory_id)

    for memory_id, record in active_records.items():
        if memory_id in explicit_by_memory:
            continue
        key = legacy.derive_semantic_key(record)
        if key:
            result.setdefault(key, set()).add(memory_id)
    return result


def _expand_dependencies(
    workspace: Path,
    selected: dict[str, dict[str, Any]],
    diagnostics: dict[str, dict[str, Any]],
    *,
    records: dict[str, dict[str, Any]],
    graph: dict[str, list[dict[str, str]]],
    profile: dict[str, Any],
    target: dict[str, Any],
    routing_policy: dict[str, Any],
    application_ids: set[str],
) -> None:
    """Expand each dependency node at most once while retaining path-cycle detection."""
    policy = profile["dependency_policy"]
    roots = sorted(selected)
    expanded: set[str] = set()

    def visit(node_id: str, path: list[str], depth: int) -> None:
        if node_id in expanded:
            return
        edges = graph.get(node_id, [])
        if not edges:
            expanded.add(node_id)
            return
        if not policy["enabled"]:
            raise RoutingError(
                f"record {node_id} has required dependencies but dependency expansion is disabled"
            )

        for edge in edges:
            relationship = records[edge["relationship_id"]]
            relationship_reason = exclusion_reason(
                relationship,
                profile=profile,
                target=target,
                routing_policy=routing_policy,
                application_ids=application_ids,
                metadata_only=not policy["include_relationship_records"],
            )
            if relationship_reason:
                raise RoutingError(
                    f"dependency relationship {edge['relationship_id']} is blocked: {relationship_reason}"
                )

            target_id = edge["target_id"]
            next_depth = depth + 1
            if next_depth > policy["max_depth"]:
                raise RoutingError(
                    f"dependency expansion exceeded max_depth={policy['max_depth']} at {target_id}"
                )
            if target_id in path:
                if policy["fail_on_cycle"]:
                    raise RoutingError(
                        f"dependency cycle detected: {' -> '.join([*path, target_id])}"
                    )
                continue

            dependency = records[target_id]
            reason = exclusion_reason(
                dependency,
                profile=profile,
                target=target,
                routing_policy=routing_policy,
                application_ids=application_ids,
            )
            if reason:
                raise RoutingError(
                    f"required dependency {target_id} for {node_id} is blocked: {reason}"
                )

            if target_id not in selected:
                selected[target_id] = dependency
                diagnostics[target_id] = {
                    "memory_id": target_id,
                    "included_by": ["dependency_expansion"],
                    "tag_matches": [],
                    "task_matches": [],
                    "dependency_of": [node_id],
                    "dependency_depth": next_depth,
                }
            else:
                diag = diagnostics[target_id]
                if node_id not in diag["dependency_of"]:
                    diag["dependency_of"].append(node_id)
                    diag["dependency_of"].sort()
                if "dependency_expansion" not in diag["included_by"]:
                    diag["included_by"].append("dependency_expansion")
                    diag["included_by"].sort()
                current_depth = diag.get("dependency_depth")
                if current_depth is None or next_depth < current_depth:
                    diag["dependency_depth"] = next_depth

            if policy["include_relationship_records"]:
                relationship_id = edge["relationship_id"]
                if relationship_id not in selected:
                    selected[relationship_id] = relationship
                    diagnostics[relationship_id] = {
                        "memory_id": relationship_id,
                        "included_by": ["dependency_relationship"],
                        "tag_matches": [],
                        "task_matches": [],
                        "dependency_of": [node_id],
                        "dependency_depth": next_depth,
                    }

            # The active path check above preserves fail-closed cycle detection. Once a node
            # has been fully expanded, revisiting it through another DAG path is redundant.
            visit(target_id, [*path, target_id], next_depth)

        expanded.add(node_id)

    for root in roots:
        visit(root, [root], 0)


def plan_bundle(
    workspace: Path,
    *,
    profile_name: str,
    extra_tags: set[str] | None = None,
    task: str | None = None,
    target_name: str | None = None,
) -> dict[str, Any]:
    legacy.ensure_routing(workspace)
    profile = legacy.load_profile(workspace, profile_name)
    if task and not profile["task_selector"]["enabled"]:
        raise RoutingError(f"profile {profile_name} disables task selection")

    # Routing must not silently collapse a corrupt canonical store into a dictionary.
    records = core.read_jsonl(workspace / "memory" / "records.jsonl")
    seen: set[str] = set()
    for record in records:
        memory_id = record.get("id")
        if not isinstance(memory_id, str) or not memory_id:
            raise RoutingError("canonical memory row is missing a non-empty id")
        if memory_id in seen:
            raise RoutingError(f"duplicate canonical memory id: {memory_id}")
        seen.add(memory_id)

    return _ORIGINAL_PLAN_BUNDLE(
        workspace,
        profile_name=profile_name,
        extra_tags=extra_tags,
        task=task,
        target_name=target_name,
    )


# Patch the compatibility module globals used by its original plan/dependency functions.
legacy.TOKEN_RE = TOKEN_RE
legacy.tokenize = tokenize
legacy.task_terms = task_terms
legacy._application_memory_ids = _application_memory_ids
legacy._direct_selector = _direct_selector
legacy._semantic_key_map = _semantic_key_map
legacy._expand_dependencies = _expand_dependencies
legacy.plan_bundle = plan_bundle


def main() -> int:
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
