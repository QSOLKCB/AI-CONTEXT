#!/usr/bin/env python3
"""Read-only Phase 6 routed-bundle validation for tool/MCP consumers.

Unlike ``tools/validate_routing.py``, this entrypoint never initializes or upgrades routing
state. Missing policy/profile state is an error. This makes the command safe to advertise
as read-only to external tool hosts.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import ai_context_legacy as core
import interop
import routing


class ReadOnlyBundleError(routing.RoutingError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReadOnlyBundleError(message)


def _load_routing_policy_read_only(workspace: Path) -> dict[str, Any]:
    core.ensure_workspace(workspace)
    path = workspace / "routing" / "policy.json"
    if not path.is_file() or path.is_symlink():
        raise ReadOnlyBundleError("routing policy is missing or unsafe; read-only validation will not create it")
    return routing.normalize_routing_policy(core.load_json(path))


def _load_profile_read_only(workspace: Path, name: str) -> dict[str, Any]:
    core.ensure_workspace(workspace)
    path = workspace / "profiles" / f"{name}.json"
    if not path.is_file() or path.is_symlink():
        raise ReadOnlyBundleError(f"profile is missing or unsafe: {name}")
    raw = core.load_json(path)
    if not isinstance(raw, dict):
        raise ReadOnlyBundleError(f"profile {name} must be an object")
    merged = dict(raw)
    for key, default in routing.PROFILE_PHASE6_DEFAULTS.items():
        merged.setdefault(key, copy.deepcopy(default))
    return routing.normalize_profile(merged, expected_name=name)


def plan_bundle_read_only(
    workspace: Path,
    *,
    profile_name: str,
    extra_tags: set[str] | None = None,
    task: str | None = None,
    target_name: str | None = None,
) -> dict[str, Any]:
    """Recompute a routed bundle without creating/upgrading any workspace state."""
    workspace = workspace.expanduser().resolve()
    core.ensure_workspace(workspace)
    policy = core.load_policy(workspace)
    profile = _load_profile_read_only(workspace, profile_name)
    if task and not profile["task_selector"]["enabled"]:
        raise ReadOnlyBundleError(f"profile {profile_name} disables task selection")
    routing_policy = _load_routing_policy_read_only(workspace)
    target_name = target_name or routing_policy["default_target"]
    if target_name not in routing_policy["targets"]:
        raise ReadOnlyBundleError(f"unknown routing target: {target_name}")
    target = routing_policy["targets"][target_name]
    if not target["enabled"]:
        raise ReadOnlyBundleError(f"routing target is disabled: {target_name}")

    records_list = core.read_jsonl(workspace / "memory" / "records.jsonl")
    records: dict[str, dict[str, Any]] = {}
    for record in records_list:
        core.validate_memory_record(record, workspace, policy)
        memory_id = record.get("id")
        if not isinstance(memory_id, str) or not memory_id:
            raise ReadOnlyBundleError("canonical memory row is missing a non-empty id")
        if memory_id in records:
            raise ReadOnlyBundleError(f"duplicate canonical memory id: {memory_id}")
        records[memory_id] = record

    application_ids = routing._application_memory_ids(workspace)
    extra_tags = set(extra_tags or set())
    terms: set[str] = set()
    if task:
        terms = routing.task_terms(task, profile["task_selector"])

    selected: dict[str, dict[str, Any]] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    for memory_id, record in sorted(records.items()):
        reason = routing.exclusion_reason(
            record,
            profile=profile,
            target=target,
            routing_policy=routing_policy,
            application_ids=application_ids,
        )
        if reason:
            continue
        matches, tag_matches, task_matches, included_by = routing._direct_selector(
            record,
            profile=profile,
            extra_tags=extra_tags,
            task=task,
            terms=terms,
        )
        if not matches:
            continue
        selected[memory_id] = record
        diagnostics[memory_id] = {
            "memory_id": memory_id,
            "included_by": sorted(included_by),
            "tag_matches": tag_matches,
            "task_matches": task_matches,
            "dependency_of": [],
            "dependency_depth": None,
        }

    graph = routing.dependency_graph(workspace, records)
    routing._expand_dependencies(
        workspace,
        selected,
        diagnostics,
        records=records,
        graph=graph,
        profile=profile,
        target=target,
        routing_policy=routing_policy,
        application_ids=application_ids,
    )

    selected_records = [selected[memory_id] for memory_id in sorted(selected)]
    selected_diagnostics = [diagnostics[memory_id] for memory_id in sorted(diagnostics)]
    store_hash = core.sha256_bytes(
        core.canonical_bytes(sorted(records_list, key=lambda record: record["id"]))
    )
    routing_meta = {
        "target": target_name,
        "target_kind": target["kind"],
        "effective_sensitivity_ceiling": routing.effective_ceiling(profile, target),
        "task": task or None,
        "task_terms": sorted(terms),
        "profile_sha256": routing.canonical_sha(profile),
        "routing_policy_sha256": routing.canonical_sha(routing_policy),
        "dependency_expansion": profile["dependency_policy"]["enabled"],
    }
    payload = {
        "type": "ai-context-bundle",
        "protocol": "AI-CONTEXT/BUNDLE",
        "schema_version": core.PROTOCOL_VERSION,
        "canonicalizer": "python-json-v0.1",
        "profile": profile_name,
        "selector_tags": sorted(extra_tags),
        "canonical_store_sha256": store_hash,
        "records": selected_records,
        "routing": routing_meta,
        "diagnostics": selected_diagnostics,
    }
    return {**payload, "canonical_payload_sha256": routing.canonical_sha(payload)}


def validate_bundle_read_only(workspace: Path, bundle_path: Path) -> dict[str, Any]:
    _bundle_bytes, bundle = interop._strict_json_bytes(bundle_path.expanduser().resolve())
    require(bundle.get("protocol") == "AI-CONTEXT/BUNDLE", "invalid bundle protocol")
    require(bundle.get("schema_version") == core.PROTOCOL_VERSION, "unsupported bundle schema version")
    require(bundle.get("canonicalizer") == "python-json-v0.1", "unsupported bundle canonicalizer")
    require(isinstance(bundle.get("routing"), dict), "bundle is not a Phase 6 routed bundle")
    require(isinstance(bundle.get("diagnostics"), list), "routed bundle diagnostics must be an array")
    digest = bundle.get("canonical_payload_sha256")
    payload = {key: value for key, value in bundle.items() if key != "canonical_payload_sha256"}
    require(digest == routing.canonical_sha(payload), "bundle canonical payload hash mismatch")

    routing_meta = bundle["routing"]
    profile = bundle.get("profile")
    target = routing_meta.get("target")
    task = routing_meta.get("task")
    selector_tags = bundle.get("selector_tags")
    require(isinstance(profile, str) and profile, "bundle profile missing")
    require(isinstance(target, str) and target, "bundle routing target missing")
    require(task is None or isinstance(task, str), "bundle routing task must be string or null")
    require(
        isinstance(selector_tags, list) and all(isinstance(tag, str) for tag in selector_tags),
        "bundle selector_tags invalid",
    )

    planned = plan_bundle_read_only(
        workspace,
        profile_name=profile,
        extra_tags=set(selector_tags),
        task=task,
        target_name=target,
    )
    require(
        core.canonical_bytes(planned) == core.canonical_bytes(bundle),
        "bundle is stale or does not match the current deterministic routing decision",
    )
    return {
        "status": "ok",
        "bundle_records": len(bundle.get("records", [])),
        "bundle_sha256": digest,
        "target": target,
        "read_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="read-only AI-CONTEXT routed bundle validator")
    parser.add_argument("workspace")
    parser.add_argument("bundle")
    args = parser.parse_args()
    try:
        result = validate_bundle_read_only(
            Path(args.workspace).expanduser().resolve(),
            Path(args.bundle).expanduser().resolve(),
        )
    except (core.ContextError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
