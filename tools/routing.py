#!/usr/bin/env python3
"""Hardened Phase 6 selective disclosure entrypoint.

The original Phase 6 implementation is retained in ``routing_legacy.py`` for audit and
compatibility. This module enforces fail-closed routing invariants discovered in Codex
review without changing canonical memory or curation semantics.
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from typing import Any

import ai_context_legacy as core
import routing_legacy as legacy
from routing_legacy import *  # noqa: F401,F403


# Preserve original functions before monkey-patching the compatibility module.
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


def _rows_by_id_read_only(path: Path, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in core.read_jsonl(path):
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            raise RoutingError(f"{label} row missing id")
        if rid in result:
            raise RoutingError(f"duplicate {label} id: {rid}")
        result[rid] = row
    return result


def _validate_curation_policy_read_only(workspace: Path) -> None:
    """Validate the Phase 5 policy without invoking any initialization helper."""
    path = workspace / "curation" / "policy.json"
    if not path.exists():
        raise RoutingError(
            "curation policy is missing; routing will not recreate curation authority state"
        )
    value = core.load_json(path)
    if not isinstance(value, dict):
        raise RoutingError("curation policy must be an object")
    if value.get("protocol") != "AI-CONTEXT/CURATION-POLICY":
        raise RoutingError("invalid curation policy protocol")
    if value.get("schema_version") != "0.1.0":
        raise RoutingError("unsupported curation policy schema version")
    allowed = {
        "protocol", "schema_version", "default_retention_days",
        "record_type_retention_days", "allow_policy_auto_expire",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RoutingError(f"unknown curation policy fields: {', '.join(unknown)}")
    default_days = value.get("default_retention_days")
    if default_days is not None and (
        not isinstance(default_days, int) or isinstance(default_days, bool) or default_days < 0
    ):
        raise RoutingError("default_retention_days must be null or a non-negative integer")
    rules = value.get("record_type_retention_days")
    if not isinstance(rules, dict):
        raise RoutingError("record_type_retention_days must be an object")
    for record_type, days in rules.items():
        if record_type not in core.RECORD_TYPES:
            raise RoutingError(f"unknown retention record_type: {record_type}")
        if days is not None and (
            not isinstance(days, int) or isinstance(days, bool) or days < 0
        ):
            raise RoutingError(
                f"retention for {record_type} must be null or a non-negative integer"
            )
    if not isinstance(value.get("allow_policy_auto_expire"), bool):
        raise RoutingError("allow_policy_auto_expire must be boolean")


def _validated_application_rows(workspace: Path) -> list[dict[str, Any]]:
    """Validate Phase 5 application authority without mutating/restoring curation state."""
    applications_path = workspace / "curation" / "applications.jsonl"
    if not applications_path.exists():
        return []

    _validate_curation_policy_read_only(workspace)

    # Import only pure helpers. Do not call validate_curation.cmd_validate(), because that
    # path intentionally initializes Phase 5 state and may recreate a missing policy.
    import curation_legacy as curation
    import validate_curation_legacy as validator

    candidates = _rows_by_id_read_only(
        workspace / "curation" / "candidates.jsonl", "candidate"
    )
    decisions = _rows_by_id_read_only(
        workspace / "curation" / "decisions.jsonl", "decision"
    )
    applications = _rows_by_id_read_only(applications_path, "application")
    mutations = _rows_by_id_read_only(
        workspace / "curation" / "mutations.jsonl", "mutation"
    )
    memories = _rows_by_id_read_only(
        workspace / "memory" / "records.jsonl", "memory"
    )

    mutations_by_memory: dict[str, list[dict[str, Any]]] = {}
    for mutation in mutations.values():
        if mutation.get("protocol") != "AI-CONTEXT/CURATION-MUTATION":
            raise RoutingError(f"invalid mutation protocol: {mutation['id']}")
        if mutation.get("schema_version") != curation.CURATION_VERSION:
            raise RoutingError(f"unsupported mutation schema: {mutation['id']}")
        if mutation["id"] != validator.mutation_expected_id(mutation):
            raise RoutingError(f"mutation id/hash mismatch: {mutation['id']}")
        memory_id = mutation.get("memory_id")
        if memory_id not in memories:
            raise RoutingError(f"mutation memory missing: {mutation['id']}")
        if not set(mutation.get("patch", {})).issubset(curation.MUTABLE_FIELDS):
            raise RoutingError(f"mutation changes forbidden fields: {mutation['id']}")
        actor = mutation.get("actor")
        if not isinstance(actor, dict) or actor.get("type") not in curation.ALLOWED_REVIEW_ACTORS:
            raise RoutingError(f"mutation actor invalid: {mutation['id']}")
        mutations_by_memory.setdefault(str(memory_id), []).append(mutation)

    initial_hash_by_memory: dict[str, str] = {}
    for memory_id, chain in mutations_by_memory.items():
        ordered = sorted(chain, key=lambda row: int(row.get("revision", 0)))
        for index, row in enumerate(ordered, 1):
            if row.get("revision") != index:
                raise RoutingError(f"mutation revision gap for memory: {memory_id}")
            expected_previous = ordered[index - 2]["id"] if index > 1 else None
            if row.get("previous_mutation_id") != expected_previous:
                raise RoutingError(f"mutation chain mismatch: {row['id']}")
            if index > 1 and row.get("before_sha256") != ordered[index - 2].get("after_sha256"):
                raise RoutingError(f"mutation before/after chain hash mismatch: {row['id']}")
        if ordered[-1].get("after_sha256") != curation.canonical_sha(memories[memory_id]):
            raise RoutingError(f"latest mutation does not match current memory: {memory_id}")
        initial_hash_by_memory[memory_id] = str(ordered[0].get("before_sha256"))

    validated: list[dict[str, Any]] = []
    for application in applications.values():
        if application.get("protocol") != "AI-CONTEXT/APPLICATION":
            raise RoutingError(f"invalid application protocol: {application['id']}")
        if application.get("schema_version") != curation.CURATION_VERSION:
            raise RoutingError(f"unsupported application schema: {application['id']}")
        if application["id"] != validator.application_expected_id(application):
            raise RoutingError(f"application id/hash mismatch: {application['id']}")

        candidate_id = application.get("candidate_id")
        decision_id = application.get("decision_id")
        memory_id = application.get("memory_id")
        candidate = candidates.get(str(candidate_id))
        decision = decisions.get(str(decision_id))
        memory = memories.get(str(memory_id))
        if candidate is None:
            raise RoutingError(f"application candidate missing: {application['id']}")
        if decision is None:
            raise RoutingError(f"application decision missing: {application['id']}")
        if memory is None:
            raise RoutingError(f"application memory missing: {application['id']}")

        if candidate.get("protocol") != "AI-CONTEXT/CANDIDATE":
            raise RoutingError(f"invalid candidate protocol: {candidate['id']}")
        if candidate.get("schema_version") != curation.CURATION_VERSION:
            raise RoutingError(f"unsupported candidate schema: {candidate['id']}")
        if candidate["id"] != validator.candidate_expected_id(candidate):
            raise RoutingError(f"candidate id/hash mismatch: {candidate['id']}")

        if decision.get("protocol") != "AI-CONTEXT/REVIEW-DECISION":
            raise RoutingError(f"invalid decision protocol: {decision['id']}")
        if decision.get("schema_version") != curation.CURATION_VERSION:
            raise RoutingError(f"unsupported decision schema: {decision['id']}")
        if decision["id"] != validator.decision_expected_id(decision):
            raise RoutingError(f"decision id/hash mismatch: {decision['id']}")
        if decision.get("candidate_id") != candidate_id or decision.get("decision") != "approve":
            raise RoutingError(
                f"application lacks approved matching decision: {application['id']}"
            )
        if decision.get("candidate_sha256") != curation.canonical_sha(candidate):
            raise RoutingError(f"decision candidate hash mismatch: {decision['id']}")
        actor = decision.get("actor")
        if not isinstance(actor, dict) or actor.get("type") not in curation.ALLOWED_REVIEW_ACTORS:
            raise RoutingError(f"application lacks human/policy approval: {application['id']}")
        if application.get("actor") != actor:
            raise RoutingError(f"application actor does not match decision: {application['id']}")

        expected_memory_hash = initial_hash_by_memory.get(
            str(memory_id), curation.canonical_sha(memory)
        )
        if application.get("memory_sha256") != expected_memory_hash:
            raise RoutingError(f"application memory hash mismatch: {application['id']}")
        validated.append(application)

    return validated


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
    """Index every explicit alias; derive a fallback only when no explicit alias exists."""
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

    # Multiple explicit aliases may legitimately name the same memory. Ambiguity is only
    # unsafe in the opposite direction: one alias resolving to multiple active memories.
    for memory_id, keys in sorted(explicit_by_memory.items()):
        for key in sorted(keys):
            result.setdefault(key, set()).add(memory_id)

    for memory_id, record in active_records.items():
        if memory_id in explicit_by_memory:
            continue
        key = legacy.derive_semantic_key(record)
        if key:
            result.setdefault(key, set()).add(memory_id)
    return result


def _validate_dependency_paths(
    roots: list[str],
    graph: dict[str, list[dict[str, str]]],
    policy: dict[str, Any],
) -> None:
    """Validate max depth independently of root ordering or expansion memoization."""
    max_depth = int(policy["max_depth"])
    if policy["fail_on_cycle"]:
        # With cycles forbidden, memoized longest-path depth gives an order-independent
        # O(V+E) validation while the active stack retains exact cycle detection.
        memo: dict[str, tuple[int, str]] = {}
        active: list[str] = []
        active_set: set[str] = set()

        def longest(node_id: str) -> tuple[int, str]:
            if node_id in active_set:
                start = active.index(node_id)
                cycle = [*active[start:], node_id]
                raise RoutingError(f"dependency cycle detected: {' -> '.join(cycle)}")
            if node_id in memo:
                return memo[node_id]
            active.append(node_id)
            active_set.add(node_id)
            best_depth = 0
            best_end = node_id
            for edge in graph.get(node_id, []):
                child_depth, child_end = longest(edge["target_id"])
                candidate_depth = child_depth + 1
                if candidate_depth > best_depth:
                    best_depth = candidate_depth
                    best_end = child_end
            active.pop()
            active_set.remove(node_id)
            memo[node_id] = (best_depth, best_end)
            return memo[node_id]

        for root in sorted(roots):
            depth, endpoint = longest(root)
            if depth > max_depth:
                raise RoutingError(
                    f"dependency expansion exceeded max_depth={max_depth} from {root} "
                    f"to {endpoint} (required path depth {depth})"
                )
        return

    # If cycles are explicitly tolerated, preserve legacy simple-path semantics. Exhaustive
    # path checking can grow exponentially, so cap work and fail closed rather than hang.
    steps = 0
    budget = max(10000, 100 * (len(graph) + sum(len(v) for v in graph.values()) + 1))

    def walk(node_id: str, path: tuple[str, ...], depth: int) -> None:
        nonlocal steps
        for edge in graph.get(node_id, []):
            target_id = edge["target_id"]
            if target_id in path:
                continue
            next_depth = depth + 1
            if next_depth > max_depth:
                raise RoutingError(
                    f"dependency expansion exceeded max_depth={max_depth} at {target_id}"
                )
            steps += 1
            if steps > budget:
                raise RoutingError(
                    "dependency path validation budget exceeded; routing fails closed"
                )
            walk(target_id, (*path, target_id), next_depth)

    for root in sorted(roots):
        walk(root, (root,), 0)


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
    """Validate every path, then expand once at shortest depth for stable diagnostics."""
    policy = profile["dependency_policy"]
    roots = sorted(selected)
    if any(graph.get(root) for root in roots) and not policy["enabled"]:
        raise RoutingError(
            f"record {next(root for root in roots if graph.get(root))} has required "
            "dependencies but dependency expansion is disabled"
        )

    _validate_dependency_paths(roots, graph, policy)

    best_depth: dict[str, int] = {root: 0 for root in roots}
    queue: deque[str] = deque(roots)

    while queue:
        node_id = queue.popleft()
        depth = best_depth[node_id]
        for edge in graph.get(node_id, []):
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
                    f"dependency relationship {edge['relationship_id']} is blocked: "
                    f"{relationship_reason}"
                )

            target_id = edge["target_id"]
            next_depth = depth + 1
            if next_depth > policy["max_depth"]:
                raise RoutingError(
                    f"dependency expansion exceeded max_depth={policy['max_depth']} at {target_id}"
                )
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
                else:
                    rel_diag = diagnostics[relationship_id]
                    if node_id not in rel_diag["dependency_of"]:
                        rel_diag["dependency_of"].append(node_id)
                        rel_diag["dependency_of"].sort()
                    current_depth = rel_diag.get("dependency_depth")
                    if current_depth is None or next_depth < current_depth:
                        rel_diag["dependency_depth"] = next_depth

            previous_depth = best_depth.get(target_id)
            if previous_depth is None or next_depth < previous_depth:
                best_depth[target_id] = next_depth
                queue.append(target_id)


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


# Patch compatibility-module globals used by its original plan/dependency functions.
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
