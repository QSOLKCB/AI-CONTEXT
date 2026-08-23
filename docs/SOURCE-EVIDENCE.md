# Phase 4 Source Evidence Model

Phase 4 turns repositories, documents, Google Drive exports, and email archives into a provenance-rich evidence graph before Phase 5 curation begins.

The core boundary remains:

```text
SOURCE EVIDENCE != CANONICAL MEMORY
SOURCE AUTHORITY != CLAIM TRUTH
DUPLICATE CONTENT != DUPLICATE PROVENANCE
TAGGED RELEASE IDENTITY != VERIFIED PUBLICATION
```

## Source snapshots

Every Phase 4 import creates a deterministic `AI-CONTEXT/SOURCE-SNAPSHOT` object in:

```text
receipts/source-snapshots.jsonl
```

The snapshot ID binds:

- source SHA-256;
- adapter id/version/layout;
- source authority class/rank;
- Git commit/tree/branch/dirty state when available;
- exact tags at `HEAD` when available;
- commit/tag/release identity records.

Operational timestamps are deliberately excluded from the source-snapshot identity. Re-importing the same exact source state yields the same snapshot bytes and ID.

Every Phase 4 evidence observation must reference the same `source_snapshot_id` carried by its import receipt. A valid snapshot elsewhere in the workspace is not interchangeable provenance.

## Repository identity

For a Git worktree the reference adapter attempts to record:

```text
HEAD commit SHA
HEAD tree SHA
branch (when attached)
dirty/clean state
tags pointing exactly at HEAD
```

Commit, tag, and release identity records require a concrete commit SHA. If Git identity or worktree status cannot be established, the source is not treated as a clean Git snapshot.

A semantic-version tag such as `v1.2.3` produces a release identity record with:

```json
{
  "kind": "release",
  "tag": "v1.2.3",
  "commit_sha": "<exact commit sha>",
  "publication_state": "unverified",
  "release_basis": "exact_semver_git_tag"
}
```

This does **not** claim that a GitHub Release, DOI, package-registry release, or other publication exists. That requires separate evidence.

### Clean Git evidence

`git_clean_head` evidence is restricted to files tracked by Git. Ignored files do not inherit clean-commit authority merely because they happen to exist inside the checkout.

If worktree status or tracked-file enumeration cannot be established, the adapter fails closed by downgrading to `source_tree_snapshot` authority and marking the parse partial.

## Authority and precedence

Authority rank is a deterministic source-state precedence hint inside the `source_evidence` domain. It is not a probability and it is not scientific truth authority.

| Class | Rank | Meaning |
|---|---:|---|
| `git_clean_head` | 95 | exact clean Git worktree bound to `HEAD` |
| `git_dirty_worktree` | 85 | Git worktree with local uncommitted state |
| `source_tree_snapshot` | 70 | source-tree snapshot or Git source whose clean state cannot be established |
| `email_archive_message` | 65 | RFC 5322/MIME message/archive evidence |
| `drive_export_snapshot` | 60 | Google Drive/Takeout export snapshot |
| `document_snapshot` | 50 | standalone/local document snapshot |

The class/rank mapping is fixed by the protocol schema. A conforming producer cannot assign an arbitrary rank to an authority class.

A higher rank may be preferred when two sources disagree about the same source-state question. It must never be used to turn an unsupported claim into a fact.

Examples:

```text
clean repository README > emailed copy of README
```

for determining current repository text, but:

```text
README assertion != verified external fact
```

still applies.

## Line-addressed chunks

Textual files are split deterministically into chunks with source metadata such as:

```json
{
  "source_path": "docs/design.md",
  "source_range": {
    "kind": "line",
    "start": 81,
    "end": 160
  }
}
```

Plain UTF-8 text/Markdown/code uses physical line ranges. OOXML and HTML extraction uses `logical_line` ranges because the extracted text does not correspond one-to-one with XML/HTML source lines.

The default reference chunk size is 80 lines and can be changed with `--chunk-lines`.

## OOXML boundary

DOCX, PPTX, and XLSX are ZIP-based formats. The reference extractor therefore applies a second archive-safety boundary inside the outer source archive, including member-count, per-member expanded-byte, total expanded-byte, and path-safety checks before XML is read.

Presentation extraction follows the slide relationship order declared by `presentation.xml`, not lexicographic ZIP member names. Spreadsheet extraction preserves sparse cell coordinates, including empty columns between populated cells.

## Content-addressed duplicate collapse

Phase 4 separates *content* from *where it came from* and also separates textual representation from binary-reference representation.

```text
staging/content.jsonl
    one content-addressed object per unique representation + payload

staging/observations.jsonl
    one provenance observation per source/range/message

staging/content-index.json
    derived content_id -> observation_ids graph
```

Content IDs are representation-aware:

```text
content.text.sha256:<sha256 of normalized UTF-8 text>
content.binary.sha256:<sha256 of original binary bytes>
```

If the same text exists in a repository, Drive export, and email body, the content registry can contain one text content object while the provenance graph retains three observations.

```text
repo observation  ----\
Drive observation -----+--> content.text.sha256:...
email observation ----/
```

A binary file with byte-identical bytes receives `content.binary.sha256:...` instead, preventing a text object and a binary reference from colliding under one identifier.

No provenance edge is discarded merely because content is duplicated.

## Drive export boundary

A directory or ZIP is only considered a recognized Takeout/Drive layout when a `Drive/` or `Google Drive/` root is detected. Explicit `drive-export` imports without that structure may still be staged conservatively, but they are marked `partial` rather than claimed as an exact provider parse.

## Email boundary

Email source paths are archive-relative and form part of provenance identity. Two identical messages in `inbox/mail.mbox` and `archive/mail.mbox` remain two provenance observations even when their Message-ID and body are identical.

Fallback identifiers for messages without `Message-ID` are derived from the stable archive-relative path plus message index. Temporary extraction filenames never enter canonical observation identity.

MIME body extraction does not descend into attachment containers, including attached `message/rfc822` messages. Forwarded or attached email content is therefore not silently flattened into the enclosing message body. Skipped/bodyless messages retain warnings so mixed archives are marked `partial`.

## Binary and PDF boundary

Binary files can be represented as hash-only `binary_ref` content objects. Raw binary bytes are not copied into the content registry.

PDF text extraction remains an external-extractor boundary. The reference implementation records the PDF content hash/length and emits a warning rather than pretending that raw PDF bytes are readable text.

## Validation

Run both validators after Phase 4 imports:

```bash
python3 tools/ai_context.py validate ~/my-ai-context
python3 tools/validate_evidence.py ~/my-ai-context
```

The evidence validator verifies snapshot IDs, fixed authority ranks, repository identity commit bindings, representation-aware content hashes, content references, receipt-to-snapshot provenance edges, receipt cardinality, duplicate groups, and deterministic content-index equality.
