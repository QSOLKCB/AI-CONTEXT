#!/usr/bin/env python3
"""Hardened Phase 9 deterministic derived indexes for AI-CONTEXT.

The original Phase 9 implementation is retained in ``indexes_legacy.py`` for audit and
compatibility. This public layer closes review findings around workspace privacy, canonical
snapshot consistency, exact artifact bytes, concurrent rebuilds, and search snapshot reuse.

Indexes remain rebuildable retrieval accelerators only. They never grant memory authority
or disclosure permission.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import ai_context_legacy as core
import indexes_legacy as legacy
from indexes_legacy import *  # noqa: F401,F403

# Public constants/classes are imported above. Explicitly alias private helpers used by the
# hardened layer because star-import deliberately excludes underscore-prefixed names.
IndexError = legacy.IndexError
INDEX_VERSION = legacy.INDEX_VERSION
INDEX_DIR = legacy.INDEX_DIR
INDEX_FILES = legacy.INDEX_FILES
RETRIEVAL_POLICY = legacy.RETRIEVAL_POLICY
TOKENIZATION = legacy.TOKENIZATION
VECTOR_GENERATOR = legacy.VECTOR_GENERATOR
VECTOR_DIMENSIONS = legacy.VECTOR_DIMENSIONS

_PRIVATE_IGNORE_RULES = (
    "indexes/",
    ".indexes-build-*/",
    ".indexes-old-*/",
)
_DEFAULT_PRIVATE_IGNORE_RULES = (
    "vault/",
    "staging/",
    "receipts/",
    "memory/",
    "bundles/",
    "curation/",
    "routing/",
    "enrichment/",
    *_PRIVATE_IGNORE_RULES,
    "*.private.*",
)


def _memory_file_bytes(workspace: Path) -> bytes:
    path = workspace / "memory" / "records.jsonl"
    try:
        return path.read_bytes() if path.exists() else b""
    except OSError as exc:
        raise IndexError(f"cannot read canonical memory snapshot: {exc}") from exc


def _parse_records_bytes(workspace: Path, raw: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IndexError("canonical memory records must be UTF-8 JSONL") from exc

    policy = core.load_policy(workspace)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        value = core.json_loads_strict(line, label=f"memory/records.jsonl:{line_no}")
        if not isinstance(value, dict):
            raise IndexError(f"canonical memory row must be an object: line {line_no}")
        core.validate_memory_record(value, workspace, policy)
        rid = value.get("id")
        if not isinstance(rid, str) or not rid:
            raise IndexError("canonical memory row missing id")
        if rid in seen:
            raise IndexError(f"duplicate canonical memory id: {rid}")
        seen.add(rid)
        records.append(value)

    ordered = sorted(records, key=lambda row: row["id"])
    active = [
        row
        for row in ordered
        if row.get("approval") == "approved"
        and isinstance(row.get("lifecycle"), dict)
        and row["lifecycle"].get("state") == "active"
    ]
    return ordered, active


def _fingerprint_from_records(
    records: list[dict[str, Any]], active: list[dict[str, Any]]
) -> dict[str, Any]:
    core_value = {
        "protocol": "AI-CONTEXT/DERIVED-SOURCE-FINGERPRINT",
        "schema_version": INDEX_VERSION,
        "canonicalizer": "python-json-v0.1",
        "retrieval_policy": RETRIEVAL_POLICY,
        "canonical_store_sha256": legacy.canonical_sha(records),
        "canonical_record_count": len(records),
        "retrieval_records_sha256": legacy.canonical_sha(active),
        "retrieval_record_count": len(active),
    }
    return {"id": legacy.stable_id("index-source", core_value), **core_value}


def _capture_records_snapshot(
    workspace: Path,
) -> tuple[bytes, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Capture and validate one exact canonical-memory byte snapshot.

    The bytes are re-read after parsing/validation so a concurrent writer cannot make one
    snapshot silently span two versions of ``records.jsonl``.
    """
    core.ensure_workspace(workspace)
    raw = _memory_file_bytes(workspace)
    records, active = _parse_records_bytes(workspace, raw)
    if _memory_file_bytes(workspace) != raw:
        raise IndexError("canonical memory changed while the index snapshot was being captured")
    return raw, records, active, _fingerprint_from_records(records, active)


