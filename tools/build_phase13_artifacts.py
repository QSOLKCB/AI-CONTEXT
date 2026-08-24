#!/usr/bin/env python3
"""Build deterministic Phase 13 Zenodo artifacts for AI-CONTEXT v1.0.0."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_TAG = "v1.0.0"
REFERENCE_COMMIT = "53d7d69dfacecf6f8605f5b6a51b2c68ee66572a"
REFERENCE_TREE = "2c0592cbd074d7596e70681cc5ed869d6b9b00e4"
FORMAL_COMMIT = "b5cf9ae50400b30ec51489bf4d1b51d431f08252"
DOI = "10.5281/zenodo.22081189"
LEAN_TOOLCHAIN = "leanprover/lean4:v4.30.0"
SOURCE_ZIP = "AI-CONTEXT-1.0.0-source.zip"
OVERVIEW_PDF = "AI-CONTEXT-v1.0.0-Overview.pdf"
RELEASE_NOTES = "RELEASE-NOTES.md"
EXPECTED_ARTIFACTS = {SOURCE_ZIP, OVERVIEW_PDF, RELEASE_NOTES}
FIXED_ZIP_DT = (1980, 1, 1, 0, 0, 0)
FORMAL_PATHS = [
    "formal",
    "lean-toolchain",
    "lakefile.lean",
    "tools/validate_formalization.py",
    "tests/test_phase12_formalization.py",
    "docs/FORMALIZATION.md",
    "release/v1-freeze.json",
]
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_FILE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 256 * 1024 * 1024


def run(*args: str, cwd: Path | None = None) -> str:
    cp = subprocess.run(
        args,
        cwd=cwd or ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return cp.stdout.strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_git_bindings() -> None:
    tag_commit = run("git", "rev-parse", f"{REFERENCE_TAG}^{{commit}}")
    if tag_commit != REFERENCE_COMMIT:
        raise RuntimeError(f"{REFERENCE_TAG} resolves to {tag_commit}, expected {REFERENCE_COMMIT}")
    tree = run("git", "rev-parse", f"{REFERENCE_COMMIT}^{{tree}}")
    if tree != REFERENCE_TREE:
        raise RuntimeError(f"reference tree is {tree}, expected {REFERENCE_TREE}")
    formal_commit = run("git", "rev-parse", f"{FORMAL_COMMIT}^{{commit}}")
    if formal_commit != FORMAL_COMMIT:
        raise RuntimeError("Phase 12 formalization commit is unavailable or ambiguous")


def _safe_tar_path(name: str) -> PurePosixPath:
    if "\\" in name:
        raise RuntimeError(f"unsafe Git archive path: {name}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RuntimeError(f"unsafe Git archive path: {name}")
    if path.as_posix() != name.rstrip("/"):
        raise RuntimeError(f"non-canonical Git archive path: {name}")
    return path


def export_git_archive(ref: str, dest: Path, paths: list[str] | None = None) -> None:
    """Export regular-file bytes directly from a bound Git object, never the worktree."""
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="ai-context-git-archive-", suffix=".tar", delete=False) as tmp:
        tar_path = Path(tmp.name)
        cmd = ["git", "archive", "--format=tar", ref]
        if paths:
            cmd.extend(["--", *paths])
        subprocess.run(cmd, cwd=ROOT, check=True, stdout=tmp)
    try:
        with tarfile.open(tar_path, "r:") as tf:
            members = tf.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise RuntimeError("Git archive exceeds member-count limit")
            total = 0
            for member in members:
                _safe_tar_path(member.name)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise RuntimeError(f"Git archive contains non-regular member: {member.name}")
                if member.size > MAX_ARCHIVE_FILE_BYTES:
                    raise RuntimeError(f"Git archive member exceeds size limit: {member.name}")
                total += member.size
                if total > MAX_ARCHIVE_TOTAL_BYTES:
                    raise RuntimeError("Git archive exceeds total expanded-size limit")
            for member in members:
                if member.isdir():
                    continue
                rel = _safe_tar_path(member.name)
                target = dest.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tf.extractfile(member)
                if source is None:
                    raise RuntimeError(f"could not read Git archive member: {member.name}")
                data = source.read(MAX_ARCHIVE_FILE_BYTES + 1)
                if len(data) != member.size:
                    raise RuntimeError(f"Git archive member size mismatch: {member.name}")
                target.write_bytes(data)
    finally:
        tar_path.unlink(missing_ok=True)


def export_reference_tree(dest: Path) -> None:
    # The tag is verified above, but the bytes are exported by immutable commit SHA.
    export_git_archive(REFERENCE_COMMIT, dest)


def export_formal_layer(dest: Path) -> None:
    # Never copy mutable working-tree state into the scholarly archive.
    export_git_archive(FORMAL_COMMIT, dest, FORMAL_PATHS)


def file_manifest(base: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    files = [p for p in base.rglob("*") if p.is_file() and not p.is_symlink()]
    for path in sorted(files, key=lambda p: PurePosixPath(p.relative_to(base).as_posix()).parts):
        rel = path.relative_to(base).as_posix()
        rows.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def archive_manifest_payload(stage: Path) -> dict[str, object]:
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
        "files": file_manifest(stage),
    }


def write_archive_manifest(stage: Path) -> None:
    payload = archive_manifest_payload(stage)
    (stage / "ARCHIVE-MANIFEST.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def deterministic_zip(stage: Path, output: Path) -> None:
    files = [p for p in stage.rglob("*") if p.is_file() and not p.is_symlink()]
    files.sort(key=lambda p: PurePosixPath(p.relative_to(stage).as_posix()).parts)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(stage).as_posix()
            info = zipfile.ZipInfo(rel, FIXED_ZIP_DT)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 & 0xFFFF) << 16
            info.create_system = 3
            zf.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def report_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleX", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=22, leading=26, alignment=TA_LEFT, spaceAfter=8))
    styles.add(ParagraphStyle(name="SubTitle", parent=styles["Normal"], fontName="Helvetica", fontSize=10.5, leading=14, textColor=colors.HexColor("#333333"), spaceAfter=14))
    styles.add(ParagraphStyle(name="H1X", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=20, spaceBefore=6, spaceAfter=7))
    styles.add(ParagraphStyle(name="H2X", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=6, spaceAfter=4))
    styles.add(ParagraphStyle(name="BodyX", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.2, leading=13.2, spaceAfter=7))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.7, leading=10.3, spaceAfter=5))
    styles.add(ParagraphStyle(name="CodeX", parent=styles["Code"], fontName="Courier", fontSize=7.5, leading=9.8, leftIndent=8, rightIndent=8, backColor=colors.HexColor("#f3f3f3"), borderPadding=5, spaceAfter=7))
    styles.add(ParagraphStyle(name="Callout", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=9, leading=13, leftIndent=10, rightIndent=10, borderWidth=.5, borderPadding=7, borderColor=colors.HexColor("#777777"), spaceBefore=4, spaceAfter=9))
    return styles


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.drawString(18 * mm, 11 * mm, "AI-CONTEXT v1.0.0 - Scholarly Overview")
    canvas.drawRightString(A4[0] - 18 * mm, 11 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_overview_pdf(output: Path, theorem_entries: list[dict[str, object]]) -> None:
    """Build a deterministic six-page human-facing technical overview."""
    rl_config.invariant = 1
    styles = report_styles()
    doc = BaseDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=18 * mm,
        title="AI-CONTEXT v1.0.0: A Vendor-Neutral Framework for Private, Portable, Governed AI Context Memory",
        author="Trent Slade; contributor: OpenAI ChatGPT (GPT-5.6 Sol)",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates(PageTemplate(id="main", frames=frame, onPage=_footer))

    def body(text: str):
        return Paragraph(text, styles["BodyX"])

    def h1(text: str):
        return Paragraph(text, styles["H1X"])

    story = [
        Paragraph("AI-CONTEXT v1.0.0", styles["TitleX"]),
        Paragraph("A Vendor-Neutral Framework for Private, Portable, Governed AI Context Memory", styles["SubTitle"]),
        body(f"<b>Zenodo DOI:</b> {DOI}<br/><b>Creator:</b> Trent Slade (QSOL-IMC; ORCID 0009-0002-4515-9237)<br/><b>Contributor:</b> OpenAI ChatGPT (GPT-5.6 Sol), OpenAI<br/><b>License:</b> Apache-2.0"),
        h1("Abstract"),
        body("AI-CONTEXT is a vendor-neutral specification and reference implementation for transforming user-controlled sources into governed canonical AI context memory. It separates evidence, provenance, curation authority, canonical memory, selective disclosure, persistence, restore, derived retrieval, interoperability and operator UX. Version 1.0.0 freezes the protocol and canonicalization contract; a post-tag Lean 4 layer then formalizes selected structural invariants of that immutable release."),
        Paragraph("Lean proves selected protocol invariants. It does not prove every Python/Rust implementation detail, cryptographic primitive, semantic truth claim, provider behavior, or universal privacy/erasure.", styles["Callout"]),
        body(f"<b>Frozen tag:</b> {REFERENCE_TAG}<br/><b>Reference commit:</b> <font name='Courier'>{REFERENCE_COMMIT}</font><br/><b>Git tree:</b> <font name='Courier'>{REFERENCE_TREE}</font><br/><b>Phase 12 integration:</b> <font name='Courier'>{FORMAL_COMMIT}</font>"),
        PageBreak(),
        h1("1. Motivation and architecture"),
        body("Provider-side memory is useful but can be opaque, non-portable and tied to one service. AI-CONTEXT treats memory as a user-governed local data system. Source material can be relevant and still be false; relevant context can still be forbidden for a target; encryption can protect bytes without making them true; restore can reconstruct context without recreating a model instance."),
        Paragraph("PRIVATE SOURCES\n  -> RAW VAULT\n  -> IMPORT + RECEIPTS\n  -> STAGING\n  -> CURATION\n  -> CANONICAL MEMORY\n  -> ROUTING\n  -> SELECTIVE BUNDLE\n  -> AI / AGENT / LOCAL MODEL / EXTERNAL PROVIDER", styles["CodeX"]),
        h1("2. Evidence, provenance and curation"),
        body("AI exports, repositories, documents, Drive/Takeout material and email archives become observations, source snapshots, content identities and receipts. Imported source is evidence, not canonical memory. Automated extraction and local LLM curation may propose candidates but cannot self-promote. Human or policy approval and canonical application remain separate authority events. Conflicts, supersession, verification, retention, expiry and tombstones remain explicit and receipted."),
        h1("3. Selective disclosure"),
        body("Routing is a read-only disclosure firewall over already-approved canonical memory. Profiles, task/tag selection, sensitivity ceilings, hard exclusions, target policy and dependency closure produce the smallest permitted bundle. Relevance and dependency never bypass permission; ambiguity fails closed."),
        PageBreak(),
        h1("4. Storage and restore"),
        body("The optional encrypted-directory backend uses AES-256-GCM through the maintained Python cryptography package with external key custody and resumable rotation. Encryption at rest is a persistence property and grants no epistemic or disclosure authority. Portable .aicr archives preserve a minimum continuity set or fuller working set. Restore reconstructs governed context, not model identity, hidden reasoning, weights or provider-private memory."),
        h1("5. Derived indexes"),
        body("Deterministic vector, graph and lexical projections are private rebuildable retrieval accelerators. Source fingerprints make stale projections unusable. Index membership and search hits remain candidate retrieval only and must return through canonical validation and routing before disclosure."),
        h1("6. Interoperability and signed receipts"),
        body("A stabilized Python surface, independent Rust verifier, language-neutral fixtures, Ed25519 integrity receipts, capability manifests and read-only MCP/tool examples provide transport interoperability without creating a second memory authority. Signature validity proves byte integrity under a key, not disclosure permission, epistemic truth or identity trust by itself."),
        h1("7. Operator UX"),
        body("The dependency-free terminal UX exposes imports, review, conflicts, provenance, exact bundle previews, backup and restore while delegating authority-sensitive behavior to the established protocol. Read-only inspection does not mutate governance state. Approval and application stay separate; preview is not disclosure; a menu action is not authority."),
        PageBreak(),
        h1("8. Conformance and formal verification"),
        body("Four evidence layers overlap without being conflated: Python executable conformance, JSON schemas, adversarial fixtures/tests and Lean 4 selected-invariant proofs. The full pre-tag release gate passed on the exact frozen main commit before v1.0.0 was created."),
        body(f"Phase 12 is pinned to <font name='Courier'>{LEAN_TOOLCHAIN}</font>, uses Lean core plus Lake without Mathlib, and contains {len(theorem_entries)} named theorems with checked finite invalid-state examples and no sorry, admit or axiom placeholders."),
        Paragraph("Representative formal invariants", styles["H2X"]),
        Paragraph("<br/>".join(f"{i}. <font name='Courier'>{e['declaration']}</font>: {e['invariant']}" for i, e in enumerate(theorem_entries[:12], 1)), styles["Small"]),
        h1("9. Formalization scope"),
        body("The formal model covers source-to-memory authority separation, secret-shaped-content exclusion independent of sensitivity labels, curation/application boundaries, non-downgrade, disclosure permission, restore semantics, storage non-authority, stale indexes, signature/tool/capability limits, UX orchestration and migration rejection. It does not re-prove AES-GCM, Ed25519, SHA-256, Git, GitHub Actions, Python, Rust or provider implementations."),
        PageBreak(),
        h1("10. Threat model and limits"),
        body("AI-CONTEXT is not a password manager. Credentials, private keys, recovery material and storage keys are excluded from canonical memory. The release audit is a bounded exact-Git-tree claim, not proof that developer machines, prior history, backups or every possible semantic private fact are clean. Deletion receipts are conservative and do not claim destruction of all copies or key material. Embedded verification keys do not become identity trust anchors."),
        h1("11. Archival provenance"),
        body("The Zenodo source archive is deliberately compound. reference-v1.0.0/ is exported directly from the immutable v1.0.0 commit after verifying the tag binding. formal/ is exported directly from the reviewed Phase 12 integration commit. ARCHIVE-MANIFEST.json binds those identities and hashes every archived file. The co-location of both layers does not imply the Lean sources existed in the original release tag."),
        h1("12. Canonicalization"),
        body("v1.0.0 retains python-json-v0.1. RFC 8785 JCS was evaluated but deliberately not adopted because a silent canonicalizer change would alter established identifiers and hashes. A future canonicalizer change requires an explicit versioned migration and new language-neutral conformance vectors."),
        PageBreak(),
        h1("13. Reproducibility"),
        body("The Phase 13 builder and verifier produce and independently validate exactly three Zenodo upload files. The verifier checks archive resource ceilings, canonical paths, duplicate members, deterministic metadata, secret/private-runtime exclusions, manifest identities, file hashes, byte-for-byte reference provenance and byte-for-byte formalization provenance."),
        Paragraph("python tools/build_phase13_artifacts.py --out /tmp/ai-context-phase13\npython tools/verify_phase13_artifacts.py /tmp/ai-context-phase13\npython -m unittest discover -s tests -v\nlake build\npython tools/validate_formalization.py", styles["CodeX"]),
        h1("14. Citation"),
        Paragraph(f"Slade, T. (2026). <i>AI-CONTEXT v1.0.0: A Vendor-Neutral Framework for Private, Portable, Governed AI Context Memory</i> [Software]. Zenodo. https://doi.org/{DOI}", styles["Callout"]),
        h1("15. Contribution note"),
        body("Creator metadata identifies Trent Slade (QSOL-IMC; ORCID 0009-0002-4515-9237). The Zenodo contributor record identifies OpenAI ChatGPT (GPT-5.6 Sol), affiliated with OpenAI, for AI-assisted architecture, implementation, review and documentation work as recorded by the project."),
    ]
    doc.build(story)


def build_release_notes(output: Path, source_hash: str, pdf_hash: str, theorem_count: int) -> None:
    existing = (ROOT / "RELEASE-NOTES.md").read_text(encoding="utf-8").rstrip()
    appendix = f"""

