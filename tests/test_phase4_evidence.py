import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "tools" / "ai_context.py"
EVIDENCE = ROOT / "tools" / "evidence_import.py"
VALIDATE_EVIDENCE = ROOT / "tools" / "validate_evidence.py"


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
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def validate_all(self):
        cli(CORE, "validate", self.workspace)
        return json.loads(cli(VALIDATE_EVIDENCE, self.workspace).stdout)

    def test_markdown_chunks_keep_exact_line_ranges(self):
        doc = self.root / "notes.md"
        doc.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
        result = json.loads(cli(EVIDENCE, self.workspace, doc, "--adapter", "document", "--chunk-lines", "2").stdout)
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
        self.assertEqual({obs["metadata"]["source_path"] for obs in observations}, {"a.md", "b.txt"})
        result = self.validate_all()
        self.assertEqual(result["duplicate_content_groups"], 1)

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

    def test_drive_takeout_directory_and_docx_are_ingested(self):
        takeout = self.root / "Takeout" / "Drive"
        takeout.mkdir(parents=True)
        (takeout / "plain.txt").write_text("Drive text\n", encoding="utf-8")
        docx = takeout / "doc.docx"
        xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes'?><w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:p><w:r><w:t>Drive DOCX text</w:t></w:r></w:p></w:body></w:document>"""
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml", xml)

        result = json.loads(cli(EVIDENCE, self.workspace, self.root / "Takeout", "--adapter", "drive-export").stdout)
        self.assertEqual(result["source_type"], "google-drive-export")
        observations = self.read_jsonl("staging/observations.jsonl")
        self.assertEqual(len(observations), 2)
        self.assertTrue(all(obs["metadata"].get("drive_export") for obs in observations))
        doc_obs = next(obs for obs in observations if obs["metadata"]["source_path"].endswith("doc.docx"))
        self.assertEqual(doc_obs["metadata"]["source_range"]["kind"], "logical_line")
        self.validate_all()

    def test_drive_takeout_zip_is_supported(self):
        archive_path = self.root / "takeout.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("Takeout/Drive/note.md", "# Drive ZIP\n")
            archive.writestr("Takeout/Other/ignore.bin", b"ignored")
        result = json.loads(cli(EVIDENCE, self.workspace, archive_path, "--adapter", "drive-export").stdout)
        self.assertEqual(result["adapter_layout"], "google-takeout-drive-zip-v1")
        self.assertEqual(result["observations_total"], 1)
        self.validate_all()

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
            "From synthetic@example.invalid Sun Aug 23 10:00:00 2026\n"
            "From: synthetic@example.invalid\n"
            "To: recipient@example.invalid\n"
            "Subject: MBOX Synthetic\n"
            "Message-ID: <mbox-1@example.invalid>\n"
            "Content-Type: text/plain; charset=utf-8\n\n"
            "MBOX body.\n\n",
            encoding="utf-8",
        )
        result = json.loads(cli(EVIDENCE, self.workspace, mbox, "--adapter", "email").stdout)
        self.assertEqual(result["observations_total"], 1)
        self.validate_all()

    def test_pdf_remains_hash_only_reference(self):
        pdf = self.root / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\nsynthetic bytes only\n")
        result = json.loads(cli(EVIDENCE, self.workspace, pdf, "--adapter", "document").stdout)
        self.assertEqual(result["parse_status"], "partial")
        content = self.read_jsonl("staging/content.jsonl")[0]
        self.assertEqual(content["content_kind"], "binary_ref")
        self.assertNotIn("text", content)
        self.assertTrue(any("PDF text extraction intentionally external" in warning for warning in result["warnings"]))
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


class Phase4SchemaTests(unittest.TestCase):
    def test_phase4_schemas_are_draft_2020_12(self):
        names = [
            "source-authority.schema.json",
            "source-snapshot.schema.json",
            "content-object.schema.json",
            "content-index.schema.json",
            "repository-identity.schema.json",
        ]
        schemas = {
            name: json.loads((ROOT / "spec" / name).read_text(encoding="utf-8"))
            for name in names
        }
        registry = Registry()
        for schema in schemas.values():
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        for name, schema in schemas.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())


if __name__ == "__main__":
    unittest.main()