def source_fingerprint(workspace: Path) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    _raw, _records, _active, fingerprint = _capture_records_snapshot(workspace)
    return fingerprint


def _ensure_private_index_path(workspace: Path) -> None:
    """Establish the private index path only after workspace validation succeeds."""
    core.ensure_workspace(workspace)
    gitignore = workspace / ".gitignore"
    if gitignore.is_symlink():
        raise IndexError("workspace .gitignore must not be a symlink")
    if gitignore.exists() and not gitignore.is_file():
        raise IndexError("workspace .gitignore must be a regular file")

    if gitignore.exists():
        try:
            lines = gitignore.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise IndexError(f"cannot read workspace .gitignore: {exc}") from exc
        changed = False
        for rule in _PRIVATE_IGNORE_RULES:
            if rule not in lines:
                lines.append(rule)
                changed = True
        if changed:
            legacy._atomic_write(
                gitignore,
                ("\n".join(lines) + "\n").encode("utf-8"),
            )
    else:
        legacy._atomic_write(
            gitignore,
            ("\n".join(_DEFAULT_PRIVATE_IGNORE_RULES) + "\n").encode("utf-8"),
        )

    index_dir = workspace / INDEX_DIR
    if index_dir.is_symlink():
        raise IndexError("indexes/ must not be a symlink")


def _build_vector_from_active(
    active: list[dict[str, Any]], fingerprint: dict[str, Any]
) -> dict[str, Any]:
    records = []
    for record in active:
        tokens = legacy.token_sequence(legacy.record_text(record))
        records.append(
            {
                "memory_id": record["id"],
                "token_count": len(tokens),
                "vector": legacy._sparse_vector(tokens),
            }
        )
    core_value = {
        "protocol": "AI-CONTEXT/VECTOR-INDEX",
        "schema_version": INDEX_VERSION,
        "index_kind": "vector",
        "authority": "derived-retrieval-only",
        "source_fingerprint": fingerprint,
        "generator": {
            "id": VECTOR_GENERATOR,
            "dimensions": VECTOR_DIMENSIONS,
            "tokenization": TOKENIZATION,
            "weights": "integer-token-counts",
        },
        "records": records,
    }
    return {"id": legacy.stable_id("vector-index", core_value), **core_value}


