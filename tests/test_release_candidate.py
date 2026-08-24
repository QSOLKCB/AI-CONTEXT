import os
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_real_secret_shape_fails_but_marked_synthetic_test_fixture_is_allowed(self):
        real = "AKIA" + "A" * 16
        self.write("docs/leak.txt", f"credential={real}\n")
        self.write("tests/fixture.txt", f"synthetic test-only credential={real}\n")
        receipt = self.audit("docs/leak.txt", "tests/fixture.txt")
        findings = [row for row in receipt["findings"] if row["kind"] == "secret-shaped-text"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["path"], "docs/leak.txt")

    @unittest.skipIf(os.name == "nt", "symlink semantics vary on Windows")
    def test_tracked_symlink_fails_closed(self):
        self.write("target.txt", "public\n")
        os.symlink(self.root / "target.txt", self.root / "linked.txt")
        receipt = self.audit("linked.txt")
        self.assertIn("tracked-symlink", {row["kind"] for row in receipt["findings"]})


class ReleaseCandidateCurrentTreeTests(unittest.TestCase):
    def test_current_tracked_public_tree_passes_release_audit(self):
        receipt = release_audit.audit_repository(ROOT)
        if receipt["status"] != "ok":
            self.fail(f"release audit findings: {receipt['findings']}")
        self.assertEqual(receipt["audit_scope"], "tracked-public-git-tree")
        self.assertGreater(receipt["tracked_files"], 0)
        self.assertRegex(receipt["tracked_tree_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
