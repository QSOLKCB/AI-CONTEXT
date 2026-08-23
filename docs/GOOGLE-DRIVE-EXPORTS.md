# Google Drive / Takeout Evidence Adapter

AI-CONTEXT Phase 4 can ingest an extracted Google Drive export directory or a Google Takeout ZIP as private source evidence.

Google officially supports exporting Drive data through Google Takeout. The exported data can include files/folders, the current version of files, versions marked to keep forever, titles, shared-drive information, and file metadata. The export is an archive surface, not a stable AI-CONTEXT schema.

Official references:

- https://support.google.com/drive/answer/9759608
- https://support.google.com/accounts/answer/3024190

## Usage

Extracted export:

```bash
python3 tools/evidence_import.py \
  ~/my-ai-context \
  ~/Downloads/Takeout \
  --adapter drive-export
```

Takeout ZIP:

```bash
python3 tools/evidence_import.py \
  ~/my-ai-context \
  ~/Downloads/takeout.zip \
  --adapter drive-export
```

## Supported evidence

The reference adapter supports:

- UTF-8 text, Markdown, code, JSON/JSONL, CSV/TSV, HTML/XML and similar text files;
- exported `.docx`, `.pptx`, and `.xlsx` OOXML using dependency-free deterministic text extraction;
- PDFs and other binary files as hash-only references when selected by the document pipeline;
- both `Takeout/Drive/...` layouts and narrower exported Drive directories.

OOXML text is recorded with `logical_line` ranges because extracted paragraphs/slides/rows are not physical XML source lines.

## Limitations

Drive export metadata and product layouts can change. The adapter does not claim that a Takeout snapshot is live Drive state or that it contains changes made after the export was requested.

Google notes that recent changes may be absent from a Takeout archive. AI-CONTEXT therefore classifies this source as `drive_export_snapshot`, not live repository or live Drive authority.

Binary parsing is deliberately conservative. Unsupported binary types remain content-addressed references rather than being decoded by guesswork.