def _build_graph_from_active(
    active: list[dict[str, Any]], fingerprint: dict[str, Any]
) -> dict[str, Any]:
    active_ids = {record["id"] for record in active}
    node_map: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []

    def add_node(node_id: str, node: dict[str, Any]) -> None:
        current = node_map.get(node_id)
        if current is not None and core.canonical_bytes(current) != core.canonical_bytes(node):
            raise IndexError(f"graph node collision: {node_id}")
        node_map[node_id] = node

    for record in active:
        memory_node = f"memory:{record['id']}"
        add_node(
            memory_node,
            {
                "id": memory_node,
                "kind": "memory",
                "memory_id": record["id"],
                "record_type": record["record_type"],
                "sensitivity": record["sensitivity"],
            },
        )
        for source_ref in sorted(record.get("source_refs", [])):
            node_id = f"observation:{source_ref}"
            add_node(node_id, {"id": node_id, "kind": "observation_ref", "ref": source_ref})
            edges.append(
                {
                    "from": memory_node,
                    "to": node_id,
                    "relation": "source_ref",
                    "relationship_memory_id": None,
                }
            )
        lifecycle = record.get("lifecycle", {})
        supersedes = lifecycle.get("supersedes") if isinstance(lifecycle, dict) else None
        if isinstance(supersedes, str) and supersedes in active_ids:
            edges.append(
                {
                    "from": memory_node,
                    "to": f"memory:{supersedes}",
                    "relation": "supersedes",
                    "relationship_memory_id": None,
                }
            )

    for record in active:
        if record.get("record_type") != "relationship":
            continue
        content = record.get("content")
        if not isinstance(content, dict):
            unresolved.append(
                {"memory_id": record["id"], "reason": "relationship-content-not-object"}
            )
            continue
        relation = content.get("relation")
        if not isinstance(relation, str) or not relation:
            unresolved.append(
                {"memory_id": record["id"], "reason": "relationship-name-missing"}
            )
            continue
        left = legacy._relationship_endpoint(content, "from")
        right = legacy._relationship_endpoint(content, "to")
        if left is None or right is None:
            unresolved.append(
                {
                    "memory_id": record["id"],
                    "reason": "relationship-endpoint-ambiguous-or-missing",
                }
            )
            continue
        endpoint_nodes: list[str] = []
        blocked = False
        for kind, value in (left, right):
            if kind == "memory":
                if value not in active_ids:
                    blocked = True
                    break
                endpoint_nodes.append(f"memory:{value}")
            else:
                node_id = f"semantic:{value}"
                add_node(node_id, {"id": node_id, "kind": "semantic_key", "ref": value})
                endpoint_nodes.append(node_id)
        if blocked:
            unresolved.append(
                {
                    "memory_id": record["id"],
                    "reason": "relationship-memory-endpoint-not-active",
                }
            )
            continue
        edges.append(
            {
                "from": endpoint_nodes[0],
                "to": endpoint_nodes[1],
                "relation": relation,
                "relationship_memory_id": record["id"],
            }
        )

    nodes = [node_map[key] for key in sorted(node_map)]
    edges = sorted(
        edges,
        key=lambda row: (
            row["from"],
            row["relation"],
            row["to"],
            row["relationship_memory_id"] or "",
        ),
    )
    unresolved = sorted(unresolved, key=lambda row: (row["memory_id"], row["reason"]))
    core_value = {
        "protocol": "AI-CONTEXT/GRAPH-INDEX",
        "schema_version": INDEX_VERSION,
        "index_kind": "graph",
        "authority": "derived-retrieval-only",
        "source_fingerprint": fingerprint,
        "nodes": nodes,
        "edges": edges,
        "unresolved_relationships": unresolved,
    }
    return {"id": legacy.stable_id("graph-index", core_value), **core_value}


def _build_search_from_active(
    active: list[dict[str, Any]], fingerprint: dict[str, Any]
) -> dict[str, Any]:
    postings: dict[str, set[str]] = {}
    record_stats = []
    for record in active:
        tokens = legacy.token_sequence(legacy.record_text(record))
        unique = sorted(set(tokens))
        for token in unique:
            postings.setdefault(token, set()).add(record["id"])
        record_stats.append(
            {
                "memory_id": record["id"],
                "token_count": len(tokens),
                "unique_token_count": len(unique),
            }
        )
    posting_rows = [
        {"token": token, "memory_ids": sorted(postings[token])}
        for token in sorted(postings)
    ]
    core_value = {
        "protocol": "AI-CONTEXT/SEARCH-CACHE",
        "schema_version": INDEX_VERSION,
        "index_kind": "search",
        "authority": "derived-retrieval-only",
        "source_fingerprint": fingerprint,
        "tokenization": TOKENIZATION,
        "postings": posting_rows,
        "records": record_stats,
    }
    return {"id": legacy.stable_id("search-cache", core_value), **core_value}


def _check_optional_fingerprint(
    supplied: dict[str, Any] | None, expected: dict[str, Any]
) -> dict[str, Any]:
    if supplied is None:
        return expected
    if core.canonical_bytes(supplied) != core.canonical_bytes(expected):
        raise IndexError("supplied source fingerprint does not match the captured canonical snapshot")
    return supplied


