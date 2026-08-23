import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Historical Phase 2 promotion behavior is verified against the preserved compatibility CLI.
CLI = ROOT / "tools" / "ai_context_legacy.py"


def run_cli(*args, expect=0):
    result = subprocess.run(
        [sys.executable, str(CLI), *map(str, args)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"expected {expect}, got {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class Phase2ConformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli("init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def write_policy(self, **changes):
        path = self.workspace / "policy.json"
        policy = json.loads(path.read_text(encoding="utf-8"))
        policy.update(changes)
        path.write_text(json.dumps(policy), encoding="utf-8")

    def write_candidate(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_unknown_adapter_is_rejected(self):
        source = self.root / "notes.md"
        source.write_text("hello\n", encoding="utf-8")
        failed = run_cli("import", self.workspace, source, "--adapter", "future-provider", expect=2)
        self.assertIn("invalid choice", failed.stderr)
        self.assertFalse((self.workspace / "receipts" / "imports.jsonl").exists())

    def test_unknown_workspace_schema_is_rejected(self):
        self.write_policy(schema_version="9.0.0")
        failed = run_cli("validate", self.workspace, expect=2)
        self.assertIn("unsupported workspace policy schema version", failed.stderr)

    def test_unknown_memory_schema_is_rejected(self):
        candidate = self.write_candidate("future-memory.json", {
            "protocol": "AI-CONTEXT/MEMORY", "schema_version": "9.0.0",
            "record_type": "preference", "content": {"editor": "vim"}, "tags": [],
        })
        failed = run_cli("promote", self.workspace, "--input", candidate, expect=2)
        self.assertIn("unsupported memory protocol/schema version", failed.stderr)
        self.assertFalse((self.workspace / "memory" / "records.jsonl").exists())

    def test_oversized_archive_member_is_rejected(self):
        self.write_policy(max_archive_member_bytes=4, max_archive_total_bytes=16)
        archive_path = self.root / "oversized.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("notes.txt", "12345")
        failed = run_cli("import", self.workspace, archive_path, expect=2)
        self.assertIn("archive member too large", failed.stderr)
        self.assertFalse((self.workspace / "receipts" / "imports.jsonl").exists())

    def test_oversized_repository_file_is_skipped_and_marked_partial(self):
        self.write_policy(max_repo_file_bytes=4)
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("12345", encoding="utf-8")
        result = json.loads(run_cli("import", self.workspace, repo, "--adapter", "repo").stdout)
        self.assertEqual(result["parse_status"], "partial")
        self.assertEqual(result["observations_total"], 0)
        self.assertTrue(any("oversized repository file" in warning for warning in result["warnings"]))
        run_cli("validate", self.workspace)

    def test_privacy_classification_never_downgrades_in_bundles(self):
        candidate = self.write_candidate("restricted.json", {
            "id": "memory.restricted.fixture", "record_type": "preference",
            "content": {"editor": "vim"}, "sensitivity": "restricted", "tags": ["coding"],
        })
        run_cli("promote", self.workspace, "--input", candidate)
        private_bundle = self.root / "private-bundle.json"
        run_cli("bundle", self.workspace, "--profile", "general", "--output", private_bundle)
        self.assertEqual(json.loads(private_bundle.read_text(encoding="utf-8"))["records"], [])
        restricted_profile = self.workspace / "profiles" / "restricted.json"
        restricted_profile.write_text(json.dumps({
            "protocol": "AI-CONTEXT/PROFILE", "schema_version": "0.1.0", "name": "restricted",
            "include_tags": [], "exclude_tags": [], "record_types": [], "max_sensitivity": "restricted",
        }), encoding="utf-8")
        restricted_bundle = self.root / "restricted-bundle.json"
        run_cli("bundle", self.workspace, "--profile", "restricted", "--output", restricted_bundle)
        records = json.loads(restricted_bundle.read_text(encoding="utf-8"))["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["sensitivity"], "restricted")

    def test_tombstone_propagates_to_rebuilt_disclosure_bundle(self):
        candidate = self.write_candidate("delete-me.json", {
            "id": "memory.tombstone.fixture", "record_type": "preference",
            "content": {"editor": "vim"}, "sensitivity": "private", "tags": [],
        })
        run_cli("promote", self.workspace, "--input", candidate)
        before = self.root / "before.json"
        run_cli("bundle", self.workspace, "--output", before)
        before_payload = json.loads(before.read_text(encoding="utf-8"))
        self.assertEqual([r["id"] for r in before_payload["records"]], ["memory.tombstone.fixture"])
        memory_path = self.workspace / "memory" / "records.jsonl"
        record = json.loads(memory_path.read_text(encoding="utf-8").strip())
        record["lifecycle"]["state"] = "tombstoned"
        memory_path.write_text(json.dumps(record, separators=(",", ":")) + "\n", encoding="utf-8")
        run_cli("validate", self.workspace)
        after = self.root / "after.json"
        run_cli("bundle", self.workspace, "--output", after)
        after_payload = json.loads(after.read_text(encoding="utf-8"))
        self.assertEqual(after_payload["records"], [])
        self.assertNotEqual(before_payload["canonical_store_sha256"], after_payload["canonical_store_sha256"])
        retained = json.loads(memory_path.read_text(encoding="utf-8").strip())
        self.assertEqual(retained["lifecycle"]["state"], "tombstoned")


if __name__ == "__main__":
    unittest.main()
