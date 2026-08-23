#!/usr/bin/env python3
"""Repository, document, Drive-export, and email evidence adapters.

Phase 4 keeps source evidence separate from canonical memory. Text is normalized into
content-addressed chunks; source-specific observations retain provenance and range data.
"""

from __future__ import annotations

import hashlib
import html
import mailbox
import mimetypes
import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from email import policy as email_policy
from email.message import Message
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from xml.etree import ElementTree as ET

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".rst", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".csv", ".tsv", ".py", ".rs", ".js", ".mjs",
    ".cjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".sh", ".bash",
    ".zsh", ".fish", ".sql", ".xml", ".go", ".java", ".kt", ".swift", ".c",
    ".h", ".cpp", ".hpp", ".wgsl", ".glsl",
}
SPECIAL_TEXT_NAMES = {"LICENSE", "README", "Makefile", "Dockerfile"}
OOXML_EXTENSIONS = {".docx", ".pptx", ".xlsx"}
SEMVER_TAG = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


class EvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceItem:
    source_local_id: str
    source_path: str
    media_type: str
    content_kind: str
    text: str | None = None
    raw_sha256: str | None = None
    byte_length: int = 0
    line_start: int | None = None
    line_end: int | None = None
    range_kind: str | None = None
    kind: str = "document_chunk"
    actor: str | None = None
    timestamp: str | None = None
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterResult:
    source_type: str
    adapter_id: str
    adapter_version: str
    layout_id: str
    parse_status: str
    authority_class: str
    authority_rank: int
    items: list[EvidenceItem]
    warnings: list[str]
    snapshot_metadata: dict[str, Any]


