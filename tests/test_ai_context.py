import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "ai_context.py"


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

    def test_generic_import_promote_validate_and_deterministic_bundle(self):
        source = self.root / "notes.md"
        source.write_text("Project Alpha uses deterministic receipts.\n", encoding="utf-8")
        imported = run_cli("import", self.workspace, source)
        result = json.loads(imported.stdout)
        self.assertEqual(result["parse_status"], "generic")
        self.assertEqual(result["observations_total"], 1)

        observation = json.loads((self.workspace / "staging" / "observations.jsonl").read_text(encoding="utf-8").strip())
        candidate = self.root / "candidate.json"
        candidate.write_text(json.dumps({
            "record_type": "project_state",
            "content": {"project": "Alpha", "state": "uses deterministic receipts"},
            "sensitivity": "private",
            "epistemic_state": "parsed",
            "confidence": 0.8,
            "approval": "approved",
            "source_refs": [observation["id"]],
            "tags": ["coding", "alpha"],
            "lifecycle": {"state": "active", "expires_at": None, "supersedes": None},
        }), encoding="utf-8")
        run_cli("promote", self.workspace, "--input", candidate)
        run_cli("validate", self.workspace)

        bundle1 = self.root / "bundle1.json"
        bundle2 = self.root / "bundle2.json"
        run_cli("bundle", self.workspace, "--profile", "general", "--output", bundle1)
        run_cli("bundle", self.workspace, "--profile", "general", "--output", bundle2)
        self.assertEqual(bundle1.read_bytes(), bundle2.read_bytes())
        payload = json.loads(bundle1.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["records"]), 1)

    def test_user_asserted_memory_can_be_source_free_under_default_policy(self):
        candidate = self.root / "preference.json"
        candidate.write_text(json.dumps({
            "record_type": "preference",
            "content": {"editor": "vim"},
            "tags": ["coding"]
        }), encoding="utf-8")
        run_cli("promote", self.workspace, "--input", candidate)
        validated = json.loads(run_cli("validate", self.workspace).stdout)
        self.assertEqual(validated["memory_records"], 1)

    def test_non_user_asserted_memory_requires_provenance(self):
        candidate = self.root / "claim.json"
        candidate.write_text(json.dumps({
            "record_type": "claim",
            "content": {"statement": "unsupported"},
            "epistemic_state": "inferred",
            "confidence": 0.4,
            "tags": []
        }), encoding="utf-8")
        failed = run_cli("promote", self.workspace, "--input", candidate, expect=2)
        self.assertIn("requires provenance", failed.stderr)

    def test_secret_like_material_is_rejected(self):
        candidate = self.root / "secret.json"
        candidate.write_text(json.dumps({
            "record_type": "preference",
            "content": {"token": "sk-abcdefghijklmnopqrstuvwxyz123456"},
            "tags": []
        }), encoding="utf-8")
        failed = run_cli("promote", self.workspace, "--input", candidate, expect=2)
        self.assertIn("secret-like material", failed.stderr)

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
                        "content": {"parts": ["Remember this only after curation."]}
                    }
                },
                "node-2": {
                    "message": {
                        "id": "msg-2",
                        "author": {"role": "assistant"},
                        "create_time": 2,
                        "content": {"parts": ["Imported AI output is not verified fact."]}
                    }
                }
            }
        }]
        with zipfile.ZipFile(export, "w") as archive:
            archive.writestr("conversations.json", json.dumps(conversations))
        result = json.loads(run_cli("import", self.workspace, export, "--adapter", "auto").stdout)
        self.assertEqual(result["source_type"], "chatgpt-export")
        self.assertEqual(result["observations_total"], 2)
        self.assertFalse((self.workspace / "memory" / "records.jsonl").exists())

    def test_claude_style_export_is_detected(self):
        export = self.root / "claude.json"
        export.write_text(json.dumps([{
            "uuid": "conv-1",
            "name": "Synthetic Claude chat",
            "chat_messages": [{
                "uuid": "msg-1",
                "sender": "human",
                "created_at": "2026-01-01T00:00:00Z",
                "text": "hello"
            }]
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


if __name__ == "__main__":
    unittest.main()
