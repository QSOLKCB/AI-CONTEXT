import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Historical Phase 1 behavior is tested against the preserved compatibility module.
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


class AIContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli("init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def write_candidate(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_generic_import_promote_validate_and_deterministic_bundle(self):
        source = self.root / "notes.md"
        source.write_text("Project Alpha uses deterministic receipts.\n", encoding="utf-8")
        imported = run_cli("import", self.workspace, source)
        result = json.loads(imported.stdout)
        self.assertEqual(result["parse_status"], "generic")
        self.assertEqual(result["observations_total"], 1)

        observation = json.loads(
            (self.workspace / "staging" / "observations.jsonl").read_text(encoding="utf-8").strip()
        )
        candidate = self.write_candidate("candidate.json", {
            "record_type": "project_state",
            "content": {"project": "Alpha", "state": "uses deterministic receipts"},
            "sensitivity": "private",
            "epistemic_state": "parsed",
            "confidence": 0.8,
            "approval": "approved",
            "source_refs": [observation["id"]],
            "tags": ["coding", "alpha"],
            "lifecycle": {"state": "active", "expires_at": None, "supersedes": None},
        })
        run_cli("promote", self.workspace, "--input", candidate)
        run_cli("validate", self.workspace)

        bundle1 = self.root / "bundle1.json"
        bundle2 = self.root / "bundle2.json"
        run_cli("bundle", self.workspace, "--profile", "general", "--output", bundle1)
        run_cli("bundle", self.workspace, "--profile", "general", "--output", bundle2)
        self.assertEqual(bundle1.read_bytes(), bundle2.read_bytes())
        payload = json.loads(bundle1.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["records"]), 1)

    def test_duplicate_import_is_idempotent(self):
        source = self.root / "notes.md"
        source.write_text("same source\n", encoding="utf-8")
        first = json.loads(run_cli("import", self.workspace, source).stdout)
        second = json.loads(run_cli("import", self.workspace, source).stdout)
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(second["observations_appended"], 0)
        self.assertFalse(second["receipt_appended"])
        run_cli("validate", self.workspace)

    def test_user_asserted_memory_can_be_source_free_under_default_policy(self):
        candidate = self.write_candidate("preference.json", {
            "record_type": "preference",
            "content": {"editor": "vim"},
            "tags": ["coding"],
        })
        run_cli("promote", self.workspace, "--input", candidate)
        validated = json.loads(run_cli("validate", self.workspace).stdout)
        self.assertEqual(validated["memory_records"], 1)

    def test_policy_default_sensitivity_is_applied(self):
        policy_path = self.workspace / "policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["default_sensitivity"] = "restricted"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        candidate = self.write_candidate("restricted.json", {
            "record_type": "preference",
            "content": {"editor": "vim"},
            "tags": [],
        })
        run_cli("promote", self.workspace, "--input", candidate)
        record = json.loads((self.workspace / "memory" / "records.jsonl").read_text(encoding="utf-8").strip())
        self.assertEqual(record["sensitivity"], "restricted")
        bundle = self.root / "bundle.json"
        run_cli("bundle", self.workspace, "--output", bundle)
        self.assertEqual(json.loads(bundle.read_text(encoding="utf-8"))["records"], [])

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
                "content": {"token": "sk-abcdefghijklmnopqrstuvwxyz123456"},
                "tags": [],
            }),
            ("notes-secret.json", {
                "record_type": "preference",
                "content": {"editor": "vim"},
                "notes": "Bearer abcdefghijklmnopqrstuvwxyz123456",
                "tags": [],
            }),
        ]:
            candidate = self.write_candidate(name, payload)
            failed = run_cli("promote", self.workspace, "--input", candidate, expect=2)
            self.assertIn("secret-like material", failed.stderr)

    def test_nonfinite_json_is_rejected(self):
        candidate = self.root / "nan.json"
        candidate.write_text(
            '{"record_type":"preference","content":{"score":NaN},"tags":[]}',
            encoding="utf-8",
        )
        failed = run_cli("promote", self.workspace, "--input", candidate, expect=2)
        self.assertIn("non-finite JSON number is forbidden", failed.stderr)

    def test_non_string_memory_id_is_rejected(self):
        candidate = self.write_candidate("numeric-id.json", {
            "id": 123,
            "record_type": "preference",
            "content": {"editor": "vim"},
            "tags": [],
        })
        failed = run_cli("promote", self.workspace, "--input", candidate, expect=2)
        self.assertIn("memory id must be a non-empty string", failed.stderr)

    def test_promotion_retry_preserves_creation_time(self):
        candidate = self.write_candidate("retry.json", {
            "record_type": "preference",
            "content": {"editor": "vim"},
            "tags": [],
        })
        run_cli("promote", self.workspace, "--input", candidate)
        memory_path = self.workspace / "memory" / "records.jsonl"
        record = json.loads(memory_path.read_text(encoding="utf-8").strip())
        record["created_at"] = "2000-01-01T00:00:00Z"
        memory_path.write_text(json.dumps(record, separators=(",", ":")) + "\n", encoding="utf-8")
        retried = json.loads(run_cli("promote", self.workspace, "--input", candidate).stdout)
        self.assertEqual(retried["promoted"], 0)
        persisted = json.loads(memory_path.read_text(encoding="utf-8").strip())
        self.assertEqual(persisted["created_at"], "2000-01-01T00:00:00Z")

    def test_failed_batch_promotion_does_not_partially_write(self):
        existing = self.write_candidate("existing.json", {
            "id": "memory.existing",
            "record_type": "preference",
            "content": {"editor": "vim"},
            "tags": [],
        })
        run_cli("promote", self.workspace, "--input", existing)
        batch = self.write_candidate("batch.json", [
            {
                "id": "memory.new",
                "record_type": "preference",
                "content": {"shell": "bash"},
                "tags": [],
            },
            {
                "id": "memory.existing",
                "record_type": "preference",
                "content": {"editor": "emacs"},
                "tags": [],
            },
        ])
        failed = run_cli("promote", self.workspace, "--input", batch, expect=2)
        self.assertIn("id collision", failed.stderr)
        records = [json.loads(line) for line in (self.workspace / "memory" / "records.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([record["id"] for record in records], ["memory.existing"])

    def test_chatgpt_style_export_is_staged_not_promoted(self):
        export = self.root / "chatgpt.zip"
        conversations = [{
            "id": "conv-1",
            "title": "Synthetic chat",
            "mapping": {
                "node-1": {
                    "message": {
                        "id": "msg-1",
                        "author": {"role": "user"},
                        "create_time": 1,
                        "content": {"parts": ["Remember this only after curation."]},
                    }
                },
                "node-2": {
                    "message": {
                        "id": "msg-2",
                        "author": {"role": "assistant"},
                        "create_time": 2,
                        "content": {"parts": ["Imported AI output is not verified fact."]},
                    }
                },
            },
        }]
        with zipfile.ZipFile(export, "w") as archive:
            archive.writestr("conversations.json", json.dumps(conversations))
        result = json.loads(run_cli("import", self.workspace, export, "--adapter", "auto").stdout)
        self.assertEqual(result["source_type"], "chatgpt-export")
        self.assertEqual(result["observations_total"], 2)
        self.assertEqual(result["parse_status"], "exact")
        self.assertFalse((self.workspace / "memory" / "records.jsonl").exists())

    def test_incomplete_provider_export_is_partial(self):
        export = self.root / "partial.json"
        export.write_text(json.dumps([
            {"id": "conv-1", "mapping": {}},
            {"unexpected": True},
        ]), encoding="utf-8")
        result = json.loads(run_cli("import", self.workspace, export, "--adapter", "chatgpt").stdout)
        self.assertEqual(result["parse_status"], "partial")
        self.assertTrue(result["warnings"])

    def test_explicit_provider_adapter_fails_on_wrong_zip_layout(self):
        export = self.root / "not-chatgpt.zip"
        with zipfile.ZipFile(export, "w") as archive:
            archive.writestr("notes.json", json.dumps({"hello": "world"}))
        failed = run_cli("import", self.workspace, export, "--adapter", "chatgpt", expect=2)
        self.assertIn("expected conversations.json", failed.stderr)

    def test_claude_style_export_is_detected(self):
        export = self.root / "claude.json"
        export.write_text(json.dumps([{
            "uuid": "conv-1",
            "name": "Synthetic Claude chat",
            "chat_messages": [{
                "uuid": "msg-1",
                "sender": "human",
                "created_at": "2026-01-01T00:00:00Z",
                "text": "hello",
            }],
        }]), encoding="utf-8")
        result = json.loads(run_cli("import", self.workspace, export).stdout)
        self.assertEqual(result["source_type"], "claude-export")
        self.assertEqual(result["observations_total"], 1)

    def test_zip_path_traversal_is_rejected(self):
        archive_path = self.root / "evil.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../escape.txt", "nope")
        failed = run_cli("import", self.workspace, archive_path, expect=2)
        self.assertIn("unsafe archive path", failed.stderr)

    def test_repo_import_skips_dot_git_and_binary_files(self):
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config").write_text("secret-ish metadata", encoding="utf-8")
        (repo / "README.md").write_text("hello repo", encoding="utf-8")
        (repo / "asset.bin").write_bytes(b"\x00\x01\x02")
        result = json.loads(run_cli("import", self.workspace, repo, "--adapter", "repo").stdout)
        self.assertEqual(result["source_type"], "git-repository")
        self.assertEqual(result["observations_total"], 1)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_repo_import_does_not_follow_symlink_outside_tree(self):
        outside = self.root / "outside.md"
        outside.write_text("must not be imported", encoding="utf-8")
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("safe", encoding="utf-8")
        os.symlink(outside, repo / "outside-link.md")
        result = json.loads(run_cli("import", self.workspace, repo, "--adapter", "repo").stdout)
        self.assertEqual(result["observations_total"], 1)
        self.assertTrue(any("symlink" in warning for warning in result["warnings"]))

    def test_observation_identity_covers_metadata(self):
        source = self.root / "notes.md"
        source.write_text("source\n", encoding="utf-8")
        run_cli("import", self.workspace, source)
        staging = self.workspace / "staging" / "observations.jsonl"
        observation = json.loads(staging.read_text(encoding="utf-8").strip())
        observation["metadata"] = {"tampered": True}
        staging.write_text(json.dumps(observation, separators=(",", ":")) + "\n", encoding="utf-8")
        failed = run_cli("validate", self.workspace, expect=2)
        self.assertIn("observation id/hash mismatch", failed.stderr)


if __name__ == "__main__":
    unittest.main()
