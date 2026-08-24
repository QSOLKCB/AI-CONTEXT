#!/usr/bin/env python3
"""AI-CONTEXT v1 release-candidate public-tree audit.

The audit is intentionally scoped to Git-tracked public release bytes. It rejects
private-workspace path classes, credential/key material, restore/storage artifacts,
build/cache debris, symlinks/submodules, and secret-shaped text. It does not claim
to inspect untracked local files, prior Git history, remote backups, or external copies.
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
MAX_TEXT_SCAN_BYTES = 4 * 1024 * 1024

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
    ".tar",
    ".tar.gz",
    ".tgz",
    ".7z",
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,255}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b")),
    ("xai-style-key", re.compile(r"\bxai-[A-Za-z0-9_-]{24,}\b", re.IGNORECASE)),
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}\b", re.IGNORECASE)),
)

SYNTHETIC_MARKERS = (
    "synthetic",
    "example",
    "dummy",
    "fake",
    "placeholder",
    "redacted",
    "test-only",
    "test_",
    "test-",
)


class AuditError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tracked_paths(root: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AuditError("release audit requires a Git checkout and git ls-files") from exc
    return sorted(item.decode("utf-8", "strict") for item in proc.stdout.split(b"\0") if item)


def _git_commit(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = proc.stdout.strip()
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


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


def _synthetic_context(path: str, line: str) -> bool:
    if not (path.startswith("tests/") or path.startswith("fixtures/")):
        return False
    folded = line.casefold()
    return any(marker in folded for marker in SYNTHETIC_MARKERS)


def _text_findings(rel: str, data: bytes) -> list[dict[str, str]]:
    if len(data) > MAX_TEXT_SCAN_BYTES or b"\0" in data:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []

    findings: list[dict[str, str]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
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


def audit_paths(root: Path, paths: Iterable[str], *, commit: str | None = None) -> dict[str, Any]:
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
        tree_rows.append(f"{rel}\0{len(data)}\0{digest}\n".encode("utf-8"))
        findings.extend(_text_findings(rel, data))

    findings.sort(key=lambda item: (item["path"], item["kind"], item["detail"]))
    tree_sha = sha256_bytes(b"".join(tree_rows))
    receipt: dict[str, Any] = {
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
            "tracked-symlinks",
            "generated-build-cache-and-archive-debris",
        ],
        "findings": findings,
        "status": "ok" if not findings else "failed",
        "claim": AUDIT_CLAIM if not findings else "release-audit-findings-present",
        "limitations": [
            "scope is the current Git-tracked tree only",
            "untracked local files and external copies are outside scope",
            "prior Git history is outside this release-tree claim",
            "secret scanning is pattern-based and cannot prove semantic absence of every possible private fact",
        ],
    }
    return receipt


def audit_repository(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    return audit_paths(root, tracked_paths(root), commit=_git_commit(root))


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-CONTEXT v1 tracked public-tree release audit")
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