def build_vector_index(
    workspace: Path, fingerprint: dict[str, Any] | None = None
) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    _raw, _records, active, expected = _capture_records_snapshot(workspace)
    return _build_vector_from_active(active, _check_optional_fingerprint(fingerprint, expected))


def build_graph_index(
    workspace: Path, fingerprint: dict[str, Any] | None = None
) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    _raw, _records, active, expected = _capture_records_snapshot(workspace)
    return _build_graph_from_active(active, _check_optional_fingerprint(fingerprint, expected))


def build_search_cache(
    workspace: Path, fingerprint: dict[str, Any] | None = None
) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    _raw, _records, active, expected = _capture_records_snapshot(workspace)
    return _build_search_from_active(active, _check_optional_fingerprint(fingerprint, expected))


def _build_from_snapshot(
    active: list[dict[str, Any]], fingerprint: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    artifacts = {
        "vector": _build_vector_from_active(active, fingerprint),
        "graph": _build_graph_from_active(active, fingerprint),
        "search": _build_search_from_active(active, fingerprint),
    }
    manifest = legacy.build_manifest(fingerprint, artifacts)
    return manifest, artifacts


def _build_all(workspace: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    workspace = workspace.expanduser().resolve()
    _raw, _records, active, fingerprint = _capture_records_snapshot(workspace)
    return _build_from_snapshot(active, fingerprint)


def build_indexes(workspace: Path) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()

    # Validate the workspace before any privacy metadata is changed, then establish the
    # ignore boundary before generating private projection bytes.
    core.ensure_workspace(workspace)
    _ensure_private_index_path(workspace)

    memory_raw, _records, active, fingerprint = _capture_records_snapshot(workspace)
    manifest, artifacts = _build_from_snapshot(active, fingerprint)

    temp = Path(tempfile.mkdtemp(prefix=".indexes-build-", dir=workspace))
    backup: Path | None = None
    index_dir = workspace / INDEX_DIR
    try:
        for kind in ("vector", "graph", "search"):
            legacy._write_json(temp / INDEX_FILES[kind], artifacts[kind])
        legacy._write_json(temp / INDEX_FILES["manifest"], manifest)

        # A canonical mutation during projection construction invalidates this build before
        # the old index set is touched. All projections above came from the same in-memory
        # snapshot, and that exact snapshot must still be current at publication time.
        if _memory_file_bytes(workspace) != memory_raw:
            raise IndexError("canonical memory changed during index build; rebuild from a fresh snapshot")

        if index_dir.exists():
            if not index_dir.is_dir() or index_dir.is_symlink():
                raise IndexError("indexes path must be a normal directory")
            backup = Path(tempfile.mkdtemp(prefix=".indexes-old-", dir=workspace))
            backup.rmdir()
            os.replace(index_dir, backup)
        try:
            os.replace(temp, index_dir)
        except Exception:
            if backup is not None and backup.exists() and not index_dir.exists():
                os.replace(backup, index_dir)
            raise
        if backup is not None and backup.exists():
            shutil.rmtree(backup)

        return {
            "status": "ok",
            "manifest_id": manifest["id"],
            "source_fingerprint_id": fingerprint["id"],
            "retrieval_records": fingerprint["retrieval_record_count"],
            "vector_records": len(artifacts["vector"]["records"]),
            "graph_nodes": len(artifacts["graph"]["nodes"]),
            "graph_edges": len(artifacts["graph"]["edges"]),
            "search_terms": len(artifacts["search"]["postings"]),
        }
    finally:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)


def _read_canonical_json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IndexError(f"cannot read index artifact {path}: {exc}") from exc
    try:
        value = core.json_loads_strict(raw.decode("utf-8"), label=str(path))
    except UnicodeDecodeError as exc:
        raise IndexError(f"index artifact must be UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise IndexError(f"index artifact must be an object: {path}")
    if raw != core.canonical_bytes(value) + b"\n":
        raise IndexError(f"index artifact is not in canonical byte encoding: {path.name}")
    return raw, value


def _index_dir_identity(path: Path) -> tuple[int, int]:
    stat_result = path.stat()
    return stat_result.st_dev, stat_result.st_ino


def _load_index_snapshot(
    workspace: Path,
) -> tuple[dict[str, bytes], dict[str, dict[str, Any]]]:
    index_dir = workspace / INDEX_DIR
    if not index_dir.is_dir() or index_dir.is_symlink():
        raise IndexError("derived indexes are missing or unsafe; rebuild them")

    before = _index_dir_identity(index_dir)
    entries = list(index_dir.iterdir())
    if any(path.is_symlink() for path in entries):
        raise IndexError("derived index files must not be symlinks")
    if any(not path.is_file() for path in entries):
        raise IndexError("derived indexes directory contains a non-file entry")
    actual_files = {path.name for path in entries}
    expected_files = set(INDEX_FILES.values())
    if actual_files != expected_files:
        raise IndexError(
            f"derived index file set mismatch: expected={sorted(expected_files)} "
            f"actual={sorted(actual_files)}"
        )

    raw_by_kind: dict[str, bytes] = {}
    value_by_kind: dict[str, dict[str, Any]] = {}
    for kind in ("vector", "graph", "search", "manifest"):
        raw, value = _read_canonical_json_bytes(index_dir / INDEX_FILES[kind])
        raw_by_kind[kind] = raw
        value_by_kind[kind] = value

    if _index_dir_identity(index_dir) != before:
        raise IndexError("derived index set changed during validation")
    return raw_by_kind, value_by_kind


def _validate_index_snapshot(
    workspace: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    workspace = workspace.expanduser().resolve()
    core.ensure_workspace(workspace)
    raw, values = _load_index_snapshot(workspace)
    vector = values["vector"]
    graph = values["graph"]
    search = values["search"]
    manifest = values["manifest"]

    legacy._validate_artifact_identity(
        vector, "AI-CONTEXT/VECTOR-INDEX", "vector-index", "vector"
    )
    legacy._validate_artifact_identity(
        graph, "AI-CONTEXT/GRAPH-INDEX", "graph-index", "graph"
    )
    legacy._validate_artifact_identity(
        search, "AI-CONTEXT/SEARCH-CACHE", "search-cache", "search"
    )
    if (
        manifest.get("protocol") != "AI-CONTEXT/INDEX-MANIFEST"
        or manifest.get("schema_version") != INDEX_VERSION
    ):
        raise IndexError("invalid index manifest protocol/schema")
    if manifest.get("authority") != "derived-retrieval-only":
        raise IndexError("index manifest claims invalid authority")
    legacy._validate_source_fingerprint(manifest.get("source_fingerprint"))
    manifest_core = {key: item for key, item in manifest.items() if key != "id"}
    if manifest.get("id") != legacy.stable_id("index-manifest", manifest_core):
        raise IndexError("index manifest id/hash mismatch")

    rows = manifest.get("artifacts")
    if not isinstance(rows, list) or len(rows) != 3:
        raise IndexError("index manifest artifact list is invalid")
    row_by_kind = {
        row.get("kind"): row for row in rows if isinstance(row, dict) and isinstance(row.get("kind"), str)
    }
    if set(row_by_kind) != {"vector", "graph", "search"}:
        raise IndexError("index manifest artifact kinds are incomplete or duplicated")
    for kind in ("vector", "graph", "search"):
        row = row_by_kind[kind]
        if row.get("path") != INDEX_FILES[kind] or row.get("artifact_id") != values[kind].get("id"):
            raise IndexError(f"index manifest {kind} identity/path mismatch")
        if row.get("sha256") != core.sha256_bytes(raw[kind]) or row.get("bytes") != len(raw[kind]):
            raise IndexError(f"index manifest {kind} hash/byte-length mismatch")

    # Build the expected projections from one exact canonical snapshot and ensure that same
    # canonical byte snapshot remains current through the end of validation.
    memory_raw, _records, active, fingerprint = _capture_records_snapshot(workspace)
    expected_manifest, expected = _build_from_snapshot(active, fingerprint)
    if core.canonical_bytes(fingerprint) != core.canonical_bytes(manifest["source_fingerprint"]):
        raise IndexError("derived indexes are stale for the current canonical memory store")
    for kind in ("vector", "graph", "search"):
        if core.canonical_bytes(expected[kind]) != core.canonical_bytes(values[kind]):
            raise IndexError(f"{kind} index does not match the deterministic current projection")
    if core.canonical_bytes(expected_manifest) != core.canonical_bytes(manifest):
        raise IndexError("index manifest is stale or inconsistent")
    if _memory_file_bytes(workspace) != memory_raw:
        raise IndexError("canonical memory changed during index validation")

    summary = {
        "status": "ok",
        "manifest_id": manifest["id"],
        "source_fingerprint_id": manifest["source_fingerprint"]["id"],
        "retrieval_records": manifest["source_fingerprint"]["retrieval_record_count"],
        "vector_records": len(vector["records"]),
        "graph_nodes": len(graph["nodes"]),
        "graph_edges": len(graph["edges"]),
        "search_terms": len(search["postings"]),
    }
    return summary, {"vector": vector, "graph": graph, "search": search, "manifest": manifest}


def validate_indexes(workspace: Path) -> dict[str, Any]:
    summary, _values = _validate_index_snapshot(workspace)
    return summary


def search_cache(workspace: Path, query: str, *, limit: int = 10) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise IndexError("search query must be non-empty")
    if limit < 1 or limit > 100:
        raise IndexError("search limit must be from 1 to 100")

    validation, values = _validate_index_snapshot(workspace)
    cache = values["search"]
    terms = sorted(set(legacy.token_sequence(query)))
    if not terms:
        raise IndexError("search query produced no meaningful terms")
    posting_map = {row["token"]: row["memory_ids"] for row in cache["postings"]}
    scores: Counter[str] = Counter()
    matches: dict[str, list[str]] = {}
    for term in terms:
        for memory_id in posting_map.get(term, []):
            scores[memory_id] += 1
            matches.setdefault(memory_id, []).append(term)
    ranked = sorted(scores, key=lambda memory_id: (-scores[memory_id], memory_id))[:limit]
    return {
        "status": "ok",
        "query_terms": terms,
        "results": [
            {
                "memory_id": memory_id,
                "score": scores[memory_id],
                "matched_terms": sorted(matches[memory_id]),
            }
            for memory_id in ranked
        ],
        "source_fingerprint_id": validation["source_fingerprint_id"],
        "authority": "candidate-retrieval-only",
        "disclosure_requires_routing": True,
    }


# Make compatibility callers that import indexes_legacy through this process see the hardened
# public behavior as well.
legacy.source_fingerprint = source_fingerprint
legacy.build_vector_index = build_vector_index
legacy.build_graph_index = build_graph_index
legacy.build_search_cache = build_search_cache
legacy._build_all = _build_all
legacy.build_indexes = build_indexes
legacy.validate_indexes = validate_indexes
legacy.search_cache = search_cache


def main() -> int:
    args = legacy.build_parser().parse_args()
    try:
        workspace = Path(args.workspace).expanduser().resolve()
        if args.command == "build":
            result = build_indexes(workspace)
        elif args.command == "validate":
            result = validate_indexes(workspace)
        elif args.command == "search":
            result = search_cache(workspace, args.query, limit=args.limit)
        else:
            raise IndexError(f"unsupported index command: {args.command}")
    except (IndexError, core.ContextError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
