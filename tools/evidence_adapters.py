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
import posixpath
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
OOXML_MAX_MEMBERS = 4096
OOXML_MAX_MEMBER_BYTES = 32 * 1024 * 1024
OOXML_MAX_TOTAL_BYTES = 128 * 1024 * 1024


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


def _safe_ooxml_members(
    archive: zipfile.ZipFile,
    *,
    max_members: int = OOXML_MAX_MEMBERS,
    max_member_bytes: int = OOXML_MAX_MEMBER_BYTES,
    max_total_bytes: int = OOXML_MAX_TOTAL_BYTES,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > max_members:
        raise EvidenceError("OOXML member-count limit exceeded")
    total = 0
    safe: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        normalized = info.filename.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if pure.is_absolute() or ".." in pure.parts:
            raise EvidenceError(f"unsafe OOXML member path: {info.filename}")
        if info.is_dir():
            continue
        if info.file_size > max_member_bytes:
            raise EvidenceError(f"OOXML member too large: {info.filename}")
        total += info.file_size
        if total > max_total_bytes:
            raise EvidenceError("OOXML expanded-byte limit exceeded")
        safe[normalized] = info
    return safe


def _read_ooxml_member(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    name: str,
) -> bytes:
    info = members.get(name)
    if info is None:
        raise KeyError(name)
    return archive.read(info)


def _extract_docx(
    raw: bytes,
    *,
    max_member_bytes: int = OOXML_MAX_MEMBER_BYTES,
    max_total_bytes: int = OOXML_MAX_TOTAL_BYTES,
) -> str:
    import io

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = _safe_ooxml_members(
            archive,
            max_member_bytes=max_member_bytes,
            max_total_bytes=max_total_bytes,
        )
        try:
            root = ET.fromstring(_read_ooxml_member(archive, members, "word/document.xml"))
        except KeyError as exc:
            raise EvidenceError("DOCX missing word/document.xml") from exc
    lines: list[str] = []
    for node in root.iter():
        if _xml_local(node.tag) == "p":
            text = "".join(
                child.text or "" for child in node.iter() if _xml_local(child.tag) == "t"
            ).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def _pptx_slide_order(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
) -> list[str]:
    presentation_name = "ppt/presentation.xml"
    rels_name = "ppt/_rels/presentation.xml.rels"
    if presentation_name in members and rels_name in members:
        presentation = ET.fromstring(_read_ooxml_member(archive, members, presentation_name))
        rels = ET.fromstring(_read_ooxml_member(archive, members, rels_name))
        rel_targets = {
            rel.attrib.get("Id"): rel.attrib.get("Target")
            for rel in rels.iter()
            if _xml_local(rel.tag) == "Relationship"
        }
        ordered: list[str] = []
        for node in presentation.iter():
            if _xml_local(node.tag) != "sldId":
                continue
            rel_id = next(
                (value for key, value in node.attrib.items() if _xml_local(key) == "id" and key.startswith("{")),
                None,
            )
            target = rel_targets.get(rel_id)
            if not target:
                continue
            normalized = posixpath.normpath(posixpath.join("ppt", target)).lstrip("/")
            if normalized in members:
                ordered.append(normalized)
        if ordered:
            return ordered
    slides = [
        name for name in members
        if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
    ]
    return sorted(slides, key=lambda name: int(re.search(r"slide(\d+)\.xml$", name).group(1)))


def _extract_pptx(
    raw: bytes,
    *,
    max_member_bytes: int = OOXML_MAX_MEMBER_BYTES,
    max_total_bytes: int = OOXML_MAX_TOTAL_BYTES,
) -> str:
    import io

    lines: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = _safe_ooxml_members(
            archive,
            max_member_bytes=max_member_bytes,
            max_total_bytes=max_total_bytes,
        )
        slides = _pptx_slide_order(archive, members)
        for index, name in enumerate(slides, 1):
            root = ET.fromstring(_read_ooxml_member(archive, members, name))
            text = " ".join(
                (node.text or "").strip()
                for node in root.iter()
                if _xml_local(node.tag) == "t" and (node.text or "").strip()
            )
            if text:
                lines.append(f"[Slide {index}] {text}")
    return "\n".join(lines)


def _xlsx_column_index(cell_ref: str) -> int | None:
    match = re.match(r"^([A-Za-z]+)\d+$", cell_ref)
    if not match:
        return None
    value = 0
    for char in match.group(1).upper():
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def _extract_xlsx(
    raw: bytes,
    *,
    max_member_bytes: int = OOXML_MAX_MEMBER_BYTES,
    max_total_bytes: int = OOXML_MAX_TOTAL_BYTES,
) -> str:
    import io

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = _safe_ooxml_members(
            archive,
            max_member_bytes=max_member_bytes,
            max_total_bytes=max_total_bytes,
        )
        shared: list[str] = []
        if "xl/sharedStrings.xml" in members:
            root = ET.fromstring(_read_ooxml_member(archive, members, "xl/sharedStrings.xml"))
            for si in root.iter():
                if _xml_local(si.tag) == "si":
                    shared.append("".join(
                        node.text or "" for node in si.iter() if _xml_local(node.tag) == "t"
                    ))
        sheets = [
            name for name in members
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        ]
        sheets = sorted(
            sheets,
            key=lambda name: int(re.search(r"sheet(\d+)\.xml$", name).group(1)),
        )
        out: list[str] = []
        for sheet_index, name in enumerate(sheets, 1):
            root = ET.fromstring(_read_ooxml_member(archive, members, name))
            out.append(f"[Sheet {sheet_index}]")
            for row in root.iter():
                if _xml_local(row.tag) != "row":
                    continue
                values: list[str] = []
                next_column = 0
                for cell in row:
                    if _xml_local(cell.tag) != "c":
                        continue
                    column = _xlsx_column_index(cell.attrib.get("r", ""))
                    if column is None:
                        column = next_column
                    while len(values) < column:
                        values.append("")
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
                    if len(values) == column:
                        values.append(value)
                    else:
                        values[column] = value
                    next_column = column + 1
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
            return None, media_type, "binary_ref", [
                f"non-UTF-8 text-like file treated as binary reference: {source_path}"
            ]
        if suffix in {".html", ".htm"}:
            parser = _VisibleHTML()
            parser.feed(text)
            text = "\n".join(parser.parts)
            return text, "text/html", "text", warnings
        return text, media_type, "text", warnings
    try:
        if suffix == ".docx":
            return (
                _extract_docx(raw),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "text",
                warnings,
            )
        if suffix == ".pptx":
            return (
                _extract_pptx(raw),
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "text",
                warnings,
            )
        if suffix == ".xlsx":
            return (
                _extract_xlsx(raw),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "text",
                warnings,
            )
    except (zipfile.BadZipFile, ET.ParseError, EvidenceError, KeyError) as exc:
        warnings.append(
            f"OOXML extraction failed for {source_path}: {exc}; retained as binary reference"
        )
        return None, media_type, "binary_ref", warnings
    if suffix == ".pdf":
        warnings.append(
            f"PDF text extraction intentionally external; retained hash-only reference: {source_path}"
        )
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
        range_kind = (
            "logical_line"
            if Path(source_path).suffix.casefold() in OOXML_EXTENSIONS | {".html", ".htm"}
            else "line"
        )
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
        identity: dict[str, Any] = {
            "kind": "commit",
            "id": f"git.commit:{head}",
            "commit_sha": head,
        }
        if tree:
            identity["tree_sha"] = tree
        identities.append(identity)
    if head:
        for tag in tags:
            identities.append({
                "kind": "tag",
                "id": f"git.tag:{tag}@{head}",
                "tag": tag,
                "commit_sha": head,
            })
            if SEMVER_TAG.fullmatch(tag):
                identities.append({
                    "kind": "release",
                    "id": f"release.git-tag:{tag}@{head}",
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


def _git_tracked_paths(root: Path) -> list[Path] | None:
    output = _git(root, "ls-files")
    if output is None:
        return None
    paths: list[Path] = []
    for rel in output.splitlines():
        child = root / rel
        if child.is_symlink() or not child.is_file():
            continue
        paths.append(child)
    return sorted(paths, key=lambda p: p.relative_to(root).as_posix())


def _repository_authority(git: dict[str, Any]) -> tuple[str, int, list[str]]:
    if not git.get("git_available"):
        return "source_tree_snapshot", 70, []
    dirty = git.get("dirty")
    if dirty is True:
        return "git_dirty_worktree", 85, []
    if dirty is False and git.get("head_commit") and git.get("head_tree"):
        return "git_clean_head", 95, []
    return (
        "source_tree_snapshot",
        70,
        ["Git worktree status or HEAD identity was unavailable; authority downgraded to source_tree_snapshot"],
    )


def parse_repository(root: Path, *, max_file_bytes: int, max_lines: int = 80) -> AdapterResult:
    if not root.is_dir():
        raise EvidenceError("repository adapter requires a directory")
    git = git_snapshot_metadata(root)
    authority_class, authority_rank, authority_warnings = _repository_authority(git)
    warnings: list[str] = list(authority_warnings)
    if authority_class == "git_clean_head":
        selected = _git_tracked_paths(root)
        if selected is None:
            selected = list(_iter_source_files(root))
            authority_class = "source_tree_snapshot"
            authority_rank = 70
            warnings.append("tracked-file enumeration failed; authority downgraded to source_tree_snapshot")
    else:
        selected = list(_iter_source_files(root))
    items: list[EvidenceItem] = []
    for child in selected:
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
    if authority_class == "git_clean_head":
        layout_id = "git-tree-evidence-v1"
    elif git.get("git_available"):
        layout_id = "git-worktree-evidence-v1"
    else:
        layout_id = "source-tree-evidence-v1"
    return AdapterResult(
        source_type="git-repository" if git.get("git_available") else "source-tree",
        adapter_id="evidence-repository",
        adapter_version="0.1.0",
        layout_id=layout_id,
        parse_status="partial" if warnings else "exact",
        authority_class=authority_class,
        authority_rank=authority_rank,
        items=items,
        warnings=warnings,
        snapshot_metadata={"git": git},
    )


def _looks_drive_root(path: str) -> bool:
    lowered = path.casefold().replace("\\", "/")
    return (
        "/drive/" in f"/{lowered}"
        or lowered.startswith("drive/")
        or "/google drive/" in f"/{lowered}"
    )


def parse_drive_directory(root: Path, *, max_file_bytes: int, max_lines: int = 80) -> AdapterResult:
    if not root.is_dir():
        raise EvidenceError("Drive export adapter requires a directory")
    files = list(_iter_source_files(root))
    drive_files = [p for p in files if _looks_drive_root(p.relative_to(root).as_posix())]
    selected = drive_files or files
    items: list[EvidenceItem] = []
    warnings: list[str] = []
    if not drive_files:
        warnings.append(
            "no recognizable Drive/Google Drive root was found; imported fallback files as partial Drive-export evidence"
        )
    for child in selected:
        rel = child.relative_to(root).as_posix()
        if child.name == "archive_browser.html":
            continue
        size = child.stat().st_size
        if size > max_file_bytes:
            warnings.append(f"skipped oversized Drive export file: {rel}")
            continue
        raw = child.read_bytes()
        file_items, extra = items_for_bytes(
            raw,
            source_path=rel,
            max_lines=max_lines,
            metadata={"drive_export": True},
        )
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
        snapshot_metadata={
            "official_export_surface": "Google Takeout/Drive",
            "drive_root_detected": bool(drive_files),
        },
    )


def _message_children(message: Message) -> list[Message]:
    payload = message.get_payload()
    return payload if isinstance(payload, list) else []


def _message_body(message: Message) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def visit(part: Message, *, root: bool = False) -> None:
        disposition = (part.get_content_disposition() or "").casefold()
        content_type = part.get_content_type()
        if not root and (disposition == "attachment" or content_type == "message/rfc822"):
            return
        if part.is_multipart():
            for child in _message_children(part):
                visit(child)
            return
        if content_type not in {"text/plain", "text/html"}:
            return
        try:
            value = part.get_content()
        except Exception as exc:
            warnings.append(f"failed to decode MIME part: {exc}")
            return
        if not isinstance(value, str):
            return
        if content_type == "text/plain":
            plain_parts.append(value)
        else:
            html_parts.append(value)

    visit(message, root=True)
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
        source_local_id=f"{source_path}#{message_id}",
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


def parse_email_file(path: Path, *, source_path: str | None = None) -> AdapterResult:
    suffix = path.suffix.casefold()
    label = source_path or path.name
    items: list[EvidenceItem] = []
    warnings: list[str] = []
    if suffix == ".eml":
        message = BytesParser(policy=email_policy.default).parsebytes(path.read_bytes())
        item, extra = email_item(message, source_path=label, index=0)
        if item:
            items.append(item)
        warnings.extend(extra)
        layout = "rfc822-eml-v1"
    elif suffix in {".mbox", ".mbx"}:
        box = mailbox.mbox(path, factory=lambda f: BytesParser(policy=email_policy.default).parse(f))
        try:
            for index, message in enumerate(box):
                item, extra = email_item(message, source_path=label, index=index)
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
        snapshot_metadata={
            "standards": ["RFC 5322/MIME"],
            "gmail_label_header": "X-Gmail-Labels",
        },
    )
