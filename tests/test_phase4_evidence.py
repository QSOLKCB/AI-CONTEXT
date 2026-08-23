import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
CORE = TOOLS / "ai_context.py"
EVIDENCE = TOOLS / "evidence_import.py"
VALIDATE_EVIDENCE = TOOLS / "validate_evidence.py"

sys.path.insert(0, str(TOOLS))
import ai_context  # noqa: E402
import evidence_adapters  # noqa: E402
import evidence_import  # noqa: E402


def run(*args, expect=0, cwd=ROOT):
    result = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        text=True,
        capture_output=True,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"expected {expect}, got {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def cli(script, *args, expect=0):
    return run(sys.executable, script, *args, expect=expect)


def make_docx(path: Path, text: str) -> None:
    xml = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


def make_mbox_message(*, message_id: str | None, body: str, subject: str = "Synthetic") -> str:
    lines = [
        "From synthetic@example.invalid Sun Aug 23 10:00:00 2026",
        "From: synthetic@example.invalid",
        "To: recipient@example.invalid",
        f"Subject: {subject}",
    ]
    if message_id is not None:
        lines.append(f"Message-ID: {message_id}")
    lines.extend(["Content-Type: text/plain; charset=utf-8", "", body, ""])
    return "\n".join(lines) + "\n"


