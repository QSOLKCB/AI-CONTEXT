import builtins
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import ai_context_legacy as core  # noqa: E402
import ux  # noqa: E402

CORE = TOOLS / "ai_context.py"
CURATION = TOOLS / "curation.py"
UX = TOOLS / "ux.py"


def run_cli(script, *args, expect=0):
    result = subprocess.run(
        [sys.executable, str(script), *map(str, args)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"expected {expect}, got {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def tree_snapshot(root: Path):
    result = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[rel] = ("symlink", os.readlink(path))
        elif path.is_file():
            result[rel] = ("file", path.read_bytes())
        elif path.is_dir():
            result[rel] = ("dir", None)
    return result


def optional_bytes(path: Path):
    return path.read_bytes() if path.exists() else None


class Phase11UXTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli(CORE, "init", self.workspace)
        run_cli(CURATION, "init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def propose(self, name, *, sensitivity="private", semantic_key=None):
        path = self.root / f"{name}.json"
        path.write_text(json.dumps({"subject": name}), encoding="utf-8")
        args = [
            "propose", self.workspace,
            "--content-file", path,
            "--record-type", "claim",
            "--sensitivity", sensitivity,
            "--epistemic-state", "user_asserted",
            "--confidence", "1",
        ]
        if semantic_key:
            args += ["--semantic-key", semantic_key]
        return json.loads(run_cli(CURATION, *args).stdout)["candidate_id"]

    def create_memory(self, name, *, sensitivity="private", semantic_key=None):
        candidate = self.propose(name, sensitivity=sensitivity, semantic_key=semantic_key)
        run_cli(
            CURATION, "review", self.workspace,
            "--candidate", candidate,
            "--decision", "approve",
            "--actor-type", "human",
            "--actor-label", "phase11-test",
        )
        return json.loads(
            run_cli(CURATION, "apply", self.workspace, "--candidate", candidate).stdout
        )["memory_id"]

    def test_read_only_status_provenance_and_bundle_inspection_do_not_mutate_workspace(self):
        memory_id = self.create_memory("read-only-surface", sensitivity="public")
        before = tree_snapshot(self.workspace)

        status = ux.workspace_status(self.workspace)
        provenance = ux.provenance_read_only(self.workspace, memory_id)
        bundle = ux.inspect_bundle_read_only(
            self.workspace,
            profile="general",
            target="local-default",
            task="read-only-surface",
        )

        self.assertEqual(status["status"], "ok")
        self.assertTrue(provenance["read_only"])
        self.assertTrue(bundle["read_only"])
        self.assertIn(memory_id, {row["id"] for row in bundle["exact_payload"]["records"]})
        self.assertEqual(tree_snapshot(self.workspace), before)

    def test_noninteractive_mutations_require_yes(self):
        source = self.root / "source.json"
        source.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
        before = tree_snapshot(self.workspace)

        failed = run_cli(UX, "import", self.workspace, source, expect=2)
        self.assertIn("--yes", failed.stderr)
        self.assertEqual(tree_snapshot(self.workspace), before)

        result = json.loads(run_cli(UX, "import", self.workspace, source, "--yes").stdout)
        self.assertIn("receipt_id", result)

    def test_review_and_application_remain_separate_authority_events(self):
        candidate = self.propose("two-step-review")
        memory_path = self.workspace / "memory" / "records.jsonl"
        before_memory = optional_bytes(memory_path)

        failed_review = run_cli(
            UX, "review", self.workspace,
            "--candidate", candidate,
            "--decision", "approve",
            expect=2,
        )
        self.assertIn("--yes", failed_review.stderr)
        self.assertEqual(optional_bytes(memory_path), before_memory)

        reviewed = json.loads(run_cli(
            UX, "review", self.workspace,
            "--candidate", candidate,
            "--decision", "approve",
            "--yes",
        ).stdout)
        self.assertEqual(reviewed["decision"], "approve")
        self.assertEqual(optional_bytes(memory_path), before_memory)

        failed_apply = run_cli(UX, "apply", self.workspace, "--candidate", candidate, expect=2)
        self.assertIn("--yes", failed_apply.stderr)
        self.assertEqual(optional_bytes(memory_path), before_memory)

        applied = json.loads(run_cli(
            UX, "apply", self.workspace, "--candidate", candidate, "--yes"
        ).stdout)
        self.assertTrue(applied["memory_appended"])
        self.assertNotEqual(optional_bytes(memory_path), before_memory)

    def test_conflict_explorer_is_read_only_and_does_not_persist_conflict_rows(self):
        self.create_memory("conflict-old", semantic_key="claim:phase11-conflict")
        candidate = self.propose("conflict-new", semantic_key="claim:phase11-conflict")
        conflicts_path = self.workspace / "curation" / "conflicts.jsonl"
        before = conflicts_path.read_bytes() if conflicts_path.exists() else None

        result = ux.candidate_conflicts_read_only(self.workspace, candidate)

        self.assertTrue(result["read_only"])
        self.assertEqual(len(result["conflicts"]), 1)
        after = conflicts_path.read_bytes() if conflicts_path.exists() else None
        self.assertEqual(after, before)

    def test_bundle_inspector_shows_exact_provider_payload_without_writing_bundle(self):
        public_id = self.create_memory("public-preview", sensitivity="public")
        private_id = self.create_memory("private-preview", sensitivity="private")
        before = tree_snapshot(self.workspace)

        result = ux.inspect_bundle_read_only(
            self.workspace,
            profile="general",
            target="provider-default",
        )
        ids = {row["id"] for row in result["exact_payload"]["records"]}

        self.assertIn(public_id, ids)
        self.assertNotIn(private_id, ids)
        self.assertEqual(result["summary"]["records"], len(ids))
        self.assertFalse((self.workspace / "bundles" / "ux-preview.json").exists())
        self.assertEqual(tree_snapshot(self.workspace), before)

    def test_one_command_backup_restore_requires_confirmation_and_roundtrips(self):
        memory_id = self.create_memory("portable-ux")
        archive = self.root / "ux-backup.aicr"
        destination = self.root / "restored"

        failed_backup = run_cli(UX, "backup", self.workspace, archive, expect=2)
        self.assertIn("--yes", failed_backup.stderr)
        self.assertFalse(archive.exists())

        backed_up = json.loads(run_cli(
            UX, "backup", self.workspace, archive, "--mode", "minimum", "--yes"
        ).stdout)
        self.assertEqual(backed_up["continuity_class"], "minimum")
        self.assertTrue(archive.is_file())

        failed_restore = run_cli(UX, "restore", archive, destination, expect=2)
        self.assertIn("--yes", failed_restore.stderr)
        self.assertFalse(destination.exists())

        restored = json.loads(run_cli(UX, "restore", archive, destination, "--yes").stdout)
        self.assertEqual(restored["status"], "ok")
        restored_ids = {
            row["id"] for row in core.read_jsonl(destination / "memory" / "records.jsonl")
        }
        self.assertIn(memory_id, restored_ids)

    def test_tui_status_then_quit_is_read_only(self):
        self.create_memory("tui-read-only")
        before = tree_snapshot(self.workspace)
        answers = iter(["1", "", "q"])
        with mock.patch.object(builtins, "input", side_effect=lambda _prompt="": next(answers)):
            ux.run_tui(self.workspace)
        self.assertEqual(tree_snapshot(self.workspace), before)

    def test_workspace_status_reports_safe_defaults(self):
        status = ux.workspace_status(self.workspace)
        self.assertTrue(status["safe_defaults"]["read_only_inspection"])
        self.assertTrue(status["safe_defaults"]["cli_mutations_require_yes"])
        self.assertTrue(status["safe_defaults"]["review_and_application_are_separate"])


if __name__ == "__main__":
    unittest.main()
