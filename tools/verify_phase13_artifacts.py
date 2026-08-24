#!/usr/bin/env python3
"""Verify the three-file AI-CONTEXT v1.0.0 Zenodo archival surface."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ZIP = "AI-CONTEXT-1.0.0-source.zip"
OVERVIEW_PDF = "AI-CONTEXT-v1.0.0-Overview.pdf"
RELEASE_NOTES = "RELEASE-NOTES.md"
EXPECTED_ARTIFACTS = {SOURCE_ZIP, OVERVIEW_PDF, RELEASE_NOTES}
REFERENCE_TAG = "v1.0.0"
REFERENCE_COMMIT = "53d7d69dfacecf6f8605f5b6a51b2c68ee66572a"
REFERENCE_TREE = "2c0592cbd074d7596e70681cc5ed869d6b9b00e4"
FORMAL_COMMIT = "b5cf9ae50400b30ec51489bf4d1b51d431f08252"
DOI = "10.5281/zenodo.22081189"
LEAN_TOOLCHAIN = "leanprover/lean4:v4.30.0"
FORMAL_PATHS = [
    "formal",
    "lean-toolchain",
    "lakefile.lean",
    "tools/validate_formalization.py",
    "tests/test_phase12_formalization.py",
    "docs/FORMALIZATION.md",
    "release/v1-freeze.json",
]
FIXED_ZIP_DT = (1980, 1, 1, 0, 0, 0)
EXPECTED_FILE_MODE = 0o100644
MAX_ZIP_MEMBERS = 4096
MAX_ZIP_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 250.0
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


def run_git(*args: str) -> str:
    cp = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return cp.stdout.strip()


def assert_frozen_git_bindings() -> None:
    tag_commit = run_git("rev-parse", f"{REFERENCE_TAG}^{{commit}}")
    if tag_commit != REFERENCE_COMMIT:
        raise RuntimeError(f"{REFERENCE_TAG} resolves to {tag_commit}, expected {REFERENCE_COMMIT}")
    reference_tree = run_git("rev-parse", f"{REFERENCE_COMMIT}^{{tree}}")
    if reference_tree != REFERENCE_TREE:
        raise RuntimeError(f"reference tree is {reference_tree}, expected {REFERENCE_TREE}")
    formal_commit = run_git("rev-parse", f"{FORMAL_COMMIT}^{{commit}}")
    if formal_commit != FORMAL_COMMIT:
        raise RuntimeError("Phase 12 formalization commit is unavailable or ambiguous")


def safe_member(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise RuntimeError(f"unsafe ZIP path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RuntimeError(f"unsafe ZIP path: {name}")
    if path.as_posix() != name:
        raise RuntimeError(f"non-canonical ZIP path: {name}")
    if path.parts[0] not in ALLOWED_TOP:
        raise RuntimeError(f"unexpected top-level ZIP entry: {name}")
    return path


def secret_scan(name: str, data: bytes) -> None:
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(data):
            line_start = data.rfind(b"\n", 0, match.start()) + 1
            line_end = data.find(b"\n", match.end())
            if line_end < 0:
                line_end = len(data)
            line = data[line_start:line_end].lower()
            if name.startswith("reference-v1.0.0/tests/") or name.startswith("reference-v1.0.0/fixtures/"):
                if any(marker in line for marker in SYNTHETIC_MARKERS):
                    continue
            raise RuntimeError(f"secret-shaped material in archival ZIP: {name}")


def expected_manifest_identity() -> dict[str, object]:
    return {
        "protocol": "AI-CONTEXT/ARCHIVE-MANIFEST",
        "schema_version": "1.0.0",
        "software": "AI-CONTEXT",
        "version": "1.0.0",
        "doi": DOI,
        "license": "Apache-2.0",
        "reference": {
            "tag": REFERENCE_TAG,
            "commit_sha": REFERENCE_COMMIT,
            "git_tree_sha": REFERENCE_TREE,
            "directory": "reference-v1.0.0",
        },
        "formalization": {
            "relationship": "post-tag selected-invariant formalization of the immutable reference release",
            "integration_commit_sha": FORMAL_COMMIT,
            "lean_toolchain": LEAN_TOOLCHAIN,
            "directory": "formal",
            "authority_rule": "Lean proofs are selected invariant proofs; they do not replace the frozen protocol or prove every implementation detail.",
        },
    }


def validate_manifest_identity(manifest: object) -> dict[str, object]:
    if not isinstance(manifest, dict):
        raise RuntimeError("archive manifest must be a JSON object")
    expected = expected_manifest_identity()
    expected_keys = set(expected) | {"files"}
    if set(manifest) != expected_keys:
        raise RuntimeError("archive manifest top-level fields drifted")
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise RuntimeError(f"archive manifest identity field drift: {key}")
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise RuntimeError("archive manifest files must be a list")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise RuntimeError("malformed archive manifest file row")
        path = row.get("path")
        size = row.get("bytes")
        digest = row.get("sha256")
        if not isinstance(path, str) or not path or PurePosixPath(path).as_posix() != path or path in seen:
            raise RuntimeError("archive manifest contains duplicate or non-canonical file path")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_ZIP_MEMBER_BYTES:
            raise RuntimeError(f"archive manifest has invalid byte length: {path}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError(f"archive manifest has invalid SHA-256: {path}")
        seen.add(path)
    return manifest


def _validated_zip_files(path: Path) -> tuple[list[str], dict[str, bytes]]:
    with zipfile.ZipFile(path, "r") as zf:
        infos = zf.infolist()
        if not infos or len(infos) > MAX_ZIP_MEMBERS:
            raise RuntimeError("source ZIP member count is outside allowed bounds")

        names: list[str] = []
        normalized_seen: set[str] = set()
        total = 0
        for info in infos:
            if info.is_dir():
                raise RuntimeError(f"source ZIP contains directory entry: {info.filename}")
            normalized = safe_member(info.filename).as_posix()
            if normalized in normalized_seen:
                raise RuntimeError(f"source ZIP contains duplicate normalized member: {normalized}")
            normalized_seen.add(normalized)
            names.append(normalized)

            if info.flag_bits & 0x1:
                raise RuntimeError(f"encrypted ZIP member is not allowed: {normalized}")
            if info.compress_type != zipfile.ZIP_DEFLATED:
                raise RuntimeError(f"unexpected ZIP compression method: {normalized}")
            if info.date_time != FIXED_ZIP_DT:
                raise RuntimeError(f"non-deterministic ZIP timestamp: {normalized}")
            full_mode = (info.external_attr >> 16) & 0o177777
            if full_mode != EXPECTED_FILE_MODE:
                raise RuntimeError(f"unexpected ZIP file mode {oct(full_mode)}: {normalized}")
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                raise RuntimeError(f"ZIP member exceeds expanded-size limit: {normalized}")
            total += info.file_size
            if total > MAX_ZIP_TOTAL_BYTES:
                raise RuntimeError("source ZIP exceeds total expanded-size limit")
            if info.file_size:
                if info.compress_size <= 0:
                    raise RuntimeError(f"invalid compressed size for ZIP member: {normalized}")
                ratio = info.file_size / info.compress_size
                if ratio > MAX_ZIP_COMPRESSION_RATIO:
                    raise RuntimeError(f"ZIP member compression ratio exceeds limit: {normalized}")

            p = PurePosixPath(normalized)
            parts_folded = {part.casefold() for part in p.parts}
            if parts_folded & FORBIDDEN_PARTS:
                raise RuntimeError(f"forbidden private/runtime path in source ZIP: {normalized}")
            if p.name.casefold().endswith(FORBIDDEN_SUFFIXES):
                raise RuntimeError(f"forbidden private/runtime artifact in source ZIP: {normalized}")

        expected_order = sorted(names, key=lambda name: PurePosixPath(name).parts)
        if names != expected_order:
            raise RuntimeError("source ZIP entries are not in deterministic path-component order")

        files: dict[str, bytes] = {}
        for info, name in zip(infos, names):
            data = zf.read(info)
            if len(data) != info.file_size:
                raise RuntimeError(f"ZIP expanded-size mismatch: {name}")
            secret_scan(name, data)
            files[name] = data
        return names, files


def verify_zip(path: Path) -> tuple[dict[str, object], dict[str, bytes]]:
    names, files = _validated_zip_files(path)
    manifest_bytes = files.get("ARCHIVE-MANIFEST.json")
    if manifest_bytes is None:
        raise RuntimeError("source ZIP is missing ARCHIVE-MANIFEST.json")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("ARCHIVE-MANIFEST.json is not valid UTF-8 JSON") from exc
    validate_manifest_identity(manifest)

    rows = manifest["files"]
    by_name = {row["path"]: row for row in rows}
    expected = [name for name in names if name != "ARCHIVE-MANIFEST.json"]
    if set(by_name) != set(expected) or len(by_name) != len(expected):
        raise RuntimeError("archive manifest file inventory mismatch")
    for name in expected:
        data = files[name]
        row = by_name[name]
        if row["bytes"] != len(data) or row["sha256"] != hashlib.sha256(data).hexdigest():
            raise RuntimeError(f"archive manifest hash mismatch: {name}")
    return manifest, files


def _safe_tar_path(name: str) -> PurePosixPath:
    if "\\" in name:
        raise RuntimeError(f"unsafe Git archive path: {name}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RuntimeError(f"unsafe Git archive path: {name}")
    if path.as_posix() != name.rstrip("/"):
        raise RuntimeError(f"non-canonical Git archive path: {name}")
    return path


def git_archive_files(ref: str, paths: list[str] | None = None) -> dict[str, bytes]:
    with tempfile.NamedTemporaryFile(prefix="ai-context-expected-", suffix=".tar", delete=False) as tmp:
        tar_path = Path(tmp.name)
        cmd = ["git", "archive", "--format=tar", ref]
        if paths:
            cmd.extend(["--", *paths])
        subprocess.run(cmd, cwd=ROOT, check=True, stdout=tmp)
    try:
        result: dict[str, bytes] = {}
        total = 0
        with tarfile.open(tar_path, "r:") as tf:
            members = tf.getmembers()
            if len(members) > MAX_ZIP_MEMBERS:
                raise RuntimeError("expected Git archive exceeds member-count limit")
            for member in members:
                name = _safe_tar_path(member.name).as_posix()
                if member.isdir():
                    continue
                if not member.isfile():
                    raise RuntimeError(f"expected Git archive contains non-regular member: {name}")
                if member.size > MAX_ZIP_MEMBER_BYTES:
                    raise RuntimeError(f"expected Git archive member exceeds size limit: {name}")
                total += member.size
                if total > MAX_ZIP_TOTAL_BYTES:
                    raise RuntimeError("expected Git archive exceeds total size limit")
                source = tf.extractfile(member)
                if source is None:
                    raise RuntimeError(f"could not read expected Git archive member: {name}")
                data = source.read(MAX_ZIP_MEMBER_BYTES + 1)
                if len(data) != member.size:
                    raise RuntimeError(f"expected Git archive member size mismatch: {name}")
                result[name] = data
        return result
    finally:
        tar_path.unlink(missing_ok=True)


def compare_reference_against_commit(files: dict[str, bytes]) -> None:
    prefix = "reference-v1.0.0/"
    actual = {name[len(prefix):]: data for name, data in files.items() if name.startswith(prefix)}
    expected = git_archive_files(REFERENCE_COMMIT)
    if actual != expected:
        raise RuntimeError("reference-v1.0.0 is not byte-for-byte derived from the frozen reference commit")


def compare_formal_against_commit(files: dict[str, bytes]) -> None:
    prefix = "formal/"
    actual = {name[len(prefix):]: data for name, data in files.items() if name.startswith(prefix)}
    expected = git_archive_files(FORMAL_COMMIT, FORMAL_PATHS)
    if actual != expected:
        raise RuntimeError("formal/ is not byte-for-byte derived from the bound Phase 12 integration commit")


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


def verify_artifact_directory(path: Path) -> dict[str, Path]:
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("artifact path must be a real directory")
    entries = list(path.iterdir())
    names = {entry.name for entry in entries}
    if names != EXPECTED_ARTIFACTS or len(entries) != len(EXPECTED_ARTIFACTS):
        raise RuntimeError(f"artifact directory must contain exactly {sorted(EXPECTED_ARTIFACTS)}, found {sorted(names)}")
    result: dict[str, Path] = {}
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise RuntimeError(f"artifact entry must be a regular non-symlink file: {entry.name}")
        result[entry.name] = entry
    return result


def extract_verified_source(zip_path: Path, dest: Path) -> None:
    """Safely materialize a source ZIP only after all provenance checks pass."""
    assert_frozen_git_bindings()
    _, files = verify_zip(zip_path)
    compare_reference_against_commit(files)
    compare_formal_against_commit(files)
    if dest.exists() or dest.is_symlink():
        raise RuntimeError("verified extraction destination must not already exist")
    dest.mkdir(parents=True)
    for name, data in files.items():
        rel = safe_member(name)
        target = dest.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact_dir", type=Path)
    args = ap.parse_args()
    artifact_dir = args.artifact_dir.expanduser().absolute()
    artifacts = verify_artifact_directory(artifact_dir)
    assert_frozen_git_bindings()

    source = artifacts[SOURCE_ZIP]
    pdf = artifacts[OVERVIEW_PDF]
    notes = artifacts[RELEASE_NOTES]
    _, files = verify_zip(source)
    compare_reference_against_commit(files)
    compare_formal_against_commit(files)
    verify_pdf(pdf)
    verify_notes(notes, sha256(source), sha256(pdf))
    print(json.dumps({
        "status": "ok",
        "artifacts": {
            SOURCE_ZIP: sha256(source),
            OVERVIEW_PDF: sha256(pdf),
            RELEASE_NOTES: sha256(notes),
        },
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