class Phase4EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        cli(CORE, "init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def read_jsonl(self, rel):
        path = self.workspace / rel
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def validate_all(self):
        cli(CORE, "validate", self.workspace)
        return json.loads(cli(VALIDATE_EVIDENCE, self.workspace).stdout)

    def test_markdown_chunks_keep_exact_line_ranges(self):
        doc = self.root / "notes.md"
        doc.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
        result = json.loads(
            cli(EVIDENCE, self.workspace, doc, "--adapter", "document", "--chunk-lines", "2").stdout
        )
        self.assertEqual(result["observations_total"], 3)
        observations = self.read_jsonl("staging/observations.jsonl")
        ranges = [obs["metadata"]["source_range"] for obs in observations]
        self.assertEqual(ranges, [
            {"kind": "line", "start": 1, "end": 2},
            {"kind": "line", "start": 3, "end": 4},
            {"kind": "line", "start": 5, "end": 5},
        ])
        self.assertEqual(self.validate_all()["content_objects"], 3)

    def test_duplicate_content_collapses_without_losing_provenance(self):
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "a.md").write_text("same evidence\n", encoding="utf-8")
        (docs / "b.txt").write_text("same evidence\n", encoding="utf-8")
        cli(EVIDENCE, self.workspace, docs, "--adapter", "document")
        contents = self.read_jsonl("staging/content.jsonl")
        observations = self.read_jsonl("staging/observations.jsonl")
        self.assertEqual(len(contents), 1)
        self.assertEqual(len(observations), 2)
        self.assertEqual(len({obs["content"]["content_id"] for obs in observations}), 1)
        self.assertEqual(
            {obs["metadata"]["source_path"] for obs in observations},
            {"a.md", "b.txt"},
        )
        result = self.validate_all()
        self.assertEqual(result["duplicate_content_groups"], 1)

    def test_text_and_binary_same_bytes_have_distinct_content_ids(self):
        takeout = self.root / "Takeout" / "Drive"
        takeout.mkdir(parents=True)
        (takeout / "a.txt").write_bytes(b"same-bytes")
        (takeout / "b.bin").write_bytes(b"same-bytes")
        cli(EVIDENCE, self.workspace, self.root / "Takeout", "--adapter", "drive-export")
        contents = self.read_jsonl("staging/content.jsonl")
        self.assertEqual(len(contents), 2)
        self.assertEqual({row["content_kind"] for row in contents}, {"text", "binary_ref"})
        self.assertEqual(len({row["id"] for row in contents}), 2)
        self.assertTrue(any(row["id"].startswith("content.text.sha256:") for row in contents))
        self.assertTrue(any(row["id"].startswith("content.binary.sha256:") for row in contents))
        self.validate_all()

    @unittest.skipUnless(shutil.which("git"), "git is required for repository identity test")
    def test_git_snapshot_records_commit_tag_and_unverified_release_identity(self):
        repo = self.root / "repo"
        repo.mkdir()
        run("git", "init", "-q", repo)
        run("git", "-C", repo, "config", "user.email", "synthetic@example.invalid")
        run("git", "-C", repo, "config", "user.name", "Synthetic Test")
        (repo / "README.md").write_text("# Synthetic\n", encoding="utf-8")
        run("git", "-C", repo, "add", "README.md")
        run("git", "-C", repo, "commit", "-q", "-m", "synthetic")
        run("git", "-C", repo, "tag", "v1.2.3")
        expected_head = run("git", "-C", repo, "rev-parse", "HEAD").stdout.strip()

        cli(EVIDENCE, self.workspace, repo, "--adapter", "repo")
        snapshot = self.read_jsonl("receipts/source-snapshots.jsonl")[0]
        git = snapshot["metadata"]["git"]
        self.assertEqual(git["head_commit"], expected_head)
        self.assertFalse(git["dirty"])
        self.assertIn("v1.2.3", git["tags_at_head"])
        identities = git["identity_records"]
        self.assertEqual({entry["kind"] for entry in identities}, {"commit", "tag", "release"})
        self.assertTrue(all(entry["commit_sha"] == expected_head for entry in identities))
        release = next(entry for entry in identities if entry["kind"] == "release")
        self.assertEqual(release["publication_state"], "unverified")
        self.assertEqual(snapshot["authority"]["class"], "git_clean_head")
        self.assertEqual(snapshot["authority"]["rank"], 95)
        self.validate_all()

        (repo / "README.md").write_text("# Synthetic\nDirty.\n", encoding="utf-8")
        cli(EVIDENCE, self.workspace, repo, "--adapter", "repo")
        snapshots = self.read_jsonl("receipts/source-snapshots.jsonl")
        self.assertEqual(len(snapshots), 2)
        dirty = next(item for item in snapshots if item["authority"]["class"] == "git_dirty_worktree")
        self.assertEqual(dirty["authority"]["rank"], 85)

    @unittest.skipUnless(shutil.which("git"), "git is required for ignored-file test")
    def test_clean_git_snapshot_excludes_ignored_supported_files(self):
        repo = self.root / "repo"
        repo.mkdir()
        run("git", "init", "-q", repo)
        run("git", "-C", repo, "config", "user.email", "synthetic@example.invalid")
        run("git", "-C", repo, "config", "user.name", "Synthetic Test")
        (repo / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
        (repo / "README.md").write_text("tracked\n", encoding="utf-8")
        (repo / "ignored.md").write_text("ignored\n", encoding="utf-8")
        run("git", "-C", repo, "add", ".gitignore", "README.md")
        run("git", "-C", repo, "commit", "-q", "-m", "synthetic")
        result = json.loads(cli(EVIDENCE, self.workspace, repo, "--adapter", "repo").stdout)
        self.assertEqual(result["parse_status"], "exact")
        paths = {row["metadata"]["source_path"] for row in self.read_jsonl("staging/observations.jsonl")}
        self.assertIn("README.md", paths)
        self.assertNotIn("ignored.md", paths)
        self.validate_all()

    def test_unknown_git_status_downgrades_authority(self):
        authority, rank, warnings = evidence_adapters._repository_authority({
            "git_available": True,
            "dirty": None,
            "head_commit": "a" * 40,
            "head_tree": "b" * 40,
        })
        self.assertEqual((authority, rank), ("source_tree_snapshot", 70))
        self.assertTrue(warnings)

    def test_drive_takeout_directory_and_docx_are_ingested(self):
        takeout = self.root / "Takeout" / "Drive"
        takeout.mkdir(parents=True)
        (takeout / "plain.txt").write_text("Drive text\n", encoding="utf-8")
        docx = takeout / "doc.docx"
        make_docx(docx, "Drive DOCX text")

        result = json.loads(
            cli(EVIDENCE, self.workspace, self.root / "Takeout", "--adapter", "drive-export").stdout
        )
        self.assertEqual(result["source_type"], "google-drive-export")
        observations = self.read_jsonl("staging/observations.jsonl")
        self.assertEqual(len(observations), 2)
        self.assertTrue(all(obs["metadata"].get("drive_export") for obs in observations))
        doc_obs = next(
            obs for obs in observations if obs["metadata"]["source_path"].endswith("doc.docx")
        )
        self.assertEqual(doc_obs["metadata"]["source_range"]["kind"], "logical_line")
        self.validate_all()

    def test_standalone_docx_uses_ooxml_extractor_not_generic_zip(self):
        docx = self.root / "standalone.docx"
        make_docx(docx, "Standalone DOCX")
        result = json.loads(cli(EVIDENCE, self.workspace, docx, "--adapter", "document").stdout)
        self.assertEqual(result["source_type"], "document-file")
        self.assertEqual(result["adapter_layout"], "document-chunks-v1")
        content = self.read_jsonl("staging/content.jsonl")[0]
        self.assertEqual(content["text"], "Standalone DOCX")
        self.validate_all()

    def test_ooxml_nested_expansion_limit_is_enforced(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", "<w:document>" + ("x" * 512) + "</w:document>")
        with self.assertRaises(evidence_adapters.EvidenceError):
            evidence_adapters._extract_docx(
                buffer.getvalue(),
                max_member_bytes=128,
                max_total_bytes=256,
            )

    def test_pptx_uses_presentation_relationship_order(self):
        buffer = io.BytesIO()
        presentation = (
            "<p:presentation xmlns:p='urn:p' xmlns:r='urn:r'><p:sldIdLst>"
            "<p:sldId id='256' r:id='rId2'/><p:sldId id='257' r:id='rId1'/>"
            "</p:sldIdLst></p:presentation>"
        )
        rels = (
            "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            "<Relationship Id='rId1' Target='slides/slide10.xml' Type='x'/>"
            "<Relationship Id='rId2' Target='slides/slide2.xml' Type='x'/>"
            "</Relationships>"
        )
        slide2 = "<p:sld xmlns:p='urn:p' xmlns:a='urn:a'><a:t>Second by filename, first by deck</a:t></p:sld>"
        slide10 = "<p:sld xmlns:p='urn:p' xmlns:a='urn:a'><a:t>Tenth by filename, second by deck</a:t></p:sld>"
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("ppt/presentation.xml", presentation)
            archive.writestr("ppt/_rels/presentation.xml.rels", rels)
            archive.writestr("ppt/slides/slide2.xml", slide2)
            archive.writestr("ppt/slides/slide10.xml", slide10)
        text = evidence_adapters._extract_pptx(buffer.getvalue())
        self.assertLess(text.index("Second by filename"), text.index("Tenth by filename"))
        self.assertTrue(text.startswith("[Slide 1] Second by filename"))

    def test_xlsx_sparse_cells_preserve_empty_columns(self):
        buffer = io.BytesIO()
        worksheet = (
            "<worksheet xmlns='urn:x'><sheetData><row r='1'>"
            "<c r='A1' t='inlineStr'><is><t>A</t></is></c>"
            "<c r='C1' t='inlineStr'><is><t>C</t></is></c>"
            "</row></sheetData></worksheet>"
        )
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        text = evidence_adapters._extract_xlsx(buffer.getvalue())
        self.assertIn("A\t\tC", text)

    def test_drive_takeout_zip_is_supported(self):
        archive_path = self.root / "takeout.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("Takeout/Drive/note.md", "# Drive ZIP\n")
            archive.writestr("Takeout/Other/ignore.bin", b"ignored")
        result = json.loads(
            cli(EVIDENCE, self.workspace, archive_path, "--adapter", "drive-export").stdout
        )
        self.assertEqual(result["adapter_layout"], "google-takeout-drive-zip-v1")
        self.assertEqual(result["observations_total"], 1)
        self.validate_all()

    def test_unrecognized_drive_directory_is_partial(self):
        folder = self.root / "arbitrary"
        folder.mkdir()
        (folder / "note.md").write_text("not a recognizable Takeout root\n", encoding="utf-8")
        result = json.loads(cli(EVIDENCE, self.workspace, folder, "--adapter", "drive-export").stdout)
        self.assertEqual(result["parse_status"], "partial")
        self.assertFalse(
            self.read_jsonl("receipts/source-snapshots.jsonl")[0]["metadata"]["drive_root_detected"]
        )
        self.assertTrue(any("no recognizable Drive" in warning for warning in result["warnings"]))

    def test_unrecognized_drive_zip_is_partial(self):
        archive_path = self.root / "arbitrary.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("Random/note.md", "not Takeout\n")
        result = json.loads(
            cli(EVIDENCE, self.workspace, archive_path, "--adapter", "drive-export").stdout
        )
        self.assertEqual(result["parse_status"], "partial")
        self.assertTrue(any("no recognizable Drive" in warning for warning in result["warnings"]))

    def test_gmail_style_eml_preserves_headers_and_labels(self):
        eml = self.root / "message.eml"
        eml.write_text(
            "From: alice@example.invalid\n"
            "To: bob@example.invalid\n"
            "Subject: Synthetic Mail\n"
            "Date: Sun, 23 Aug 2026 10:00:00 +0000\n"
            "Message-ID: <synthetic-1@example.invalid>\n"
            "X-Gmail-Labels: Inbox,Research\n"
            "Content-Type: text/plain; charset=utf-8\n\n"
            "Mail evidence body.\n",
            encoding="utf-8",
        )
        cli(EVIDENCE, self.workspace, eml, "--adapter", "email")
        obs = self.read_jsonl("staging/observations.jsonl")[0]
        self.assertEqual(obs["kind"], "email_message")
        self.assertEqual(obs["metadata"]["gmail_labels"], ["Inbox", "Research"])
        self.assertEqual(obs["metadata"]["message_id"], "<synthetic-1@example.invalid>")
        self.validate_all()

    def test_mbox_archive_is_supported(self):
        mbox = self.root / "mail.mbox"
        mbox.write_text(
            make_mbox_message(message_id="<mbox-1@example.invalid>", body="MBOX body."),
            encoding="utf-8",
        )
        result = json.loads(cli(EVIDENCE, self.workspace, mbox, "--adapter", "email").stdout)
        self.assertEqual(result["observations_total"], 1)
        self.validate_all()

    def test_zip_mbox_without_message_id_is_idempotent(self):
        archive_path = self.root / "mail.zip"
        raw = make_mbox_message(message_id=None, body="No message id.").encode()
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("nested/mail.mbox", raw)
        first = json.loads(cli(EVIDENCE, self.workspace, archive_path, "--adapter", "email").stdout)
        second = json.loads(cli(EVIDENCE, self.workspace, archive_path, "--adapter", "email").stdout)
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(second["observations_appended"], 0)
        self.assertFalse(second["receipt_appended"])
        self.validate_all()

    def test_email_directory_preserves_relative_paths(self):
        root = self.root / "maildir"
        (root / "inbox").mkdir(parents=True)
        (root / "archive").mkdir(parents=True)
        message = make_mbox_message(
            message_id="<same@example.invalid>",
            body="Same body.",
        )
        (root / "inbox" / "mail.mbox").write_text(message, encoding="utf-8")
        (root / "archive" / "mail.mbox").write_text(message, encoding="utf-8")
        cli(EVIDENCE, self.workspace, root, "--adapter", "email")
        observations = self.read_jsonl("staging/observations.jsonl")
        self.assertEqual(len(observations), 2)
        self.assertEqual(
            {obs["metadata"]["source_path"] for obs in observations},
            {"inbox/mail.mbox", "archive/mail.mbox"},
        )
        self.assertEqual(len({obs["content"]["content_id"] for obs in observations}), 1)
        self.validate_all()

    def test_attached_rfc822_message_is_not_flattened_into_parent_body(self):
        parent = EmailMessage()
        parent["From"] = "a@example.invalid"
        parent["To"] = "b@example.invalid"
        parent["Subject"] = "Parent"
        parent["Message-ID"] = "<parent@example.invalid>"
        parent.set_content("Parent body only.")
        attached = EmailMessage()
        attached["From"] = "c@example.invalid"
        attached["To"] = "d@example.invalid"
        attached["Subject"] = "Attached"
        attached.set_content("ATTACHED BODY MUST NOT BE FLATTENED")
        parent.add_attachment(attached)
        eml = self.root / "attached.eml"
        eml.write_bytes(parent.as_bytes())
        cli(EVIDENCE, self.workspace, eml, "--adapter", "email")
        content = self.read_jsonl("staging/content.jsonl")[0]["text"]
        self.assertIn("Parent body only.", content)
        self.assertNotIn("ATTACHED BODY", content)
        self.validate_all()

    def test_email_zip_retains_bodyless_member_warning(self):
        archive_path = self.root / "messages.zip"
        valid = (
            "From: a@example.invalid\nTo: b@example.invalid\nSubject: valid\n"
            "Message-ID: <valid@example.invalid>\nContent-Type: text/plain; charset=utf-8\n\nbody\n"
        )
        bodyless = (
            "From: a@example.invalid\nTo: b@example.invalid\nSubject: bodyless\n"
            "Message-ID: <bodyless@example.invalid>\nContent-Type: application/octet-stream\n\nabc\n"
        )
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("valid.eml", valid)
            archive.writestr("bodyless.eml", bodyless)
        result = json.loads(cli(EVIDENCE, self.workspace, archive_path, "--adapter", "email").stdout)
        self.assertEqual(result["parse_status"], "partial")
        self.assertEqual(result["observations_total"], 1)
        self.assertTrue(any("no textual body" in warning for warning in result["warnings"]))
        self.validate_all()

    def test_pdf_remains_hash_only_reference(self):
        pdf = self.root / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\nsynthetic bytes only\n")
        result = json.loads(cli(EVIDENCE, self.workspace, pdf, "--adapter", "document").stdout)
        self.assertEqual(result["parse_status"], "partial")
        content = self.read_jsonl("staging/content.jsonl")[0]
        self.assertEqual(content["content_kind"], "binary_ref")
        self.assertTrue(content["id"].startswith("content.binary.sha256:"))
        self.assertNotIn("text", content)
        self.assertTrue(
            any("PDF text extraction intentionally external" in warning for warning in result["warnings"])
        )
        self.validate_all()

    def test_evidence_import_is_idempotent(self):
        doc = self.root / "stable.md"
        doc.write_text("Stable evidence.\n", encoding="utf-8")
        first = json.loads(cli(EVIDENCE, self.workspace, doc, "--adapter", "document").stdout)
        second = json.loads(cli(EVIDENCE, self.workspace, doc, "--adapter", "document").stdout)
        self.assertEqual(first["source_snapshot_id"], second["source_snapshot_id"])
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(second["observations_appended"], 0)
        self.assertFalse(second["receipt_appended"])
        self.validate_all()

    def test_validator_binds_observation_snapshot_to_receipt_snapshot(self):
        first = self.root / "first.md"
        second = self.root / "second.md"
        first.write_text("first\n", encoding="utf-8")
        second.write_text("second\n", encoding="utf-8")
        cli(EVIDENCE, self.workspace, first, "--adapter", "document")
        cli(EVIDENCE, self.workspace, second, "--adapter", "document")
        observations = self.read_jsonl("staging/observations.jsonl")
        snapshots = self.read_jsonl("receipts/source-snapshots.jsonl")
        target = observations[0]
        original_snapshot = target["metadata"]["source_snapshot_id"]
        other_snapshot = next(row["id"] for row in snapshots if row["id"] != original_snapshot)
        target["metadata"]["source_snapshot_id"] = other_snapshot
        core = {key: value for key, value in target.items() if key != "id"}
        target["id"] = f"obs.sha256:{ai_context.sha256_bytes(ai_context.canonical_bytes(core))}"
        path = self.workspace / "staging" / "observations.jsonl"
        path.write_text(
            "\n".join(ai_context.canonical_bytes(row).decode() for row in observations) + "\n",
            encoding="utf-8",
        )
        evidence_import.rebuild_content_index(self.workspace)
        cli(CORE, "validate", self.workspace)
        failed = cli(VALIDATE_EVIDENCE, self.workspace, expect=2)
        self.assertIn("snapshot does not match receipt snapshot", failed.stderr)


class Phase4SchemaTests(unittest.TestCase):
    def setUp(self):
        names = [
            "source-authority.schema.json",
            "source-snapshot.schema.json",
            "content-object.schema.json",
            "content-index.schema.json",
            "repository-identity.schema.json",
        ]
        self.schemas = {
            name: json.loads((ROOT / "spec" / name).read_text(encoding="utf-8"))
            for name in names
        }
        registry = Registry()
        for schema in self.schemas.values():
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        self.registry = registry

    def validator(self, name):
        return Draft202012Validator(
            self.schemas[name],
            registry=self.registry,
            format_checker=FormatChecker(),
        )

    def test_phase4_schemas_are_draft_2020_12(self):
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)

    def test_authority_schema_binds_rank_to_class(self):
        self.validator("source-authority.schema.json").validate({
            "class": "git_clean_head",
            "rank": 95,
            "domain": "source_evidence",
        })
        with self.assertRaises(ValidationError):
            self.validator("source-authority.schema.json").validate({
                "class": "git_clean_head",
                "rank": 50,
                "domain": "source_evidence",
            })

    def test_repository_identity_requires_non_null_commit_sha(self):
        validator = self.validator("repository-identity.schema.json")
        validator.validate({
            "kind": "tag",
            "id": "git.tag:v1.0.0@" + "a" * 40,
            "tag": "v1.0.0",
            "commit_sha": "a" * 40,
        })
        with self.assertRaises(ValidationError):
            validator.validate({
                "kind": "tag",
                "id": "git.tag:v1.0.0@unknown",
                "tag": "v1.0.0",
                "commit_sha": None,
            })

    def test_content_schema_requires_kind_specific_id_prefix(self):
        digest = "a" * 64
        validator = self.validator("content-object.schema.json")
        validator.validate({
            "id": f"content.text.sha256:{digest}",
            "protocol": "AI-CONTEXT/CONTENT",
            "schema_version": "0.1.0",
            "content_kind": "text",
            "sha256": digest,
            "byte_length": 1,
            "text": "x",
        })
        with self.assertRaises(ValidationError):
            validator.validate({
                "id": f"content.binary.sha256:{digest}",
                "protocol": "AI-CONTEXT/CONTENT",
                "schema_version": "0.1.0",
                "content_kind": "text",
                "sha256": digest,
                "byte_length": 1,
                "text": "x",
            })


if __name__ == "__main__":
    unittest.main()
