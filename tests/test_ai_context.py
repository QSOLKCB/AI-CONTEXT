import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "ai_context.py"


def run_cli(*args, expect=0):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"expected {expect}, got {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class AIContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli("init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def write_candidate(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_generic_import_promote_validate_and_deterministic_bundle(self):
        source = self.root / "notes.json"
        source.write_text(json.dumps({"project": "alpha", "state": "active"}), encoding="utf-8")
        imported = json.loads(run_cli("import", self.workspace, source).stdout)
        self.assertEqual(imported["adapter"], "generic-json")
        observation = imported["observation_ids"][0]
        candidate = self.write_candidate("candidate.json", {
            "record_type": "project_state",
            "content": {"project": "alpha", "state": "active"},
            "epistemic_state": "user_asserted",
            "confidence": 0.8,
            "source_refs": [observation],
            "tags": ["alpha"],
        })
        promoted = json.loads(run_cli("promote", self.workspace, "--input", candidate).stdout)
        self.assertEqual(promoted["appended"], 1)
        run_cli("validate", self.workspace)

        bundle1 = self.root / "bundle1.json"
        bundle2 = self.root / "bundle2.json"
        run_cli("bundle", self.workspace, "--profile", "general", "--tags", "alpha", "--output", bundle1)
        run_cli("bundle", self.workspace, "--profile", "general", "--tags", "alpha", "--output", bundle2)
        self.assertEqual(bundle1.read_bytes(), bundle2.read_bytes())

    def test_duplicate_import_is_idempotent(self):
        source = self.root / "notes.json"
        source.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
        first = json.loads(run_cli("import", self.workspace, source).stdout)
        second = json.loads(run_cli("import", self.workspace, source).stdout)
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(second["observations_appended"], 0)

    def test_chatgpt_style_export_is_staged_not_promoted(self):
        source = self.root / "conversations.json"
        source.write_text(json.dumps([
            {
                "id": "conv-1",
                "title": "Synthetic",
                "mapping": {
                    "u": {"message": {"id": "u", "author": {"role": "user"}, "content": {"parts": ["hello"]}}},
                    "a": {"message": {"id": "a", "author": {"role": "assistant"}, "content": {"parts": ["hi"]}}},
                },
            }
        ]), encoding="utf-8")
        result = json.loads(run_cli("import", self.workspace, source).stdout)
        self.assertEqual(result["adapter"], "chatgpt")
        memory_path = self.workspace / "memory" / "records.jsonl"
        self.assertFalse(memory_path.exists())

    def test_claude_style_export_is_detected(self):
        source = self.root / "claude.json"
        source.write_text(json.dumps([
            {"uuid": "claude-1", "chat_messages": [
                {"uuid": "m1", "sender": "human", "text": "hello"},
                {"uuid": "m2", "sender": "assistant", "text": "hi"},
            ]}
        ]), encoding="utf-8")
        result = json.loads(run_cli("import", self.workspace, source).stdout)
        self.assertEqual(result["adapter"], "claude")

    def test_non_user_asserted_memory_requires_provenance(self):
        candidate = self.write_candidate("claim.json", {
            "record_type": "claim",
            "content": {"statement": "unsupported"},
            "epistemic_state": "inferred",
            "confidence": 0.4,
            "tags": [],
        })
        failed = run_cli("promote", self.workspace, "--input", candidate, expect=2)
        self.assertIn("requires provenance", failed.stderr)

    def test_secret_like_material_is_rejected_anywhere_in_record(self):
        for name, payload in [
            ("content-secret.json", {
                "record_type": "preference",
                "content": {"token": "sk-abcdefghijklmnopqrstuvwxyz123456"},  # synthetic test-only secret-shaped fixture
                "tags": [],
            }),
            ("notes-secret.json", {
                "record_type": "preference",
                "content": {"editor": "vim"},
                "notes": "Bearer abcdefghijklmnopqrstuvwxyz123456",  # synthetic test-only secret-shaped fixture
                "tags": [],
            }),
        ]:
            candidate = self.write_candidate(name, payload)
            failed = run_cli("promote", self.workspace, "--input", candidate, expect=2)
            self.assertIn("secret-like material", failed.stderr)

    def test_nonfinite_json_is_rejected(self):
        candidate = self.root / "nan.json"
        candidate.write_text(
            '{"record_type":"fact","content":{"value":NaN},"epistemic_state":"user_asserted","confidence":1,"tags":[]}',
            encoding="utf-8",
        )
        failed = run_cli("promote", self.workspace, "--input", candidate, expect=2)
        self.assertIn("non-finite", failed.stderr)

    def test_non_string_memory_id_is_rejected(self):
        memory = self.workspace / "memory" / "records.jsonl"
        memory.parent.mkdir(exist_ok=True)
        memory.write_text(json.dumps({
            "id": 123,
            "protocol": "AI-CONTEXT/MEMORY",
            "schema_version": "0.1.0",
            "record_type": "fact",
            "content": {"x": 1},
            "sensitivity": "private",
            "confidence": 1,
            "epistemic_state": "user_asserted",
            "source_refs": [],
            "approval": "approved",
            "tags": [],
            "lifecycle": {"state": "active", "expires_at": None, "supersedes": None},
            "created_at": "2026-01-01T00:00:00Z",
            "last_verified": None,
            "notes": "",
        }) + "\n", encoding="utf-8")
        failed = run_cli("validate", self.workspace, expect=2)
        self.assertIn("id", failed.stderr)

    def test_policy_default_sensitivity_is_applied(self):
        candidate = self.write_candidate("preference.json", {
            "record_type": "preference",
            "content": {"editor": "vim"},
            "epistemic_state": "user_asserted",
            "confidence": 1,
            "tags": [],
        })
        result = json.loads(run_cli("promote", self.workspace, "--input", candidate).stdout)
        self.assertEqual(result["appended"], 1)
        record = json.loads((self.workspace / "memory" / "records.jsonl").read_text().splitlines()[0])
        self.assertEqual(record["sensitivity"], "private")

    def test_user_asserted_memory_can_be_source_free_under_default_policy(self):
        candidate = self.write_candidate("preference.json", {
            "record_type": "preference",
            "content": {"editor": "vim"},
            "epistemic_state": "user_asserted",
            "confidence": 1,
            "tags": [],
        })
        result = json.loads(run_cli("promote", self.workspace, "--input", candidate).stdout)
        self.assertEqual(result["appended"], 1)

    def test_zip_path_traversal_is_rejected(self):
        import zipfile

        archive = self.root / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escape.json", "{}")
        failed = run_cli("import", self.workspace, archive, expect=2)
        self.assertIn("unsafe archive path", failed.stderr)

    def test_repo_import_skips_dot_git_and_binary_files(self):
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("hello", encoding="utf-8")
        (repo / "blob.bin").write_bytes(b"\x00\x01")
        git = repo / ".git"
        git.mkdir()
        (git / "config").write_text("secret", encoding="utf-8")
        result = json.loads(run_cli("import", self.workspace, repo, "--adapter", "repo").stdout)
        self.assertEqual(result["observations_total"], 1)

    def test_repo_import_does_not_follow_symlink_outside_tree(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        repo = self.root / "repo"
        repo.mkdir()
        try:
            (repo / "link.txt").symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation unavailable")
        (repo / "inside.txt").write_text("inside", encoding="utf-8")
        result = json.loads(run_cli("import", self.workspace, repo, "--adapter", "repo").stdout)
        self.assertEqual(result["observations_total"], 1)

    def test_observation_identity_covers_metadata(self):
        source = self.root / "note.txt"
        source.write_text("hello", encoding="utf-8")
        first = json.loads(run_cli("import", self.workspace, source).stdout)
        original = json.loads((self.workspace / "staging" / "observations.jsonl").read_text().splitlines()[0])
        modified = dict(original)
        modified["metadata"] = dict(original["metadata"])
        modified["metadata"]["extra"] = "changed"
        self.assertNotEqual(original["id"], modified["id"])
        self.assertEqual(first["observations_total"], 1)

    def test_promotion_retry_preserves_creation_time(self):
        candidate = self.write_candidate("preference.json", {
            "record_type": "preference",
            "content": {"editor": "vim"},
            "epistemic_state": "user_asserted",
            "confidence": 1,
            "tags": [],
        })
        first = json.loads(run_cli("promote", self.workspace, "--input", candidate).stdout)
        second = json.loads(run_cli("promote", self.workspace, "--input", candidate).stdout)
        self.assertEqual(first["record_ids"], second["record_ids"])
        self.assertEqual(second["appended"], 0)

    def test_failed_batch_promotion_does_not_partially_write(self):
        batch = self.root / "batch.jsonl"
        batch.write_text(
            "\n".join([
                json.dumps({
                    "record_type": "preference",
                    "content": {"editor": "vim"},
                    "epistemic_state": "user_asserted",
                    "confidence": 1,
                    "tags": [],
                }),
                json.dumps({
                    "record_type": "claim",
                    "content": {"statement": "unsupported"},
                    "epistemic_state": "inferred",
                    "confidence": 0.5,
                    "tags": [],
                }),
            ]) + "\n",
            encoding="utf-8",
        )
        failed = run_cli("promote", self.workspace, "--input", batch, expect=2)
        self.assertIn("requires provenance", failed.stderr)
        memory = self.workspace / "memory" / "records.jsonl"
        self.assertFalse(memory.exists())

    def test_explicit_provider_adapter_fails_on_wrong_zip_layout(self):
        import zipfile

        archive = self.root / "wrong.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("notes.json", "{}")
        failed = run_cli("import", self.workspace, archive, "--adapter", "chatgpt", expect=2)
        self.assertIn("does not contain", failed.stderr)

    def test_incomplete_provider_export_is_partial(self):
        source = self.root / "conversations.json"
        source.write_text(json.dumps([
            {
                "id": "conv-1",
                "title": "Synthetic incomplete",
                "mapping": {
                    "u": {"message": {"id": "u", "author": {"role": "user"}, "content": {"parts": ["hello"]}}},
                    "x": {"message": {"id": "x", "author": {"role": "tool"}, "content": {"parts": ["ignored"]}}},
                },
            }
        ]), encoding="utf-8")
        result = json.loads(run_cli("import", self.workspace, source).stdout)
        self.assertEqual(result["parse_status"], "partial")


if __name__ == "__main__":
    unittest.main()