class _VisibleHTML(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in self.SKIP:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self.SKIP and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data.strip():
            self.parts.append(html.unescape(data.strip()))


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def chunk_text(
    text: str,
    *,
    source_path: str,
    media_type: str = "text/plain",
    max_lines: int = 80,
    range_kind: str = "line",
    metadata: dict[str, Any] | None = None,
) -> list[EvidenceItem]:
    text = normalize_text(text)
    lines = text.splitlines()
    if not lines and text:
        lines = [text]
    chunks: list[EvidenceItem] = []
    for start in range(0, len(lines), max_lines):
        block = lines[start:start + max_lines]
        if not any(line.strip() for line in block):
            continue
        body = "\n".join(block)
        line_start = start + 1
        line_end = start + len(block)
        chunks.append(EvidenceItem(
            source_local_id=f"{source_path}:L{line_start}-L{line_end}",
            source_path=source_path,
            media_type=media_type,
            content_kind="text",
            text=body,
            byte_length=len(body.encode("utf-8")),
            line_start=line_start,
            line_end=line_end,
            range_kind=range_kind,
            metadata=metadata or {},
        ))
    return chunks


def _xml_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _extract_docx(raw: bytes) -> str:
    import io
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        try:
            root = ET.fromstring(archive.read("word/document.xml"))
        except KeyError as exc:
            raise EvidenceError("DOCX missing word/document.xml") from exc
    lines: list[str] = []
    for node in root.iter():
        if _xml_local(node.tag) == "p":
            text = "".join(child.text or "" for child in node.iter() if _xml_local(child.tag) == "t").strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def _extract_pptx(raw: bytes) -> str:
    import io
    lines: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        slides = sorted(
            name for name in archive.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        )
        for index, name in enumerate(slides, 1):
            root = ET.fromstring(archive.read(name))
            text = " ".join(
                (node.text or "").strip()
                for node in root.iter()
                if _xml_local(node.tag) == "t" and (node.text or "").strip()
            )
            if text:
                lines.append(f"[Slide {index}] {text}")
    return "\n".join(lines)


def _extract_xlsx(raw: bytes) -> str:
    import io
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.iter():
                if _xml_local(si.tag) == "si":
                    shared.append("".join(
                        node.text or "" for node in si.iter() if _xml_local(node.tag) == "t"
                    ))
        sheets = sorted(
            name for name in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        )
        out: list[str] = []
        for sheet_index, name in enumerate(sheets, 1):
            root = ET.fromstring(archive.read(name))
            out.append(f"[Sheet {sheet_index}]")
            for row in root.iter():
                if _xml_local(row.tag) != "row":
                    continue
                values: list[str] = []
                for cell in row:
                    if _xml_local(cell.tag) != "c":
                        continue
                    cell_type = cell.attrib.get("t")
                    value_node = next((n for n in cell if _xml_local(n.tag) == "v"), None)
                    inline = next((n for n in cell.iter() if _xml_local(n.tag) == "t"), None)
                    value = ""
                    if inline is not None and inline.text is not None:
                        value = inline.text
                    elif value_node is not None and value_node.text is not None:
                        value = value_node.text
                        if cell_type == "s":
                            try:
                                value = shared[int(value)]
                            except (ValueError, IndexError):
                                pass
                    values.append(value)
                if any(value for value in values):
                    out.append("\t".join(values))
    return "\n".join(out)


def extract_document(raw: bytes, source_path: str) -> tuple[str | None, str, str, list[str]]:
    suffix = Path(source_path).suffix.casefold()
    media_type = mimetypes.guess_type(source_path)[0] or "application/octet-stream"
    warnings: list[str] = []
    if suffix in TEXT_EXTENSIONS or Path(source_path).name in SPECIAL_TEXT_NAMES:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None, media_type, "binary_ref", [f"non-UTF-8 text-like file treated as binary reference: {source_path}"]
        if suffix in {".html", ".htm"}:
            parser = _VisibleHTML()
            parser.feed(text)
            text = "\n".join(parser.parts)
            return text, "text/html", "text", warnings
        return text, media_type, "text", warnings
    try:
        if suffix == ".docx":
            return _extract_docx(raw), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text", warnings
        if suffix == ".pptx":
            return _extract_pptx(raw), "application/vnd.openxmlformats-officedocument.presentationml.presentation", "text", warnings
        if suffix == ".xlsx":
            return _extract_xlsx(raw), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "text", warnings
    except (zipfile.BadZipFile, ET.ParseError, EvidenceError, KeyError) as exc:
        warnings.append(f"OOXML extraction failed for {source_path}: {exc}; retained as binary reference")
        return None, media_type, "binary_ref", warnings
    if suffix == ".pdf":
        warnings.append(f"PDF text extraction intentionally external; retained hash-only reference: {source_path}")
    return None, media_type, "binary_ref", warnings


def items_for_bytes(
    raw: bytes,
    *,
    source_path: str,
    max_lines: int = 80,
    metadata: dict[str, Any] | None = None,
) -> tuple[list[EvidenceItem], list[str]]:
    text, media_type, kind, warnings = extract_document(raw, source_path)
    if kind == "text" and text is not None:
        range_kind = "logical_line" if Path(source_path).suffix.casefold() in OOXML_EXTENSIONS | {".html", ".htm"} else "line"
        return chunk_text(
            text,
            source_path=source_path,
            media_type=media_type,
            max_lines=max_lines,
            range_kind=range_kind,
            metadata=metadata,
        ), warnings
    digest = sha256_bytes(raw)
    return [EvidenceItem(
        source_local_id=source_path,
        source_path=source_path,
        media_type=media_type,
        content_kind="binary_ref",
        raw_sha256=digest,
        byte_length=len(raw),
        kind="document_reference",
        metadata=metadata or {},
    )], warnings


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_snapshot_metadata(root: Path) -> dict[str, Any]:
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        return {"git_available": False, "identity_records": []}
    head = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    branch = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=normal")
    tag_text = _git(root, "tag", "--points-at", "HEAD") or ""
    tags = sorted(tag for tag in tag_text.splitlines() if tag)
    dirty = bool(status) if status is not None else None
    identities: list[dict[str, Any]] = []
    if head:
        identities.append({
            "kind": "commit",
            "id": f"git.commit:{head}",
            "commit_sha": head,
            "tree_sha": tree,
        })
    for tag in tags:
        identities.append({
            "kind": "tag",
            "id": f"git.tag:{tag}@{head or 'unknown'}",
            "tag": tag,
            "commit_sha": head,
        })
        if SEMVER_TAG.fullmatch(tag):
            identities.append({
                "kind": "release",
                "id": f"release.git-tag:{tag}@{head or 'unknown'}",
                "tag": tag,
                "commit_sha": head,
                "publication_state": "unverified",
                "release_basis": "exact_semver_git_tag",
            })
    return {
        "git_available": True,
        "head_commit": head,
        "head_tree": tree,
        "branch": branch,
        "dirty": dirty,
        "tags_at_head": tags,
        "identity_records": identities,
    }


def _iter_source_files(root: Path) -> Iterable[Path]:
    for child in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if child.is_symlink() or not child.is_file():
            continue
        if ".git" in child.relative_to(root).parts:
            continue
        yield child


def parse_repository(root: Path, *, max_file_bytes: int, max_lines: int = 80) -> AdapterResult:
    if not root.is_dir():
        raise EvidenceError("repository adapter requires a directory")
    git = git_snapshot_metadata(root)
    authority_class = "git_dirty_worktree" if git.get("dirty") else "git_clean_head"
    authority_rank = 85 if git.get("dirty") else 95
    if not git.get("git_available"):
        authority_class = "source_tree_snapshot"
        authority_rank = 70
    items: list[EvidenceItem] = []
    warnings: list[str] = []
    for child in _iter_source_files(root):
        rel = child.relative_to(root).as_posix()
        size = child.stat().st_size
        if size > max_file_bytes:
            warnings.append(f"skipped oversized repository file: {rel}")
            continue
        if child.suffix.casefold() not in TEXT_EXTENSIONS | OOXML_EXTENSIONS and child.name not in SPECIAL_TEXT_NAMES:
            continue
        raw = child.read_bytes()
        file_items, extra = items_for_bytes(raw, source_path=rel, max_lines=max_lines)
        items.extend(file_items)
        warnings.extend(extra)
    if not items:
        warnings.append("repository snapshot produced no supported document evidence")
    return AdapterResult(
        source_type="git-repository" if git.get("git_available") else "source-tree",
        adapter_id="evidence-repository",
        adapter_version="0.1.0",
        layout_id="git-tree-evidence-v1" if git.get("git_available") else "source-tree-evidence-v1",
        parse_status="partial" if warnings else "exact",
        authority_class=authority_class,
        authority_rank=authority_rank,
        items=items,
        warnings=warnings,
        snapshot_metadata={"git": git},
    )


def _looks_drive_root(path: str) -> bool:
    lowered = path.casefold().replace("\\", "/")
    return "/drive/" in f"/{lowered}" or lowered.startswith("drive/") or "/google drive/" in f"/{lowered}"


def parse_drive_directory(root: Path, *, max_file_bytes: int, max_lines: int = 80) -> AdapterResult:
    if not root.is_dir():
        raise EvidenceError("Drive export adapter requires a directory")
    files = list(_iter_source_files(root))
    drive_files = [p for p in files if _looks_drive_root(p.relative_to(root).as_posix())]
    selected = drive_files or files
    items: list[EvidenceItem] = []
    warnings: list[str] = []
    for child in selected:
        rel = child.relative_to(root).as_posix()
        if child.name == "archive_browser.html":
            continue
        size = child.stat().st_size
        if size > max_file_bytes:
            warnings.append(f"skipped oversized Drive export file: {rel}")
            continue
        raw = child.read_bytes()
        file_items, extra = items_for_bytes(raw, source_path=rel, max_lines=max_lines, metadata={"drive_export": True})
        items.extend(file_items)
        warnings.extend(extra)
    return AdapterResult(
        source_type="google-drive-export",
        adapter_id="evidence-google-drive-export",
        adapter_version="0.1.0",
        layout_id="google-takeout-drive-tree-v1" if drive_files else "drive-export-tree-v1",
        parse_status="partial" if warnings else "exact",
        authority_class="drive_export_snapshot",
        authority_rank=60,
        items=items,
        warnings=warnings,
        snapshot_metadata={"official_export_surface": "Google Takeout/Drive", "drive_root_detected": bool(drive_files)},
    )


def _message_body(message: Message) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    plain_parts: list[str] = []
    html_parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            disposition = (part.get_content_disposition() or "").casefold()
            if disposition == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            try:
                value = part.get_content()
            except Exception as exc:  # email package decoders vary with malformed archives
                warnings.append(f"failed to decode MIME part: {exc}")
                continue
            if not isinstance(value, str):
                continue
            if content_type == "text/plain":
                plain_parts.append(value)
            else:
                html_parts.append(value)
    else:
        try:
            value = message.get_content()
        except Exception as exc:
            return "", "text/plain", [f"failed to decode message body: {exc}"]
        if isinstance(value, str):
            if message.get_content_type() == "text/html":
                html_parts.append(value)
            else:
                plain_parts.append(value)
    if plain_parts:
        return normalize_text("\n".join(plain_parts)).strip(), "text/plain", warnings
    if html_parts:
        parser = _VisibleHTML()
        parser.feed("\n".join(html_parts))
        warnings.append("email had no text/plain body; used visible HTML text fallback")
        return "\n".join(parser.parts).strip(), "text/html", warnings
    return "", "text/plain", warnings


def email_item(message: Message, *, source_path: str, index: int) -> tuple[EvidenceItem | None, list[str]]:
    body, body_type, warnings = _message_body(message)
    if not body:
        warnings.append(f"email message had no textual body: {source_path}:{index}")
        return None, warnings
    message_id = str(message.get("Message-ID") or f"{source_path}:{index}")
    labels = str(message.get("X-Gmail-Labels") or "")
    metadata = {
        "message_id": message_id,
        "subject": str(message.get("Subject") or ""),
        "from": str(message.get("From") or ""),
        "to": str(message.get("To") or ""),
        "cc": str(message.get("Cc") or ""),
        "gmail_labels": [label.strip() for label in labels.split(",") if label.strip()],
        "body_media_type": body_type,
    }
    return EvidenceItem(
        source_local_id=message_id,
        source_path=source_path,
        media_type=body_type,
        content_kind="text",
        text=body,
        byte_length=len(body.encode("utf-8")),
        kind="email_message",
        actor="email",
        timestamp=str(message.get("Date") or "") or None,
        title=str(message.get("Subject") or ""),
        metadata=metadata,
    ), warnings


def parse_email_file(path: Path) -> AdapterResult:
    suffix = path.suffix.casefold()
    items: list[EvidenceItem] = []
    warnings: list[str] = []
    if suffix == ".eml":
        message = BytesParser(policy=email_policy.default).parsebytes(path.read_bytes())
        item, extra = email_item(message, source_path=path.name, index=0)
        if item:
            items.append(item)
        warnings.extend(extra)
        layout = "rfc822-eml-v1"
    elif suffix in {".mbox", ".mbx"}:
        box = mailbox.mbox(path, factory=lambda f: BytesParser(policy=email_policy.default).parse(f))
        try:
            for index, message in enumerate(box):
                item, extra = email_item(message, source_path=path.name, index=index)
                if item:
                    items.append(item)
                warnings.extend(extra)
        finally:
            box.close()
        layout = "mbox-rfc822-v1"
    else:
        raise EvidenceError("email adapter supports .eml, .mbox, or .mbx files")
    if not items:
        raise EvidenceError("email archive produced no textual messages")
    return AdapterResult(
        source_type="email-archive",
        adapter_id="evidence-email-archive",
        adapter_version="0.1.0",
        layout_id=layout,
        parse_status="partial" if warnings else "exact",
        authority_class="email_archive_message",
        authority_rank=65,
        items=items,
        warnings=warnings,
        snapshot_metadata={"standards": ["RFC 5322/MIME"], "gmail_label_header": "X-Gmail-Labels"},
    )
