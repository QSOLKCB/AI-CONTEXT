#!/usr/bin/env python3
"""AI-CONTEXT v1 release-candidate public-tree audit.

The release claim is bound to the exact Git commit reported in the receipt. Repository
audits resolve the checkout top-level, enumerate that commit's tree, and scan Git blob
bytes rather than mutable working-tree files. The audit rejects private-workspace path
classes, credential/key material, restore/storage artifacts, build/cache debris,
symlinks/submodules, and secret-shaped bytes. It does not claim to inspect untracked
local files, prior Git history, remote backups, or external copies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

AUDIT_PROTOCOL = "AI-CONTEXT/RELEASE-AUDIT"
AUDIT_VERSION = "0.1.0"
AUDIT_SCOPE = "tracked-public-git-tree"
AUDIT_CLAIM = "no-forbidden-private-or-runtime-artifacts-detected-in-tracked-tree"

# A blob larger than this limit fails the release audit rather than being silently skipped.
# This is a resource-safety limit, not a statement that large files are harmless.
MAX_SECRET_SCAN_BYTES = 32 * 1024 * 1024

FORBIDDEN_TOP_LEVEL = {
    "workspace",
    "workspaces",
    "vault",
    "imports",
    "staging",
    "memory",
    "bundles",
    "curation",
    "routing",
    "profiles",
    "receipts",
    "indexes",
}

FORBIDDEN_COMPONENTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "target",
    "build",
    "dist",
    "cache",
    ".cache",
    ".idea",
}

FORBIDDEN_BASENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
    "service_account.json",
    "grok_data.zip",
    "chatgpt_export.zip",
    "claude_export.zip",
    "takeout.zip",
}

FORBIDDEN_SUFFIXES = (
    ".aicr",
    ".private.json",
    ".private.jsonl",
    ".private.zip",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".kdbx",
    ".sqlite3",
    ".db-wal",
    ".db-shm",
)

GENERATED_ARCHIVE_SUFFIXES = (
    ".tar.gz",
    ".tar.xz",
    ".tar.bz2",
    ".tgz",
    ".txz",
    ".tbz",
    ".tbz2",
    ".tar",
    ".rar",
    ".7z",
    ".gz",
    ".bz2",
    ".xz",
)

# These patterns are intentionally ASCII/byte-safe so NUL-containing or otherwise binary
# blobs do not escape secret-shape scanning merely because UTF-8 decoding is unavailable.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private-key-block", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,255}\b")),
    ("google-api-key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("openai-style-key", re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}\b")),
    ("xai-style-key", re.compile(rb"\bxai-[A-Za-z0-9_-]{24,}\b", re.IGNORECASE)),
    ("bearer-token", re.compile(rb"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}\b", re.IGNORECASE)),
)

# Secret-shaped fixtures may be exempted only by an explicit local annotation. Generic test
# identifiers such as "test_key" are deliberately NOT evidence that a credential is fake.
SYNTHETIC_ANNOTATIONS = (
    b"synthetic test-only",
    b"synthetic fixture",
    b"deterministic fake credential",
    b"release-audit synthetic-ok",
)


class AuditError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_git(root: Path, *args: str, text: bool = False) -> subprocess.CompletedProcess[Any]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AuditError(f"release audit git command failed: {' '.join(args)}") from exc


def _git_toplevel(start: Path) -> Path:
    proc = _run_git(start.expanduser().resolve(), "rev-parse", "--show-toplevel", text=True)
    value = proc.stdout.strip()
    if not value:
        raise AuditError("release audit could not resolve Git repository top level")
    return Path(value).resolve()


def _git_commit(root: Path) -> str:
    proc = _run_git(root, "rev-parse", "HEAD", text=True)
    value = proc.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise AuditError("release audit requires a concrete 40-hex HEAD commit")
    return value


def _git_tree_entries(root: Path, commit: str) -> list[dict[str, str]]:
    proc = _run_git(root, "ls-tree", "-rz", "-r", "--full-tree", commit)
    entries: list[dict[str, str]] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            meta, raw_path = raw.split(b"\t", 1)
            mode, object_type, object_id = meta.decode("ascii").split(" ", 2)
            path = raw_path.decode("utf-8", "strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise AuditError("malformed or non-UTF-8 path in Git release tree") from exc
        entries.append(
            {
                "mode": mode,
                "object_type": object_type,
                "object_id": object_id,
                "path": path,
            }
        )
    return sorted(entries, key=lambda row: row["path"])


def tracked_paths(root: Path) -> list[str]:
    """Return paths from the exact HEAD tree, resolving a subdirectory to repo top-level."""
    top = _git_toplevel(root)
    commit = _git_commit(top)
    return [row["path"] for row in _git_tree_entries(top, commit)]


def _git_blob_size(root: Path, object_id: str) -> int:
    proc = _run_git(root, "cat-file", "-s", object_id, text=True)
    try:
        value = int(proc.stdout.strip())
    except ValueError as exc:
        raise AuditError(f"invalid Git blob size for {object_id}") from exc
    if value < 0:
        raise AuditError(f"negative Git blob size for {object_id}")
    return value


def _git_blob(root: Path, object_id: str) -> bytes:
    return bytes(_run_git(root, "cat-file", "blob", object_id).stdout)


def _path_findings(rel: str) -> list[dict[str, str]]:
    path = PurePosixPath(rel)
    parts = path.parts
    lower_parts = tuple(part.casefold() for part in parts)
    basename = path.name.casefold()
    findings: list[dict[str, str]] = []

    if parts and lower_parts[0] in FORBIDDEN_TOP_LEVEL:
        findings.append({"kind": "private-workspace-path", "path": rel, "detail": lower_parts[0]})
    if any(part in FORBIDDEN_COMPONENTS for part in lower_parts):
        findings.append({"kind": "generated-runtime-path", "path": rel, "detail": "forbidden path component"})
    if basename in FORBIDDEN_BASENAMES or basename.startswith(".env."):
        findings.append({"kind": "credential-or-raw-export-filename", "path": rel, "detail": basename})
    if any(basename.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        findings.append({"kind": "private-or-key-artifact-filename", "path": rel, "detail": basename})
    if any(rel.casefold().endswith(suffix) for suffix in GENERATED_ARCHIVE_SUFFIXES):
        findings.append({"kind": "generated-archive", "path": rel, "detail": basename})
    if basename.endswith(".zip") and not rel.startswith("fixtures/"):
        findings.append({"kind": "generated-or-raw-zip", "path": rel, "detail": basename})
    return findings


def _synthetic_context(path: str, line: bytes) -> bool:
    if not (path.startswith("tests/") or path.startswith("fixtures/")):
        return False
    folded = line.casefold()
    return any(marker in folded for marker in SYNTHETIC_ANNOTATIONS)


def _secret_findings(rel: str, data: bytes) -> list[dict[str, str]]:
    if len(data) > MAX_SECRET_SCAN_BYTES:
        return [
            {
                "kind": "unscanned-oversized-tracked-file",
                "path": rel,
                "detail": f"{len(data)} bytes exceeds secret-scan limit {MAX_SECRET_SCAN_BYTES}",
            }
        ]

    findings: list[dict[str, str]] = []
    # bytes.splitlines() works for binary/NUL-containing input and keeps the scan byte-safe.
    for line_no, line in enumerate(data.splitlines() or [data], 1):
        for name, pattern in SECRET_PATTERNS:
            if not pattern.search(line):
                continue
            if _synthetic_context(rel, line):
                continue
            findings.append(
                {
                    "kind": "secret-shaped-text",
                    "path": rel,
                    "detail": f"{name} at line {line_no}",
                }
            )
    return findings


def _receipt(
    *,
    commit: str | None,
    count: int,
    total_bytes: int,
    tree_rows: list[bytes],
    findings: list[dict[str, str]],
) -> dict[str, Any]:
    findings.sort(key=lambda item: (item["path"], item["kind"], item["detail"]))
    tree_sha = sha256_bytes(b"".join(tree_rows))
    return {
        "protocol": AUDIT_PROTOCOL,
        "schema_version": AUDIT_VERSION,
        "audit_scope": AUDIT_SCOPE,
        "git_commit": commit,
        "tracked_files": count,
        "tracked_bytes": total_bytes,
        "tracked_tree_sha256": tree_sha,
        "categories_checked": [
            "private-workspace-paths",
            "raw-export-and-restore-artifact-filenames",
            "credentials-and-private-key-filenames",
            "secret-shaped-text",
            "tracked-symlinks-and-submodules",
            "generated-build-cache-and-archive-debris",
        ],
        "findings": findings,
        "status": "ok" if not findings else "failed",
        "claim": AUDIT_CLAIM if not findings else "release-audit-findings-present",
        "limitations": [
            "scope is the exact Git commit tree named by git_commit",
            "untracked local files and external copies are outside scope",
            "prior Git history is outside this release-tree claim",
            "secret scanning is pattern-based and cannot prove semantic absence of every possible private fact",
            f"tracked blobs larger than {MAX_SECRET_SCAN_BYTES} bytes fail closed instead of being skipped",
        ],
    }


def audit_paths(root: Path, paths: Iterable[str], *, commit: str | None = None) -> dict[str, Any]:
    """Audit explicit filesystem paths for unit/conformance tests.

    Production release claims use ``audit_repository`` below, which reads commit-bound Git
    blobs. This helper remains useful for isolated scanner tests and deliberately does not
    claim that arbitrary filesystem bytes are bound to ``commit``.
    """
    root = root.resolve()
    findings: list[dict[str, str]] = []
    tree_rows: list[bytes] = []
    total_bytes = 0
    count = 0

    for rel in sorted(set(paths)):
        if not rel or rel.startswith("/") or ".." in PurePosixPath(rel).parts:
            findings.append({"kind": "unsafe-tracked-path", "path": rel, "detail": "non-relative or traversal path"})
            continue
        path = root / rel
        count += 1
        findings.extend(_path_findings(rel))
        if path.is_symlink():
            findings.append({"kind": "tracked-symlink", "path": rel, "detail": "release tree symlinks are forbidden"})
            continue
        if not path.is_file():
            findings.append({"kind": "missing-or-nonfile-tracked-entry", "path": rel, "detail": "tracked path is not a regular file"})
            continue
        data = path.read_bytes()
        total_bytes += len(data)
        digest = sha256_bytes(data)
        tree_rows.append(f"{rel}\0regular\0{len(data)}\0{digest}\n".encode("utf-8"))
        findings.extend(_secret_findings(rel, data))

    return _receipt(
        commit=commit,
        count=count,
        total_bytes=total_bytes,
        tree_rows=tree_rows,
        findings=findings,
    )


def audit_repository(root: Path) -> dict[str, Any]:
    """Audit the exact HEAD commit tree for the repository containing ``root``."""
    top = _git_toplevel(root)
    commit = _git_commit(top)
    entries = _git_tree_entries(top, commit)
    findings: list[dict[str, str]] = []
    tree_rows: list[bytes] = []
    total_bytes = 0

    for entry in entries:
        rel = entry["path"]
        mode = entry["mode"]
        object_type = entry["object_type"]
        object_id = entry["object_id"]
        findings.extend(_path_findings(rel))

        if mode == "120000":
            findings.append({"kind": "tracked-symlink", "path": rel, "detail": "release tree symlinks are forbidden"})
            tree_rows.append(f"{rel}\0{mode}\0{object_type}\0{object_id}\n".encode("utf-8"))
            continue
        if mode == "160000" or object_type == "commit":
            findings.append({"kind": "tracked-submodule", "path": rel, "detail": "release tree submodules are forbidden"})
            tree_rows.append(f"{rel}\0{mode}\0{object_type}\0{object_id}\n".encode("utf-8"))
            continue
        if object_type != "blob" or not mode.startswith("100"):
            findings.append({"kind": "unsupported-git-tree-entry", "path": rel, "detail": f"mode={mode} type={object_type}"})
            tree_rows.append(f"{rel}\0{mode}\0{object_type}\0{object_id}\n".encode("utf-8"))
            continue

        size = _git_blob_size(top, object_id)
        total_bytes += size
        if size > MAX_SECRET_SCAN_BYTES:
            findings.append(
                {
                    "kind": "unscanned-oversized-tracked-file",
                    "path": rel,
                    "detail": f"{size} bytes exceeds secret-scan limit {MAX_SECRET_SCAN_BYTES}",
                }
            )
            tree_rows.append(f"{rel}\0{mode}\0{object_id}\0{size}\0oversized\n".encode("utf-8"))
            continue

        data = _git_blob(top, object_id)
        if len(data) != size:
            raise AuditError(f"Git blob size changed while auditing {rel}")
        digest = sha256_bytes(data)
        tree_rows.append(f"{rel}\0{mode}\0{object_id}\0{size}\0{digest}\n".encode("utf-8"))
        findings.extend(_secret_findings(rel, data))

    return _receipt(
        commit=commit,
        count=len(entries),
        total_bytes=total_bytes,
        tree_rows=tree_rows,
        findings=findings,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-CONTEXT v1 exact-commit public-tree release audit")
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--receipt", help="optional output path for the JSON audit receipt")
    args = parser.parse_args()
    try:
        receipt = audit_repository(Path(args.root))
    except (AuditError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.receipt:
        output = Path(args.receipt).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return 0 if receipt["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