# Phase 12/13 archival addendum

## Archival identity

- Zenodo DOI: `{DOI}`
- Software version: `1.0.0`
- License: `Apache-2.0`
- Frozen reference tag: `{REFERENCE_TAG}`
- Frozen reference commit: `{REFERENCE_COMMIT}`
- Frozen Git tree: `{REFERENCE_TREE}`
- Post-tag Lean formalization integration commit: `{FORMAL_COMMIT}`
- Lean toolchain: `{LEAN_TOOLCHAIN}`
- Named Lean theorems in archival inventory: `{theorem_count}`

## Zenodo upload files

Exactly three files are intended for the version record:

1. `{SOURCE_ZIP}`
2. `{OVERVIEW_PDF}`
3. `{RELEASE_NOTES}`

### SHA-256

- `{SOURCE_ZIP}`: `{source_hash}`
- `{OVERVIEW_PDF}`: `{pdf_hash}`
- `{RELEASE_NOTES}`: Zenodo records the checksum of this file independently after upload. The file does not embed its own final SHA-256 because doing so would create a circular self-hash dependency.

No fourth checksum file is required. The source ZIP additionally contains `ARCHIVE-MANIFEST.json`, which records SHA-256 and byte length for every archived reference/formalization file.

## Formalization scope

The Lean 4 layer is post-tag and targets exactly the immutable `v1.0.0` implementation. It proves selected structural protocol invariants concerning authority separation, curation, sensitivity, selective disclosure, restore semantics, storage non-authority, derived-index freshness, signed-receipt/tool/capability authority limits, UX orchestration and migration behavior.

