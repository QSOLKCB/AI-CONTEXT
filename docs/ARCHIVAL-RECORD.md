# Phase 13 scholarly report and Zenodo archival record

Phase 13 packages the frozen AI-CONTEXT v1.0.0 implementation and the post-tag Lean 4 formalization into a small, reproducible scholarly surface.

## Zenodo identity

- DOI: `10.5281/zenodo.22081189`
- resource type: Software
- version: `1.0.0`
- license: Apache-2.0
- creator: Trent Slade (QSOL-IMC; ORCID `0009-0002-4515-9237`)
- contributor: OpenAI ChatGPT (GPT-5.6 Sol), OpenAI

## Exactly three upload files

The generated Zenodo upload directory contains exactly:

```text
AI-CONTEXT-1.0.0-source.zip
AI-CONTEXT-v1.0.0-Overview.pdf
RELEASE-NOTES.md
```

No generated archive or report is committed to the public repository. CI builds the artifacts into a temporary directory and exposes them through a GitHub Actions artifact.

## Compound source archive

`AI-CONTEXT-1.0.0-source.zip` has this provenance-preserving shape:

```text
reference-v1.0.0/
  exact byte-for-byte export of immutable Git tag v1.0.0

formal/
  post-tag Lean 4 formalization of that immutable release
  pinned Lake/Lean build metadata
  theorem inventory and validator

ARCHIVE-MANIFEST.json
  reference tag/commit/tree identity
  formalization integration identity
  SHA-256 and byte length for every archived file
```

The archive never claims that the Lean sources existed inside the original v1.0.0 tag. The two source layers are deliberately distinct.

## Immutable identities

```text
v1.0.0 reference commit:
53d7d69dfacecf6f8605f5b6a51b2c68ee66572a

v1.0.0 Git tree:
2c0592cbd074d7596e70681cc5ed869d6b9b00e4

Phase 12 formalization integration commit:
b5cf9ae50400b30ec51489bf4d1b51d431f08252

Lean toolchain:
leanprover/lean4:v4.30.0
```

## Build

```bash
python -m pip install -r requirements-archive.txt
python tools/build_phase13_artifacts.py --out /tmp/ai-context-phase13
python tools/verify_phase13_artifacts.py /tmp/ai-context-phase13
```

The builder is deterministic for the frozen inputs. ZIP member timestamps and modes are fixed, entries are sorted, and ReportLab invariant mode removes runtime PDF timestamp/ID drift.

## Reproduce from the source ZIP

After extraction:

```bash
cd reference-v1.0.0
python -m unittest discover -s tests -v

cd ../formal
lake build
python tools/validate_formalization.py
```

The reference directory is compared byte-for-byte against `git archive v1.0.0` during package verification.

## Privacy and release-tree verification

The repository release audit continues to run before Phase 13 packaging. The Phase 13 verifier additionally rejects unsafe ZIP paths, symlinks, private/runtime path classes, key/private-store suffixes, and non-synthetic secret-shaped content in the compound source archive. The `ARCHIVE-MANIFEST.json` hashes every archived source file.

## Checksum rule

A file cannot embed its own ordinary final SHA-256 without changing the bytes being hashed. Therefore the non-circular three-file checksum contract is:

- `RELEASE-NOTES.md` contains the exact SHA-256 of the source ZIP;
- `RELEASE-NOTES.md` contains the exact SHA-256 of the Overview PDF;
- Zenodo records the checksum of `RELEASE-NOTES.md` itself after upload;
- `ARCHIVE-MANIFEST.json` inside the source ZIP hashes every internal archived file.

This avoids a fourth checksum upload without making an impossible self-hash claim.

## Publication boundary

The repository can build and verify the complete three-file upload set. Publication of the Zenodo record is an external human action. The final roadmap items remain open until the record is actually published and the DOI/citation is added back to the GitHub release and README without modifying the frozen v1.0.0 tag.
