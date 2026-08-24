#!/usr/bin/env python3
"""Phase 6 selective disclosure and routing for AI-CONTEXT.

Routing is a deterministic read-only projection over canonical memory. It never mutates
canonical records. Positive selectors choose the smallest useful starting set; dependency
expansion may add required records, but dependencies never bypass disclosure gates.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import ai_context_legacy as core

ROUTING_VERSION = "0.1.0"

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to",
    "with", "you", "your", "we", "our", "please", "can", "could", "should",
}
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+.-]*")
DEPENDENCY_RELATIONS = {"depends_on", "requires"}

EMPTY_HARD_EXCLUSIONS = {
    "record_ids": [],
    "record_types": [],
    "epistemic_states": [],
    "source_refs": [],
    "content_paths": [],
}

DEFAULT_TASK_SELECTOR = {
    "enabled": True,
    "min_matches": 1,
    "aliases": {},
    "search_notes": False,
}

DEFAULT_DEPENDENCY_POLICY = {
    "enabled": True,
    "max_depth": 8,
    "fail_on_cycle": True,
    "include_relationship_records": False,
}

DEFAULT_ROUTING_POLICY = {
    "protocol": "AI-CONTEXT/ROUTING-POLICY",
    "schema_version": ROUTING_VERSION,
    "default_target": "local-default",
    "global_hard_exclusions": EMPTY_HARD_EXCLUSIONS,
    "targets": {
        "local-default": {
            "kind": "local_model",
            "enabled": True,
            "description": "Default local-model disclosure target.",
            "max_sensitivity": "private",
            "min_confidence": 0.0,
            "allowed_record_types": [],
            "denied_record_types": [],
            "denied_tags": [],
            "denied_epistemic_states": [],
            "require_verified_record_types": [],
            "require_curation_application": False,
            "hard_exclusions": EMPTY_HARD_EXCLUSIONS,
        },
        "provider-default": {
            "kind": "provider",
            "enabled": True,
            "description": "Conservative generic external-provider target.",
            "max_sensitivity": "public",
            "min_confidence": 0.0,
            "allowed_record_types": [],
            "denied_record_types": [],
            "denied_tags": [],
            "denied_epistemic_states": [],
            "require_verified_record_types": [],
            "require_curation_application": True,
            "hard_exclusions": EMPTY_HARD_EXCLUSIONS,
        },
    },
}

PROFILE_PHASE6_DEFAULTS = {
    "task_selector": DEFAULT_TASK_SELECTOR,
    "hard_exclusions": EMPTY_HARD_EXCLUSIONS,
    "dependency_policy": DEFAULT_DEPENDENCY_POLICY,
}


class RoutingError(core.ContextError):
    pass


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def canonical_sha(value: Any) -> str:
    return core.sha256_bytes(core.canonical_bytes(value))


def _unique_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise RoutingError(f"{label} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise RoutingError(f"{label} must not contain duplicates")
    return list(value)


def _validate_record_types(value: Any, label: str) -> list[str]:
    result = _unique_strings(value, label)
    unknown = sorted(set(result) - core.RECORD_TYPES)
    if unknown:
        raise RoutingError(f"{label} contains unknown record types: {', '.join(unknown)}")
    return result


def _validate_epistemic_states(value: Any, label: str) -> list[str]:
    result = _unique_strings(value, label)
    unknown = sorted(set(result) - core.EPISTEMIC_STATES)
    if unknown:
        raise RoutingError(f"{label} contains unknown epistemic states: {', '.join(unknown)}")
    return result


def validate_hard_exclusions(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        value = _copy_json(EMPTY_HARD_EXCLUSIONS)
    if not isinstance(value, dict):
        raise RoutingError(f"{label} must be an object")
    allowed = {"record_ids", "record_types", "epistemic_states", "source_refs", "content_paths"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RoutingError(f"unknown {label} fields: {', '.join(unknown)}")
    result = {
        "record_ids": _unique_strings(value.get("record_ids", []), f"{label}.record_ids"),
        "record_types": _validate_record_types(value.get("record_types", []), f"{label}.record_types"),
        "epistemic_states": _validate_epistemic_states(value.get("epistemic_states", []), f"{label}.epistemic_states"),
        "source_refs": _unique_strings(value.get("source_refs", []), f"{label}.source_refs"),
        "content_paths": _unique_strings(value.get("content_paths", []), f"{label}.content_paths"),
    }
    for path in result["content_paths"]:
        if path.startswith(".") or path.endswith(".") or ".." in path or "*" in path:
            raise RoutingError(f"{label}.content_paths contains invalid exact dotted path: {path}")
    return result


def _validate_task_selector(value: Any) -> dict[str, Any]:
    if value is None:
        value = _copy_json(DEFAULT_TASK_SELECTOR)
    if not isinstance(value, dict):
        raise RoutingError("profile task_selector must be an object")
    allowed = {"enabled", "min_matches", "aliases", "search_notes"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RoutingError(f"unknown task_selector fields: {', '.join(unknown)}")
    enabled = value.get("enabled", True)
    min_matches = value.get("min_matches", 1)
    aliases = value.get("aliases", {})
    search_notes = value.get("search_notes", False)
    if not isinstance(enabled, bool) or not isinstance(search_notes, bool):
        raise RoutingError("task_selector enabled/search_notes must be boolean")
    if not isinstance(min_matches, int) or isinstance(min_matches, bool) or not 1 <= min_matches <= 32:
        raise RoutingError("task_selector.min_matches must be an integer from 1 to 32")
    if not isinstance(aliases, dict):
        raise RoutingError("task_selector.aliases must be an object")
    normalized_aliases: dict[str, list[str]] = {}
    for key, items in aliases.items():
        if not isinstance(key, str) or not key.strip():
            raise RoutingError("task_selector alias keys must be non-empty strings")
        normalized_aliases[key] = _unique_strings(items, f"task_selector.aliases[{key!r}]")
    return {
        "enabled": enabled,
        "min_matches": min_matches,
        "aliases": normalized_aliases,
        "search_notes": search_notes,
    }


def _validate_dependency_policy(value: Any) -> dict[str, Any]:
    if value is None:
        value = _copy_json(DEFAULT_DEPENDENCY_POLICY)
    if not isinstance(value, dict):
        raise RoutingError("profile dependency_policy must be an object")
    allowed = {"enabled", "max_depth", "fail_on_cycle", "include_relationship_records"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RoutingError(f"unknown dependency_policy fields: {', '.join(unknown)}")
    enabled = value.get("enabled", True)
    max_depth = value.get("max_depth", 8)
    fail_on_cycle = value.get("fail_on_cycle", True)
    include_relationship_records = value.get("include_relationship_records", False)
    if not all(isinstance(item, bool) for item in (enabled, fail_on_cycle, include_relationship_records)):
        raise RoutingError("dependency_policy boolean fields must be boolean")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 0 <= max_depth <= 32:
        raise RoutingError("dependency_policy.max_depth must be an integer from 0 to 32")
    return {
        "enabled": enabled,
        "max_depth": max_depth,
        "fail_on_cycle": fail_on_cycle,
        "include_relationship_records": include_relationship_records,
    }


def normalize_profile(value: Any, *, expected_name: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RoutingError("profile must be an object")
    allowed = {
        "protocol", "schema_version", "name", "include_tags", "exclude_tags",
        "record_types", "max_sensitivity", "task_selector", "hard_exclusions",
        "dependency_policy",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RoutingError(f"unknown profile fields: {', '.join(unknown)}")
    if value.get("protocol") != "AI-CONTEXT/PROFILE" or value.get("schema_version") != ROUTING_VERSION:
        raise RoutingError("invalid or unsupported profile protocol/schema")
    name = value.get("name")
    if not isinstance(name, str) or not name:
        raise RoutingError("profile name must be a non-empty string")
    if expected_name is not None and name != expected_name:
        raise RoutingError(f"profile name mismatch: expected {expected_name}, found {name}")
    include_tags = _unique_strings(value.get("include_tags", []), "profile.include_tags")
    exclude_tags = _unique_strings(value.get("exclude_tags", []), "profile.exclude_tags")
    overlap = sorted(set(include_tags) & set(exclude_tags))
    if overlap:
        raise RoutingError(f"profile tags cannot be both included and excluded: {', '.join(overlap)}")
    record_types = _validate_record_types(value.get("record_types", []), "profile.record_types")
    max_sensitivity = value.get("max_sensitivity", "private")
    if max_sensitivity not in {"public", "private", "restricted"}:
        raise RoutingError("profile max_sensitivity must be public, private, or restricted")
    return {
        "protocol": "AI-CONTEXT/PROFILE",
        "schema_version": ROUTING_VERSION,
        "name": name,
        "include_tags": include_tags,
        "exclude_tags": exclude_tags,
        "record_types": record_types,
        "max_sensitivity": max_sensitivity,
        "task_selector": _validate_task_selector(value.get("task_selector")),
        "hard_exclusions": validate_hard_exclusions(value.get("hard_exclusions"), "profile.hard_exclusions"),
        "dependency_policy": _validate_dependency_policy(value.get("dependency_policy")),
    }


def _validate_target(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RoutingError(f"routing target {name} must be an object")
    allowed = {
        "kind", "enabled", "description", "max_sensitivity", "min_confidence",
        "allowed_record_types", "denied_record_types", "denied_tags",
        "denied_epistemic_states", "require_verified_record_types",
        "require_curation_application", "hard_exclusions",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RoutingError(f"unknown routing target fields for {name}: {', '.join(unknown)}")
    kind = value.get("kind")
    if kind not in {"provider", "local_model"}:
        raise RoutingError(f"routing target {name}.kind must be provider or local_model")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise RoutingError(f"routing target {name}.enabled must be boolean")
    description = value.get("description", "")
    if not isinstance(description, str):
        raise RoutingError(f"routing target {name}.description must be a string")
    max_sensitivity = value.get("max_sensitivity", "public")
    if max_sensitivity not in {"public", "private", "restricted"}:
        raise RoutingError(f"routing target {name}.max_sensitivity must not be secret")
    min_confidence = value.get("min_confidence", 0.0)
    if (
        not isinstance(min_confidence, (int, float))
        or isinstance(min_confidence, bool)
        or not 0 <= min_confidence <= 1
    ):
        raise RoutingError(f"routing target {name}.min_confidence must be from 0 to 1")
    allowed_types = _validate_record_types(value.get("allowed_record_types", []), f"target {name}.allowed_record_types")
    denied_types = _validate_record_types(value.get("denied_record_types", []), f"target {name}.denied_record_types")
    overlap = sorted(set(allowed_types) & set(denied_types))
    if overlap:
        raise RoutingError(f"routing target {name} both allows and denies: {', '.join(overlap)}")
    denied_tags = _unique_strings(value.get("denied_tags", []), f"target {name}.denied_tags")
    denied_states = _validate_epistemic_states(value.get("denied_epistemic_states", []), f"target {name}.denied_epistemic_states")
    verified_types = _validate_record_types(value.get("require_verified_record_types", []), f"target {name}.require_verified_record_types")
    require_application = value.get("require_curation_application", False)
    if not isinstance(require_application, bool):
        raise RoutingError(f"routing target {name}.require_curation_application must be boolean")
    return {
        "kind": kind,
        "enabled": enabled,
        "description": description,
        "max_sensitivity": max_sensitivity,
        "min_confidence": float(min_confidence),
        "allowed_record_types": allowed_types,
        "denied_record_types": denied_types,
        "denied_tags": denied_tags,
        "denied_epistemic_states": denied_states,
        "require_verified_record_types": verified_types,
        "require_curation_application": require_application,
        "hard_exclusions": validate_hard_exclusions(value.get("hard_exclusions"), f"target {name}.hard_exclusions"),
    }


def normalize_routing_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RoutingError("routing policy must be an object")
    allowed = {"protocol", "schema_version", "default_target", "global_hard_exclusions", "targets"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RoutingError(f"unknown routing policy fields: {', '.join(unknown)}")
    if value.get("protocol") != "AI-CONTEXT/ROUTING-POLICY" or value.get("schema_version") != ROUTING_VERSION:
        raise RoutingError("invalid or unsupported routing policy protocol/schema")
    targets = value.get("targets")
    if not isinstance(targets, dict) or not targets:
        raise RoutingError("routing policy targets must be a non-empty object")
    normalized_targets: dict[str, dict[str, Any]] = {}
    for name, target in targets.items():
        if not isinstance(name, str) or not name:
            raise RoutingError("routing target names must be non-empty strings")
        normalized_targets[name] = _validate_target(name, target)
    default_target = value.get("default_target")
    if not isinstance(default_target, str) or default_target not in normalized_targets:
        raise RoutingError("routing policy default_target must name an existing target")
    return {
        "protocol": "AI-CONTEXT/ROUTING-POLICY",
        "schema_version": ROUTING_VERSION,
        "default_target": default_target,
        "global_hard_exclusions": validate_hard_exclusions(
            value.get("global_hard_exclusions"), "routing.global_hard_exclusions"
        ),
        "targets": normalized_targets,
    }


def _ensure_private_ignore(workspace: Path) -> None:
    path = workspace / ".gitignore"
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    changed = False
    for entry in ("routing/",):
        if entry not in lines:
            lines.append(entry)
            changed = True
    if changed:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _upgrade_general_profile(workspace: Path) -> None:
    path = workspace / "profiles" / "general.json"
    if not path.exists():
        return
    value = core.load_json(path)
    if not isinstance(value, dict):
        raise RoutingError("general profile must be an object")
    changed = False
    upgraded = dict(value)
    for key, default in PROFILE_PHASE6_DEFAULTS.items():
        if key not in upgraded:
            upgraded[key] = _copy_json(default)
            changed = True
    normalized = normalize_profile(upgraded, expected_name="general")
    if changed:
        core.write_json(path, normalized)


def ensure_routing(workspace: Path) -> None:
    core.ensure_workspace(workspace)
    root = workspace / "routing"
    root.mkdir(parents=True, exist_ok=True)
    policy_path = root / "policy.json"
    if not policy_path.exists():
        core.write_json(policy_path, _copy_json(DEFAULT_ROUTING_POLICY))
    _ensure_private_ignore(workspace)
    _upgrade_general_profile(workspace)


def load_routing_policy(workspace: Path) -> dict[str, Any]:
    ensure_routing(workspace)
    return normalize_routing_policy(core.load_json(workspace / "routing" / "policy.json"))


def load_profile(workspace: Path, name: str) -> dict[str, Any]:
    ensure_routing(workspace)
    path = workspace / "profiles" / f"{name}.json"
    if not path.exists():
        raise RoutingError(f"unknown profile: {name}")
    raw = core.load_json(path)
    if not isinstance(raw, dict):
        raise RoutingError(f"profile {name} must be an object")
    merged = dict(raw)
    for key, default in PROFILE_PHASE6_DEFAULTS.items():
        merged.setdefault(key, _copy_json(default))
    return normalize_profile(merged, expected_name=name)


def tokenize(value: str) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_RE.findall(value)
        if token.casefold() not in STOPWORDS
    }


def task_terms(task: str, selector: dict[str, Any]) -> set[str]:
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


def _collect_tokens(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(tokenize(str(key)))
            _collect_tokens(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect_tokens(item, out)
    elif isinstance(value, (str, int, float, bool)):
        out.update(tokenize(str(value)))


def record_semantic_tokens(record: dict[str, Any], *, search_notes: bool) -> set[str]:
    out: set[str] = set()
    out.update(tokenize(str(record.get("record_type", ""))))
    for tag in record.get("tags", []):
        if isinstance(tag, str):
            out.update(tokenize(tag))
    _collect_tokens(record.get("content", {}), out)
    if search_notes and isinstance(record.get("notes"), str):
        out.update(tokenize(record["notes"])
    )
    return out


def _path_exists(value: Any, dotted: str) -> bool:
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _merged_exclusion_sets(*items: dict[str, Any]) -> dict[str, set[str]]:
    keys = ("record_ids", "record_types", "epistemic_states", "source_refs", "content_paths")
    return {key: set().union(*(set(item.get(key, [])) for item in items)) for key in keys}


def _application_memory_ids(workspace: Path) -> set[str]:
    path = workspace / "curation" / "applications.jsonl"
    if not path.exists():
        return set()
    return {
        row.get("memory_id")
        for row in core.read_jsonl(path)
        if isinstance(row.get("memory_id"), str)
    }


def effective_ceiling(profile: dict[str, Any], target: dict[str, Any]) -> str:
    profile_rank = core.SENSITIVITY_RANK[profile["max_sensitivity"]]
    target_rank = core.SENSITIVITY_RANK[target["max_sensitivity"]]
    rank = min(profile_rank, target_rank)
    return next(name for name, value in core.SENSITIVITY_RANK.items() if value == rank)


def exclusion_reason(
    record: dict[str, Any],
    *,
    profile: dict[str, Any],
    target: dict[str, Any],
    routing_policy: dict[str, Any],
    application_ids: set[str],
    metadata_only: bool = False,
) -> str | None:
    if record.get("approval") != "approved":
        return "record is not approved"
    if record.get("lifecycle", {}).get("state") != "active":
        return f"record lifecycle is {record.get('lifecycle', {}).get('state')}"
    sensitivity = record.get("sensitivity")
    if sensitivity not in core.SENSITIVITY_RANK or sensitivity == "secret":
        return "record sensitivity is undisclosable"
    ceiling = effective_ceiling(profile, target)
    if core.SENSITIVITY_RANK[sensitivity] > core.SENSITIVITY_RANK[ceiling]:
        return f"record sensitivity {sensitivity} exceeds effective ceiling {ceiling}"

    record_type = record.get("record_type")
    if not metadata_only:
        profile_types = set(profile["record_types"])
        if profile_types and record_type not in profile_types:
            return f"record type {record_type} is outside profile record_types"
    allowed_types = set(target["allowed_record_types"])
    if allowed_types and record_type not in allowed_types:
        return f"record type {record_type} is not allowed by target"
    if record_type in set(target["denied_record_types"]):
        return f"record type {record_type} is denied by target"

    tags = set(record.get("tags", []))
    if tags & set(profile["exclude_tags"]):
        return "record has a profile-excluded tag"
    if tags & set(target["denied_tags"]):
        return "record has a target-denied tag"
    if record.get("epistemic_state") in set(target["denied_epistemic_states"]):
        return f"epistemic state {record.get('epistemic_state')} is denied by target"
    if float(record.get("confidence", 0.0)) < target["min_confidence"]:
        return f"confidence is below target minimum {target['min_confidence']}"
    if (
        record_type in set(target["require_verified_record_types"])
        and record.get("epistemic_state") != "verified"
    ):
        return f"record type {record_type} requires verified epistemic state for target"
    if target["require_curation_application"] and record.get("id") not in application_ids:
        return "target requires a Phase 5 curation application receipt"

    exclusions = _merged_exclusion_sets(
        routing_policy["global_hard_exclusions"],
        profile["hard_exclusions"],
        target["hard_exclusions"],
    )
    if record.get("id") in exclusions["record_ids"]:
        return "record id is hard-excluded"
    if record_type in exclusions["record_types"]:
        return f"record type {record_type} is hard-excluded"
    if record.get("epistemic_state") in exclusions["epistemic_states"]:
        return f"epistemic state {record.get('epistemic_state')} is hard-excluded"
    if set(record.get("source_refs", [])) & exclusions["source_refs"]:
        return "record references a hard-excluded source observation"
    for path in sorted(exclusions["content_paths"]):
        if _path_exists(record.get("content", {}), path):
            return f"record contains hard-excluded content path {path}"
    return None


def _direct_selector(
    record: dict[str, Any],
    *,
    profile: dict[str, Any],
    extra_tags: set[str],
    task: str | None,
    terms: set[str],
) -> tuple[bool, list[str], list[str], list[str]]:
    record_tags = set(record.get("tags", []))
    positive_tags = set(profile["include_tags"]) | extra_tags
    tag_matches = sorted(record_tags & positive_tags)
    if positive_tags and not tag_matches:
        return False, [], [], []

    task_matches: list[str] = []
    if task:
        selector = profile["task_selector"]
        if not selector["enabled"]:
            raise RoutingError(f"profile {profile['name']} disables task selection")
        surface = record_semantic_tokens(record, search_notes=selector["search_notes"])
        task_matches = sorted(terms & surface)
        if len(task_matches) < selector["min_matches"]:
            return False, tag_matches, task_matches, []

    included_by: list[str] = []
    if positive_tags:
        included_by.append("tag_selector")
    if task:
        included_by.append("task_selector")
    if not included_by:
        included_by.append("profile_baseline")
    return True, tag_matches, task_matches, included_by


def derive_semantic_key(record: dict[str, Any]) -> str | None:
    content = record.get("content")
    record_type = record.get("record_type")
    if not isinstance(content, dict) or not isinstance(record_type, str):
        return None
    for key in ("memory_key", "key", "subject", "project", "name", "id"):
        value = content.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value):
            return f"{record_type}:{key}:{value}"
    return None


def _semantic_key_map(workspace: Path, active_records: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    apps = workspace / "curation" / "applications.jsonl"
    if apps.exists():
        for row in core.read_jsonl(apps):
            memory_id = row.get("memory_id")
            key = row.get("semantic_key")
            if isinstance(memory_id, str) and memory_id in active_records and isinstance(key, str) and key:
                result.setdefault(key, set()).add(memory_id)
    for memory_id, record in active_records.items():
        key = derive_semantic_key(record)
        if key:
            result.setdefault(key, set()).add(memory_id)
    return result


def _resolve_endpoint(
    content: dict[str, Any],
    prefix: str,
    active_records: dict[str, dict[str, Any]],
    key_map: dict[str, set[str]],
    relationship_id: str,
) -> str:
    id_field = f"{prefix}_memory_id"
    key_field = f"{prefix}_semantic_key"
    memory_id = content.get(id_field)
    semantic_key = content.get(key_field)
    present = int(isinstance(memory_id, str) and bool(memory_id)) + int(isinstance(semantic_key, str) and bool(semantic_key))
    if present != 1:
        raise RoutingError(
            f"dependency relationship {relationship_id} must contain exactly one of {id_field} or {key_field}"
        )
    if isinstance(memory_id, str) and memory_id:
        if memory_id not in active_records:
            raise RoutingError(f"dependency relationship {relationship_id} references unavailable memory {memory_id}")
        return memory_id
    matches = sorted(key_map.get(str(semantic_key), set()))
    if len(matches) != 1:
        state = "missing" if not matches else "ambiguous"
        raise RoutingError(
            f"dependency relationship {relationship_id} has {state} semantic endpoint {semantic_key!r}: {matches}"
        )
    return matches[0]


def dependency_graph(
    workspace: Path,
    records: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    active_records = {
        rid: record for rid, record in records.items()
        if record.get("approval") == "approved" and record.get("lifecycle", {}).get("state") == "active"
    }
    key_map = _semantic_key_map(workspace, active_records)
    graph: dict[str, list[dict[str, str]]] = {}
    for relationship_id, record in sorted(active_records.items()):
        if record.get("record_type") != "relationship":
            continue
        content = record.get("content")
        if not isinstance(content, dict) or content.get("relation") not in DEPENDENCY_RELATIONS:
            continue
        source_id = _resolve_endpoint(content, "from", active_records, key_map, relationship_id)
        target_id = _resolve_endpoint(content, "to", active_records, key_map, relationship_id)
        if source_id == target_id:
            raise RoutingError(f"dependency relationship {relationship_id} is a self-dependency")
        graph.setdefault(source_id, []).append({
            "target_id": target_id,
            "relationship_id": relationship_id,
            "relation": str(content["relation"]),
        })
    for source_id in graph:
        graph[source_id].sort(key=lambda edge: (edge["target_id"], edge["relationship_id"]))
    return graph


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
    policy = profile["dependency_policy"]
    roots = sorted(selected)

    def visit(node_id: str, path: list[str], depth: int) -> None:
        edges = graph.get(node_id, [])
        if not edges:
            return
        if not policy["enabled"]:
            raise RoutingError(f"record {node_id} has required dependencies but dependency expansion is disabled")
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
                    raise RoutingError(f"dependency cycle detected: {' -> '.join([*path, target_id])}")
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
                raise RoutingError(f"required dependency {target_id} for {node_id} is blocked: {reason}")
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
            visit(target_id, [*path, target_id], next_depth)

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
    ensure_routing(workspace)
    policy = core.load_policy(workspace)
    profile = load_profile(workspace, profile_name)
    routing_policy = load_routing_policy(workspace)
    target_name = target_name or routing_policy["default_target"]
    if target_name not in routing_policy["targets"]:
        raise RoutingError(f"unknown routing target: {target_name}")
    target = routing_policy["targets"][target_name]
    if not target["enabled"]:
        raise RoutingError(f"routing target is disabled: {target_name}")

    records_list = core.read_jsonl(workspace / "memory" / "records.jsonl")
    for record in records_list:
        core.validate_memory_record(record, workspace, policy)
    records = {record["id"]: record for record in records_list}
    application_ids = _application_memory_ids(workspace)
    extra_tags = set(extra_tags or set())
    terms: set[str] = set()
    if task:
        terms = task_terms(task, profile["task_selector"])

    selected: dict[str, dict[str, Any]] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    for memory_id, record in sorted(records.items()):
        reason = exclusion_reason(
            record,
            profile=profile,
            target=target,
            routing_policy=routing_policy,
            application_ids=application_ids,
        )
        if reason:
            continue
        matches, tag_matches, task_matches, included_by = _direct_selector(
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

    graph = dependency_graph(workspace, records)
    _expand_dependencies(
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
    store_hash = core.sha256_bytes(core.canonical_bytes(sorted(records_list, key=lambda record: record["id"])))
    routing_meta = {
        "target": target_name,
        "target_kind": target["kind"],
        "effective_sensitivity_ceiling": effective_ceiling(profile, target),
        "task": task or None,
        "task_terms": sorted(terms),
        "profile_sha256": canonical_sha(profile),
        "routing_policy_sha256": canonical_sha(routing_policy),
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
    return {**payload, "canonical_payload_sha256": canonical_sha(payload)}


def write_bundle(bundle: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(core.canonical_bytes(bundle).decode("utf-8") + "\n", encoding="utf-8", newline="\n")


def parse_tags(value: str) -> set[str]:
    return {tag.strip() for tag in value.split(",") if tag.strip()}


def bundle_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-CONTEXT Phase 6 selective bundle router")
    parser.add_argument("workspace")
    parser.add_argument("--profile", default="general")
    parser.add_argument("--tags", default="")
    parser.add_argument("--task")
    parser.add_argument("--target")
    parser.add_argument("--output", required=True)
    return parser


def main_bundle(argv: list[str] | None = None) -> int:
    args = bundle_parser().parse_args(argv)
    try:
        workspace = Path(args.workspace).expanduser().resolve()
        bundle = plan_bundle(
            workspace,
            profile_name=args.profile,
            extra_tags=parse_tags(args.tags),
            task=args.task,
            target_name=args.target,
        )
        output = Path(args.output).expanduser().resolve()
        write_bundle(bundle, output)
        print(json.dumps({
            "output": str(output),
            "records": len(bundle["records"]),
            "sha256": bundle["canonical_payload_sha256"],
            "target": bundle["routing"]["target"],
            "target_kind": bundle["routing"]["target_kind"],
            "task_terms": bundle["routing"]["task_terms"],
        }, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except (core.ContextError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-CONTEXT Phase 6 routing utilities")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("workspace")
    bundle = sub.add_parser("bundle")
    bundle.add_argument("workspace")
    bundle.add_argument("--profile", default="general")
    bundle.add_argument("--tags", default="")
    bundle.add_argument("--task")
    bundle.add_argument("--target")
    bundle.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "init":
        try:
            ensure_routing(Path(args.workspace).expanduser().resolve())
            print(json.dumps({"status": "ok", "routing_dir": str(Path(args.workspace).expanduser().resolve() / "routing")}, indent=2, sort_keys=True))
            return 0
        except (core.ContextError, OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    forwarded = [args.workspace, "--profile", args.profile, "--tags", args.tags]
    if args.task:
        forwarded += ["--task", args.task]
    if args.target:
        forwarded += ["--target", args.target]
    forwarded += ["--output", args.output]
    return main_bundle(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
