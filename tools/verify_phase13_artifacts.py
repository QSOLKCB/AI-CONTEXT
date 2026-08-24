#!/usr/bin/env python3
"""Verify the three-file AI-CONTEXT v1.0.0 Zenodo archival surface."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ZIP = "AI-CONTEXT-1.0.0-source.zip"
OVERVIEW_PDF = "AI-CONTEXT-v1.0.0-Overview.pdf"
RELEASE_NOTES = "RELEASE-NOTES.md"
REFERENCE_TAG = "v1.0.0"
REFERENCE_COMMIT = "53d7d69dfacecf6f8605f5b6a51b2c68ee66572a"
REFERENCE_TREE = "2c0592cbd074d7596e70681cc5ed869d6b9b00e4"
FORMAL_COMMIT = "b5cf9ae50400b30ec51489bf4d1b51d431f08252"
DOI = "10.5281/zenodo.22081189"
FIXED_ZIP_DT = (1980, 1, 1, 0, 0, 0)
EXPECTED_FILE_MODE = 0o100644
ALLOWED_TOP = {"reference-v1.0.0", "formal", "ARCHIVE-MANIFEST.json"}
FORBIDDEN_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".cache", "cache", "build", "dist",
    "workspace", "vault", "staging", "memory", "curation", "routing", "receipts", "indexes",
}
FORBIDDEN_SUFFIXES = (".aicr", ".pem", ".key", ".p12", ".pfx", ".kdbx", ".sqlite", ".sqlite3", ".db")
SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|bearer)[\"' ]*[:=][\"' ]*[A-Za-z0-9_./+\-=]{16,}"),
]
SYNTHETIC_MARKERS = (b"synthetic", b"dummy", b"fake", b"placeholder", b"redacted", b"test-only", b"test only")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_member(name: str) -> PurePosixPath:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise RuntimeError(f"unsafe ZIP path: {name}")
    if p.parts[0] not in ALLOWED_TOP:
        raise RuntimeError(f"unexpected top-level ZIP entry: {name}")
    return p


def secret_scan(name: str, data: bytes) -> None:
    for pattern in SECRET_PATTERNS:
        for m in pattern.finditer(data):
            line_start = data.rfind(b"\n", 0, m.start()) + 1
            line_end = data.find(b"\n", m.end())
            if line_end < 0:
                line_end = len(data)
            line = data[line_start:line_end].lower()
            if name.startswith("reference-v1.0.0/tests/") or name.startswith("reference-v1.0.0/fixtures/"):
                if any(mark in line for mark in SYNTHETIC_MARKERS):
                    continue
            raise RuntimeError(f"secret-shaped material in archival ZIP: {name}")


def verify_zip(path: Path) -> dict:
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("source ZIP contains duplicate member names")
        expected_order = sorted(names, key=lambda name: PurePosixPath(name).parts)
        if names != expected_order:
            raise RuntimeError("source ZIP entries are not in deterministic path-component order")
        for info in zf.infolist():
            p = safe_member(info.filename)
            if info.date_time != FIXED_ZIP_DT:
                raise RuntimeError(f"non-deterministic ZIP timestamp: {info.filename}")
            full_mode = (info.external_attr >> 16) & 0o177777
            if full_mode != EXPECTED_FILE_MODE:
                raise RuntimeError(f"unexpected ZIP file mode {oct(full_mode)}: {info.filename}")
            parts_folded = {part.casefold() for part in p.parts}
            if parts_folded & FORBIDDEN_PARTS:
                raise RuntimeError(f"forbidden private/runtime path in source ZIP: {info.filename}")
            if p.name.casefold().endswith(FORBIDDEN_SUFFIXES):
                raise RuntimeError(f"forbidden private/runtime artifact in source ZIP: {info.filename}")
            secret_scan(info.filename, zf.read(info))
        manifest = json.loads(zf.read("ARCHIVE-MANIFEST.json"))
        if manifest["reference"]["tag"] != REFERENCE_TAG or manifest["reference"]["commit_sha"] != REFERENCE_COMMIT or manifest["reference"]["git_tree_sha"] != REFERENCE_TREE:
            raise RuntimeError("archive manifest reference binding drift")
        if manifest["formalization"]["integration_commit_sha"] != FORMAL_COMMIT:
            raise RuntimeError("archive manifest formalization binding drift")
        by_name = {row["path"]: row for row in manifest["files"]}
        expected = [n for n in names if n != "ARCHIVE-MANIFEST.json"]
        if set(by_name) != set(expected):
            raise RuntimeError("archive manifest file inventory mismatch")
        for name in expected:
            data = zf.read(name)
            row = by_name[name]
            if row["bytes"] != len(data) or row["sha256"] != hashlib.sha256(data).hexdigest():
                raise RuntimeError(f"archive manifest hash mismatch: {name}")
    return manifest


def verify_pdf(path: Path) -> None:
    reader = PdfReader(str(path))
    if len(reader.pages) < 5:
        raise RuntimeError("Overview PDF is unexpectedly short")
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    required = ["AI-CONTEXT v1.0.0", DOI, REFERENCE_COMMIT, "Lean 4 formalization", "Threat model and limits", "Citation"]
    for needle in required:
        if needle not in text:
            raise RuntimeError(f"Overview PDF missing required text: {needle}")


def verify_notes(path: Path, source_hash: str, pdf_hash: str) -> None:
    text = path.read_text(encoding="utf-8")
    for needle in (DOI, REFERENCE_COMMIT, FORMAL_COMMIT, source_hash, pdf_hash, "circular self-hash dependency"):
        if needle not in text:
            raise RuntimeError(f"RELEASE-NOTES.md missing archival field: {needle}")


def compare_reference_against_tag(zip_path: Path) -> None:
    import tarfile
    with tempfile.TemporaryDirectory(prefix="ai-context-phase13-verify-") as td:
        td = Path(td)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(td)
        expected = td / "expected"
        expected.mkdir()
        tar = td / "tag.tar"
        with tar.open("wb") as fh:
            subprocess.run(["git", "archive", "--format=tar", REFERENCE_TAG], cwd=ROOT, check=True, stdout=fh)
        with tarfile.open(tar, "r:") as tf:
            tf.extractall(expected, filter="data")
        actual = td / "reference-v1.0.0"
        exp_files = {p.relative_to(expected).as_posix(): p.read_bytes() for p in expected.rglob("*") if p.is_file()}
        act_files = {p.relative_to(actual).as_posix(): p.read_bytes() for p in actual.rglob("*") if p.is_file()}
        if exp_files != act_files:
            raise RuntimeError("reference-v1.0.0 is not byte-for-byte derived from git archive v1.0.0")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact_dir", type=Path)
    args = ap.parse_args()
    d = args.artifact_dir.resolve()
    expected = {SOURCE_ZIP, OVERVIEW_PDF, RELEASE_NOTES}
    actual = {p.name for p in d.iterdir() if p.is_file()}
    if actual != expected:
        raise RuntimeError(f"artifact directory must contain exactly {sorted(expected)}, found {sorted(actual)}")
    z = d / SOURCE_ZIP
    p = d / OVERVIEW_PDF
    n = d / RELEASE_NOTES
    verify_zip(z)
    compare_reference_against_tag(z)
    verify_pdf(p)
    verify_notes(n, sha256(z), sha256(p))
    print(json.dumps({"status":"ok","artifacts":{SOURCE_ZIP:sha256(z),OVERVIEW_PDF:sha256(p),RELEASE_NOTES:sha256(n)}}, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
