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

## Repository identity

For a Git worktree the reference adapter attempts to record:

```text
HEAD commit SHA
HEAD tree SHA
branch (when attached)
dirty/clean state
tags pointing exactly at HEAD
```

A semantic-version tag such as `v1.2.3` produces a release identity record with:

```json
{
  "kind": "release",
  "tag": "v1.2.3",
  "publication_state": "unverified",
  "release_basis": "exact_semver_git_tag"
}
```

This does **not** claim that a GitHub Release, DOI, package-registry release, or other publication exists. That requires separate evidence.

## Authority and precedence

Authority rank is a deterministic source-state precedence hint inside the `source_evidence` domain. It is not a probability and it is not scientific truth authority.

| Class | Rank | Meaning |
|---|---:|---|
| `git_clean_head` | 95 | exact clean Git worktree bound to `HEAD` |
| `git_dirty_worktree` | 85 | Git worktree with local uncommitted state |
| `source_tree_snapshot` | 70 | non-Git local source-tree snapshot |
| `email_archive_message` | 65 | RFC 5322/MIME message/archive evidence |
| `drive_export_snapshot` | 60 | Google Drive/Takeout export snapshot |
| `document_snapshot` | 50 | standalone/local document snapshot |

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

## Content-addressed duplicate collapse

Phase 4 separates *content* from *where it came from*.

```text
staging/content.jsonl
    one content-addressed object per unique payload

staging/observations.jsonl
    one provenance observation per source/range/message

staging/content-index.json
    derived content_id -> observation_ids graph
```

If the same text exists in a repository, Drive export, and email body, the content registry can contain one content object while the provenance graph retains three observations.

```text
repo observation  ----\
Drive observation -----+--> content.sha256:...
email observation ----/
```

No provenance edge is discarded merely because content is duplicated.

## Binary and PDF boundary

Binary files can be represented as hash-only `binary_ref` content objects. Raw binary bytes are not copied into the content registry.

PDF text extraction remains an external-extractor boundary. The reference implementation records the PDF content hash/length and emits a warning rather than pretending that raw PDF bytes are readable text.

## Validation

Run both validators after Phase 4 imports:

```bash
python3 tools/ai_context.py validate ~/my-ai-context
python3 tools/validate_evidence.py ~/my-ai-context
```

The evidence validator verifies snapshot IDs, content hashes, content references, source-snapshot references, receipt cardinality, duplicate groups, and deterministic content-index equality.