It does **not** claim to prove every Python/Rust implementation detail, AES-GCM, Ed25519, SHA-256, Git/GitHub Actions, semantic truth, provider behavior, universal privacy/erasure, or model-identity reconstruction.

## Reproduction

From a clean checkout containing the tag and Phase 12 commit:

```bash
python tools/build_phase13_artifacts.py --out /tmp/ai-context-phase13
python tools/verify_phase13_artifacts.py /tmp/ai-context-phase13
```

The builder exports both reference and formalization bytes from their bound Git commits rather than mutable working-tree state. The source archive can then be extracted and its two components reproduced independently.

## Provenance rule

`reference-v1.0.0/` is exported from `{REFERENCE_COMMIT}` after verifying the `v1.0.0` tag binding. `formal/` is exported from `{FORMAL_COMMIT}`. Their co-location in the archival ZIP is not a claim that the Lean files existed in the original release tag.
"""
    output.write_text(existing + appendix + "\n", encoding="utf-8")


def _has_symlink_component(path: Path) -> bool:
    candidate = path.absolute()
    parts = candidate.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink():
                return True
        else:
            break
    return False


def prepare_output_dir(raw_out: Path, replace: bool) -> Path:
    out = raw_out.expanduser().absolute()
    if _has_symlink_component(out):
        raise RuntimeError("output path may not contain symlink components")
    resolved = out.resolve(strict=False)
    root = ROOT.resolve()
    unsafe = {Path("/").resolve(), Path.home().resolve(), root}
    if resolved in unsafe or resolved in root.parents:
        raise RuntimeError(f"unsafe archival output path: {out}")
    if out.exists():
        if not out.is_dir() or out.is_symlink():
            raise RuntimeError("existing output path must be a real directory")
        if not replace:
            raise RuntimeError("output directory already exists; pass --yes to replace prior archival artifacts")
        entries = list(out.iterdir())
        for entry in entries:
            if entry.name not in EXPECTED_ARTIFACTS or entry.is_symlink() or not entry.is_file():
                raise RuntimeError("refusing to replace a non-dedicated output directory")
        for entry in entries:
            entry.unlink()
    else:
        out.mkdir(parents=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--yes", action="store_true", help="replace an existing dedicated three-file artifact directory")
    args = ap.parse_args()
    out = prepare_output_dir(args.out, args.yes)
    assert_git_bindings()

    # Read theorem metadata from the reviewed Phase 12 commit, not the mutable worktree.
    inventory_text = run("git", "show", f"{FORMAL_COMMIT}:formal/theorem-inventory.json")
    inventory = json.loads(inventory_text)
    theorem_entries = inventory["theorems"]

    pdf = out / OVERVIEW_PDF
    build_overview_pdf(pdf, theorem_entries)
    with tempfile.TemporaryDirectory(prefix="ai-context-phase13-") as td:
        stage = Path(td) / "AI-CONTEXT-1.0.0-source"
        export_reference_tree(stage / "reference-v1.0.0")
        export_formal_layer(stage / "formal")
        write_archive_manifest(stage)
        deterministic_zip(stage, out / SOURCE_ZIP)

    source_hash = sha256_file(out / SOURCE_ZIP)
    pdf_hash = sha256_file(pdf)
    build_release_notes(out / RELEASE_NOTES, source_hash, pdf_hash, len(theorem_entries))
    print(json.dumps({
        "status": "ok",
        "doi": DOI,
        "artifacts": {
            SOURCE_ZIP: source_hash,
            OVERVIEW_PDF: pdf_hash,
            RELEASE_NOTES: sha256_file(out / RELEASE_NOTES),
        },
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
