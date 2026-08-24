#!/usr/bin/env python3
"""Build deterministic Phase 13 Zenodo artifacts for AI-CONTEXT v1.0.0."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, Flowable,
)

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


def run(*args: str, cwd: Path | None = None, capture: bool = True) -> str:
    cp = subprocess.run(args, cwd=cwd or ROOT, check=True, text=True,
                        stdout=subprocess.PIPE if capture else None,
                        stderr=subprocess.PIPE if capture else None)
    return cp.stdout.strip() if capture else ""


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
    cp = subprocess.run(["git", "diff", "--quiet", FORMAL_COMMIT, "HEAD", "--", *FORMAL_PATHS], cwd=ROOT)
    if cp.returncode != 0:
        raise RuntimeError("formalization source drifted from the reviewed Phase 12 integration commit")


def export_reference_tree(dest: Path) -> None:
    # Keep this helper self-contained: the staging root does not exist yet on a clean build.
    dest.parent.mkdir(parents=True, exist_ok=True)
    tar_path = dest.parent / "reference.tar"
    with tar_path.open("wb") as fh:
        subprocess.run(["git", "archive", "--format=tar", REFERENCE_TAG], cwd=ROOT, check=True, stdout=fh)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:") as tf:
        for member in tf.getmembers():
            if member.issym() or member.islnk():
                raise RuntimeError(f"tag archive unexpectedly contains link: {member.name}")
            target = (dest / member.name).resolve()
            if dest.resolve() not in target.parents and target != dest.resolve():
                raise RuntimeError(f"unsafe archive member: {member.name}")
        tf.extractall(dest)
    tar_path.unlink()


def export_formal_layer(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for rel in FORMAL_PATHS:
        src = ROOT / rel
        out = dest / rel
        if src.is_dir():
            shutil.copytree(src, out)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)


def file_manifest(base: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        rel = path.relative_to(base).as_posix()
        rows.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def write_archive_manifest(stage: Path) -> None:
    payload = {
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
    (stage / "ARCHIVE-MANIFEST.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def deterministic_zip(stage: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(p for p in stage.rglob("*") if p.is_file()):
            rel = path.relative_to(stage).as_posix()
            info = zipfile.ZipInfo(rel, FIXED_ZIP_DT)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 & 0xFFFF) << 16
            info.create_system = 3
            zf.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


class Rule(Flowable):
    def __init__(self, width: float, thickness: float = 0.6):
        super().__init__(); self.width = width; self.thickness = thickness; self.height = 4
    def draw(self):
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 2, self.width, 2)


class ArchitectureFlow(Flowable):
    labels = ["RAW VAULT", "IMPORT + RECEIPTS", "STAGING", "CURATION", "CANONICAL MEMORY", "ROUTING", "SELECTIVE BUNDLE"]
    def __init__(self, width: float):
        super().__init__(); self.width = width; self.height = 188
    def draw(self):
        c = self.canv
        box_w = min(260, self.width * 0.66); box_h = 20; gap = 6
        x = (self.width - box_w) / 2
        y = self.height - box_h
        centers = []
        for label in self.labels:
            c.roundRect(x, y, box_w, box_h, 4, stroke=1, fill=0)
            c.setFont("Helvetica-Bold", 7.2)
            c.drawCentredString(x + box_w / 2, y + 7.2, label)
            centers.append((x + box_w/2, y))
            y -= box_h + gap
        for i in range(len(centers)-1):
            cx, by = centers[i]
            nx, ny = centers[i+1]
            c.line(cx, by, nx, ny + box_h)
        c.setFont("Helvetica-Oblique", 7)
        c.drawString(0, 1, "Authority increases only through explicit protocol gates; relevance, storage, restore, indexes and transport do not create memory authority.")


class EvidenceLayers(Flowable):
    def __init__(self, width: float):
        super().__init__(); self.width = width; self.height = 90
    def draw(self):
        c = self.canv
        items = [
            ("1", "Python executable conformance", "Reference behavior and adversarial tests"),
            ("2", "JSON schemas", "Structural wire and record contracts"),
            ("3", "Adversarial fixtures", "Finite failure cases and fail-closed behavior"),
            ("4", "Lean 4", "Selected structural invariants of frozen v1.0.0"),
        ]
        y = self.height - 18
        for n, title, sub in items:
            c.circle(10, y+2, 8, stroke=1, fill=0)
            c.setFont("Helvetica-Bold", 8); c.drawCentredString(10, y-1, n)
            c.setFont("Helvetica-Bold", 8); c.drawString(24, y+2, title)
            c.setFont("Helvetica", 7); c.drawString(24, y-8, sub)
            y -= 22


def report_styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="TitleX", parent=s["Title"], fontName="Helvetica-Bold", fontSize=23, leading=27, alignment=TA_LEFT, spaceAfter=8))
    s.add(ParagraphStyle(name="SubTitle", parent=s["Normal"], fontName="Helvetica", fontSize=10.5, leading=14, textColor=colors.HexColor("#333333"), spaceAfter=16))
    s.add(ParagraphStyle(name="H1X", parent=s["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=20, spaceBefore=8, spaceAfter=8))
    s.add(ParagraphStyle(name="H2X", parent=s["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=7, spaceAfter=5))
    s.add(ParagraphStyle(name="BodyX", parent=s["BodyText"], fontName="Helvetica", fontSize=9.2, leading=13.2, spaceAfter=7))
    s.add(ParagraphStyle(name="Small", parent=s["BodyText"], fontName="Helvetica", fontSize=7.6, leading=10.2, spaceAfter=5))
    s.add(ParagraphStyle(name="CodeX", parent=s["Code"], fontName="Courier", fontSize=7.6, leading=10, leftIndent=8, rightIndent=8, backColor=colors.HexColor("#f3f3f3"), borderPadding=5, spaceAfter=7))
    s.add(ParagraphStyle(name="Callout", parent=s["BodyText"], fontName="Helvetica-Bold", fontSize=9, leading=13, leftIndent=10, rightIndent=10, borderWidth=.5, borderPadding=7, borderColor=colors.HexColor("#777777"), spaceBefore=4, spaceAfter=9))
    return s


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.drawString(18*mm, 11*mm, "AI-CONTEXT v1.0.0 - Scholarly Overview")
    canvas.drawRightString(A4[0]-18*mm, 11*mm, f"Page {doc.page}")
    canvas.restoreState()


def build_overview_pdf(output: Path, theorem_entries: list[dict[str, object]]) -> None:
    rl_config.invariant = 1
    styles = report_styles()
    doc = BaseDocTemplate(str(output), pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=17*mm, bottomMargin=18*mm,
                          title="AI-CONTEXT v1.0.0: A Vendor-Neutral Framework for Private, Portable, Governed AI Context Memory",
                          author="Trent Slade; contributor: OpenAI ChatGPT (GPT-5.6 Sol)")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates(PageTemplate(id="main", frames=frame, onPage=_footer))
    W = doc.width
    story = [
        Paragraph("AI-CONTEXT v1.0.0", styles["TitleX"]),
        Paragraph("A Vendor-Neutral Framework for Private, Portable, Governed AI Context Memory", styles["SubTitle"]),
        Rule(W), Spacer(1, 8),
        Paragraph(f"<b>Zenodo DOI:</b> {DOI}<br/><b>Creator:</b> Trent Slade (QSOL-IMC; ORCID 0009-0002-4515-9237)<br/><b>Contributor:</b> OpenAI ChatGPT (GPT-5.6 Sol), OpenAI<br/><b>License:</b> Apache-2.0", styles["BodyX"]),
        Paragraph("Reference identity", styles["H2X"]),
        Paragraph(f"Tag: <b>{REFERENCE_TAG}</b><br/>Reference commit: <font name='Courier'>{REFERENCE_COMMIT}</font><br/>Git tree: <font name='Courier'>{REFERENCE_TREE}</font><br/>Post-tag Lean integration commit: <font name='Courier'>{FORMAL_COMMIT}</font>", styles["Small"]),
        Spacer(1, 6),
        Paragraph("Abstract", styles["H1X"]),
        Paragraph("AI-CONTEXT is a vendor-neutral specification and reference implementation for transforming user-controlled sources into governed canonical AI context memory. It separates raw evidence, provenance, curation authority, canonical memory, selective disclosure, encrypted persistence, portable restore, derived retrieval, interoperability and operator UX. Version 1.0.0 freezes the reference protocol and canonicalization contract, then a post-tag Lean 4 layer formalizes selected structural invariants of that immutable release. The project is designed around a simple principle: useful context and protocol authority are different things.", styles["BodyX"]),
        Paragraph("Scope statement", styles["Callout"]),
        Paragraph("The Lean layer proves selected authority, disclosure, lifecycle, restore, retrieval and transport invariants. It does not prove every Python/Rust implementation detail, cryptographic primitives, semantic truth, provider behavior, or universal privacy/erasure.", styles["BodyX"]),
        PageBreak(),
        Paragraph("1. Motivation and design problem", styles["H1X"]),
        Paragraph("Provider-side memory is convenient but typically opaque, non-portable and entangled with one service boundary. AI-CONTEXT instead treats memory as a user-governed local data system. Source material may be useful while still being wrong; a task may be relevant to context that a selected provider is not permitted to receive; encryption may protect bytes without making those bytes true; restore may reconstruct context without recreating an AI instance. The architecture therefore keeps evidence, authority and disclosure separate.", styles["BodyX"]),
        Paragraph("Core distinctions", styles["H2X"]),
        Paragraph("SOURCE MATERIAL != CANONICAL MEMORY\nCANDIDATE != MEMORY\nLLM SUGGESTION != REVIEW DECISION\nRELEVANT != PERMITTED\nDEPENDENCY != PERMISSION BYPASS\nROUTED BUNDLE != CANONICAL MEMORY\nENCRYPTION AT REST != MEMORY AUTHORITY\nRESTORE != MODEL IDENTITY\nINDEX HIT != MEMORY AUTHORITY\nSIGNATURE != DISCLOSURE AUTHORITY\nTUI ACTION != AUTHORITY", styles["CodeX"]),
        Paragraph("2. Reference architecture", styles["H1X"]),
        ArchitectureFlow(W), Spacer(1, 7),
        Paragraph("Raw sources enter a vault and become staged observations plus receipts. Only explicit curation can apply approved candidates into canonical memory. Routing then constructs a target-specific bundle from already-authorized records. Optional encrypted persistence and restore operate below or beside these authority boundaries. Derived indexes accelerate retrieval but must return to canonical validation and routing before disclosure.", styles["BodyX"]),
        PageBreak(),
        Paragraph("3. Evidence and provenance", styles["H1X"]),
        Paragraph("The framework supports AI export formats, local repositories, text/JSON, office documents, Drive/Takeout material and email archives. Ingested data becomes observations, source snapshots, content identities and receipts. Duplicate content may collapse computationally without destroying provenance. Source authority and claim truth remain separate.", styles["BodyX"]),
        Paragraph("4. Curation and lifecycle", styles["H1X"]),
        Paragraph("Candidate generation cannot self-promote. Automated semantic extraction and local LLM curation are advisory. Canonical application requires an explicit human or policy approval decision. The curation layer records conflicts, supersession, verification/confidence changes, retention, expiry and tombstones. Content correction is represented as a new record plus supersession rather than silent history rewrite.", styles["BodyX"]),
        Paragraph("5. Selective disclosure", styles["H1X"]),
        Paragraph("Routing is a read-only disclosure firewall over canonical memory. Profiles, task/tag selectors, sensitivity ceilings, target policies, hard exclusions and dependency closure determine the smallest permitted bundle. A dependency can bypass relevance selection only; it cannot bypass sensitivity, lifecycle, approval or target permission. Ambiguity fails closed.", styles["BodyX"]),
        Paragraph("6. Persistence and restore", styles["H1X"]),
        Paragraph("The optional encrypted-directory backend uses AES-256-GCM through the maintained Python cryptography package, with external key custody and resumable rotation. Storage encryption confers no epistemic or disclosure authority. Portable .aicr archives preserve either a minimum continuity set or a fuller working set. Restore means governed context reconstruction, not recreation of model identity, hidden chain of thought or provider-private memory.", styles["BodyX"]),
        PageBreak(),
        Paragraph("7. Derived retrieval and interoperability", styles["H1X"]),
        Paragraph("Deterministic vector, graph and lexical projections are private rebuildable retrieval accelerators. A source fingerprint makes stale projections unusable. Search hits remain candidates and must return through routing before disclosure. The interoperability layer adds a stabilized Python surface, an independent Rust verifier, language-neutral fixtures, Ed25519 integrity receipts, capability manifests and read-only tool/MCP examples. Signature validity proves byte integrity under a key, not disclosure permission, epistemic truth or identity trust by itself.", styles["BodyX"]),
        Paragraph("8. Operator UX", styles["H1X"]),
        Paragraph("The dependency-free terminal UX exposes imports, review, conflict inspection, provenance, exact bundle previews, backup and restore while delegating authority-sensitive work to the established protocol paths. Read-only inspection may not bootstrap or repair governance state. Approval and canonical application remain separate events. A preview is not disclosure and a menu action is not authority.", styles["BodyX"]),
        Paragraph("9. Reproducibility evidence model", styles["H1X"]),
        EvidenceLayers(W),
        Paragraph("The four layers deliberately overlap without being conflated. Python and JSON schemas provide executable and structural conformance. Adversarial tests exercise finite failure modes. Lean proves selected invariants in a small reference model. None of these layers is allowed to silently upgrade the claims of the others.", styles["BodyX"]),
        PageBreak(),
        Paragraph("10. Frozen v1.0.0 identity", styles["H1X"]),
        Paragraph(f"The immutable v1.0.0 implementation target is commit <font name='Courier'>{REFERENCE_COMMIT}</font> with Git tree <font name='Courier'>{REFERENCE_TREE}</font>. The active canonicalizer is <font name='Courier'>python-json-v0.1</font>. RFC 8785 JCS was evaluated but not adopted because a silent canonicalizer change would alter existing deterministic identifiers and hashes. The exact merged main release commit passed the complete release gate before the v1.0.0 tag was created.", styles["BodyX"]),
        Paragraph("11. Lean 4 formalization", styles["H1X"]),
        Paragraph(f"Phase 12 was created after the tag and integrated at commit <font name='Courier'>{FORMAL_COMMIT}</font>. It is pinned to <font name='Courier'>{LEAN_TOOLCHAIN}</font>, uses Lean core plus Lake without Mathlib, and treats v1.0.0 as an immutable theorem subject. The formalization strengthened source-to-memory separation by modeling import transitions, and secret exclusion by modeling secret-shaped content independently of declared sensitivity labels.", styles["BodyX"]),
        Paragraph("Formal theorem inventory", styles["H2X"]),
    ]
    inv_rows = [["#", "Lean declaration", "Frozen invariant"]]
    for i, e in enumerate(theorem_entries[:12], 1):
        inv_rows.append([str(i), str(e["declaration"]), str(e["invariant"])])
    table = Table(inv_rows, colWidths=[9*mm, 62*mm, W-71*mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("FONTNAME", (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 6.8), ("LEADING", (0,0), (-1,-1), 8.5),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#eaeaea")),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#aaaaaa")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 3), ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    story += [
        table,
        Paragraph(f"Representative entries shown above. The machine-readable inventory contains {len(theorem_entries)} named theorems in total.", styles["Small"]),
        Spacer(1, 9),
        Paragraph("12. Threat model and limits", styles["H1X"]),
        Paragraph("AI-CONTEXT is not a password manager. Credentials, private keys, recovery codes, storage keys and similar secrets are excluded from canonical memory. The release audit is a bounded claim about the exact tracked public tree, not a proof that prior Git history, developer machines, backups or every possible semantic private fact are clean. Deletion receipts are deliberately conservative and do not claim destruction of every backup or key copy. Embedded signing keys do not become identity trust anchors.", styles["BodyX"]),
        Paragraph("13. Archival provenance", styles["H1X"]),
        Paragraph("The Zenodo source archive is a compound scholarly source bundle. reference-v1.0.0/ is derived exactly from the immutable v1.0.0 tag. formal/ contains the later Lean formalization of that tag, together with its pinned build metadata and theorem inventory. ARCHIVE-MANIFEST.json records the identities and SHA-256 digest of every archived file. This structure explicitly avoids claiming that the Lean sources existed in the original tag.", styles["BodyX"]),
        Paragraph("14. Reproduction", styles["H1X"]),
        Paragraph("Reference implementation tests: <font name='Courier'>python -m unittest discover -s tests -v</font><br/>Formal layer: <font name='Courier'>lake build</font><br/>Inventory validation: <font name='Courier'>python tools/validate_formalization.py</font><br/>Phase 13 package validation: <font name='Courier'>python tools/verify_phase13_artifacts.py &lt;artifact-dir&gt;</font>", styles["BodyX"]),
        Paragraph("15. Citation", styles["H1X"]),
        Paragraph(f"Slade, T. (2026). <i>AI-CONTEXT v1.0.0: A Vendor-Neutral Framework for Private, Portable, Governed AI Context Memory</i> [Software]. Zenodo. https://doi.org/{DOI}", styles["Callout"]),
        Paragraph("Contribution note", styles["H2X"]),
        Paragraph("Creator metadata identifies Trent Slade (QSOL-IMC; ORCID 0009-0002-4515-9237). The Zenodo contributor record identifies OpenAI ChatGPT (GPT-5.6 Sol), affiliated with OpenAI, for AI-assisted architecture, implementation, review and documentation work as recorded by the project.", styles["Small"]),
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

From a clean checkout containing the tag and the Phase 12 formalization:

```bash
python tools/build_phase13_artifacts.py --out /tmp/ai-context-phase13
python tools/verify_phase13_artifacts.py /tmp/ai-context-phase13
```

The archival source ZIP is designed for two independent reproductions:

```bash
# frozen v1.0.0 reference tree
cd reference-v1.0.0
python -m unittest discover -s tests -v

# post-tag formalization of that frozen tree
cd ../formal
lake build
python tools/validate_formalization.py
```

## Provenance rule

`reference-v1.0.0/` is derived from the immutable Git tag. `formal/` is a later scholarly layer bound to that tag. Their co-location in the archival ZIP is not a claim that the Lean files existed in the original release tag.
"""
    output.write_text(existing + appendix + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    out = args.out.resolve()
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True)
    assert_git_bindings()
    inventory = json.loads((ROOT / "formal" / "theorem-inventory.json").read_text(encoding="utf-8"))
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
    print(json.dumps({"status":"ok","doi":DOI,"artifacts":{SOURCE_ZIP:source_hash,OVERVIEW_PDF:pdf_hash,RELEASE_NOTES:sha256_file(out / RELEASE_NOTES)}}, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
