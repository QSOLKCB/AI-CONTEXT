import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import release_audit  # noqa: E402


class ReleaseAuditUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rel: str, data: str | bytes = "ok\n") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")

    def audit(self, *paths: str):
        return release_audit.audit_paths(self.root, paths, commit="0" * 40)

    def init_git_repo(self, root: Path) -> None:
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "AI-CONTEXT Test"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)

    def commit_all(self, root: Path, message: str = "fixture") -> str:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-m", message], check=True, capture_output=True)
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def test_clean_public_tree_passes(self):
        self.write("README.md", "public framework\n")
        self.write("spec/example.schema.json", '{"type":"object"}\n')
        receipt = self.audit("README.md", "spec/example.schema.json")
        self.assertEqual(receipt["status"], "ok")
        self.assertEqual(receipt["findings"], [])
        self.assertEqual(receipt["claim"], release_audit.AUDIT_CLAIM)

    def test_private_workspace_paths_fail(self):
        self.write("workspace/memory/records.jsonl", "{}\n")
        receipt = self.audit("workspace/memory/records.jsonl")
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("private-workspace-path", {row["kind"] for row in receipt["findings"]})

    def test_key_restore_and_generated_artifact_names_fail(self):
        for rel in ("secret.key", "backup.aicr", "build/cache.tar.gz"):
            self.write(rel)
        receipt = self.audit("secret.key", "backup.aicr", "build/cache.tar.gz")
        kinds = {row["kind"] for row in receipt["findings"]}
        self.assertIn("private-or-key-artifact-filename", kinds)
        self.assertIn("generated-archive", kinds)
        self.assertIn("generated-runtime-path", kinds)

    def test_common_build_and_cache_directories_fail_for_ordinary_files(self):
        paths = ["build/output.json", "dist/app.txt", "cache/state.json", ".cache/result.txt"]
        for rel in paths:
            self.write(rel)
        receipt = self.audit(*paths)
        runtime_paths = {
            row["path"] for row in receipt["findings"] if row["kind"] == "generated-runtime-path"
        }
        self.assertEqual(runtime_paths, set(paths))

    def test_common_generated_archive_suffixes_fail(self):
        paths = [
            "backup.tar.xz",
            "backup.tar.bz2",
            "backup.rar",
            "backup.gz",
            "backup.bz2",
            "backup.xz",
        ]
        for rel in paths:
            self.write(rel)
        receipt = self.audit(*paths)
        archive_paths = {
            row["path"] for row in receipt["findings"] if row["kind"] == "generated-archive"
        }
        self.assertEqual(archive_paths, set(paths))

    def test_real_secret_shape_fails_but_explicit_synthetic_annotation_is_allowed(self):
        real = "AKIA" + "A" * 16
        self.write("docs/leak.txt", f"credential={real}\n")
        self.write("tests/fixture.txt", f"synthetic test-only credential={real}\n")
        receipt = self.audit("docs/leak.txt", "tests/fixture.txt")
        findings = [row for row in receipt["findings"] if row["kind"] == "secret-shaped-text"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["path"], "docs/leak.txt")

    def test_generic_test_name_does_not_exempt_real_secret_shape(self):
        secret = "sk-" + "A" * 32
        self.write("tests/test_credentials.py", f'test_key = "{secret}"\n')
        receipt = self.audit("tests/test_credentials.py")
        self.assertIn("secret-shaped-text", {row["kind"] for row in receipt["findings"]})

    def test_nul_containing_blob_is_still_scanned_for_secret_shape(self):
        self.write("docs/blob.dat", b"prefix\x00-----BEGIN PRIVATE KEY-----\x00suffix")  # synthetic test-only secret-shaped fixture
        receipt = self.audit("docs/blob.dat")
        self.assertIn("secret-shaped-text", {row["kind"] for row in receipt["findings"]})

    def test_oversized_blob_fails_closed_instead_of_being_skipped(self):
        self.write("docs/large.bin", b"123456789")
        with mock.patch.object(release_audit, "MAX_SECRET_SCAN_BYTES", 8):
            receipt = self.audit("docs/large.bin")
        self.assertIn(
            "unscanned-oversized-tracked-file",
            {row["kind"] for row in receipt["findings"]},
        )

    @unittest.skipIf(os.name == "nt", "symlink semantics vary on Windows")
    def test_tracked_symlink_fails_closed(self):
        self.write("target.txt", "public\n")
        os.symlink(self.root / "target.txt", self.root / "linked.txt")
        receipt = self.audit("linked.txt")
        self.assertIn("tracked-symlink", {row["kind"] for row in receipt["findings"]})

    def test_repository_audit_reads_reported_commit_not_dirty_worktree(self):
        repo = self.root / "repo"
        repo.mkdir()
        self.init_git_repo(repo)
        leak = repo / "docs" / "leak.txt"
        leak.parent.mkdir(parents=True)
        leak.write_text("credential=sk-" + "A" * 32 + "\n", encoding="utf-8")
        commit = self.commit_all(repo, "unsafe committed fixture")

        # Dirty the working tree to look safe. The audit must still read the unsafe HEAD blob.
        leak.write_text("clean working-tree replacement\n", encoding="utf-8")
        receipt = release_audit.audit_repository(repo)
        self.assertEqual(receipt["git_commit"], commit)
        self.assertIn(
            "docs/leak.txt",
            {row["path"] for row in receipt["findings"] if row["kind"] == "secret-shaped-text"},
        )

    def test_repository_audit_from_subdirectory_still_covers_repo_top_level(self):
        repo = self.root / "repo"
        repo.mkdir()
        self.init_git_repo(repo)
        (repo / "docs").mkdir()
        (repo / "docs" / "README.txt").write_text("public\n", encoding="utf-8")
        (repo / "build").mkdir()
        (repo / "build" / "output.json").write_text("{}\n", encoding="utf-8")
        self.commit_all(repo)

        receipt = release_audit.audit_repository(repo / "docs")
        self.assertIn(
            "build/output.json",
            {row["path"] for row in receipt["findings"] if row["kind"] == "generated-runtime-path"},
        )
        self.assertEqual(receipt["tracked_files"], 2)


class ReleaseCandidateCurrentTreeTests(unittest.TestCase):
    def test_current_tracked_public_tree_passes_release_audit_and_schema(self):
        receipt = release_audit.audit_repository(ROOT)
        if receipt["status"] != "ok":
            self.fail(f"release audit findings: {receipt['findings']}")
        self.assertEqual(receipt["audit_scope"], "tracked-public-git-tree")
        self.assertGreater(receipt["tracked_files"], 0)
        self.assertRegex(receipt["tracked_tree_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(receipt["git_commit"], r"^[0-9a-f]{40}$")

        schema = json.loads((ROOT / "spec" / "release-audit.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(receipt)), [])

    def test_v1_freeze_declaration_retains_reference_canonicalizer(self):
        value = json.loads((ROOT / "release" / "v1-freeze.json").read_text(encoding="utf-8"))
        self.assertEqual(value["protocol"], "AI-CONTEXT/V1-FREEZE")
        self.assertEqual(value["release"], "v1.0.0")
        self.assertEqual(value["reference_protocol_version"], "0.1.0")
        self.assertEqual(value["canonicalization"]["active"], "python-json-v0.1")
        self.assertEqual(value["canonicalization"]["rfc8785_jcs"], "evaluated-not-adopted")
        self.assertTrue(value["release_tree_audit"]["required"])


if __name__ == "__main__":
    unittest.main()
