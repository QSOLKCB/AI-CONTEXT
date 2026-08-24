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


class Phase11CodexHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli(CORE, "init", self.workspace)
        run_cli(CURATION, "init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def propose(self, name, *, semantic_key=None):
        content = self.root / f"{name}.json"
        content.write_text(json.dumps({"subject": name}), encoding="utf-8")
        args = [
            "propose", self.workspace,
            "--content-file", content,
            "--record-type", "claim",
            "--sensitivity", "private",
            "--epistemic-state", "user_asserted",
            "--confidence", "1",
        ]
        if semantic_key:
            args += ["--semantic-key", semantic_key]
        return json.loads(run_cli(CURATION, *args).stdout)["candidate_id"]

    def approve(self, candidate):
        run_cli(
            CURATION, "review", self.workspace,
            "--candidate", candidate,
            "--decision", "approve",
            "--actor-type", "human",
            "--actor-label", "phase11-hardening-test",
        )

    def create_memory(self, name, *, semantic_key=None):
        candidate = self.propose(name, semantic_key=semantic_key)
        self.approve(candidate)
        return json.loads(
            run_cli(CURATION, "apply", self.workspace, "--candidate", candidate).stdout
        )["memory_id"]

    def test_import_result_exposes_new_observation_ids_without_granting_authority(self):
        source = self.root / "notes.md"
        source.write_text("# Project notes\nA staged observation only.\n", encoding="utf-8")

        result = ux.import_source(
            self.workspace,
            source,
            mode="core",
            adapter=None,
            plugin=None,
            yes=True,
        )

        ids = result.get("observation_ids")
        self.assertIsInstance(ids, list)
        self.assertTrue(ids)
        self.assertTrue(all(isinstance(value, str) and value.startswith("obs.sha256:") for value in ids))
        staged = {
            row["id"]
            for row in core.read_jsonl(self.workspace / "staging" / "observations.jsonl")
        }
        self.assertTrue(set(ids).issubset(staged))
        self.assertFalse((self.workspace / "memory" / "records.jsonl").exists())
        self.assertEqual(core.read_jsonl(self.workspace / "curation" / "candidates.jsonl"), [])

    def test_status_normalizes_approve_to_stable_approved_key(self):
        candidate = self.propose("approved-unapplied")
        self.approve(candidate)

        status = ux.workspace_status(self.workspace)

        self.assertEqual(status["candidates"]["approved"], 1)
        self.assertNotIn("approve", status["candidates"])
        self.assertEqual(ux.candidate_status(self.workspace, candidate), "approved")
        self.assertEqual(
            [row["candidate_id"] for row in ux.list_candidates(self.workspace, status="approved")],
            [candidate],
        )

    def test_tui_can_resume_and_apply_an_existing_approved_candidate(self):
        candidate = self.propose("resume-approved")
        self.approve(candidate)
        decisions_before = (self.workspace / "curation" / "decisions.jsonl").read_bytes()

        answers = iter(["1", "YES"])
        with mock.patch.object(builtins, "input", side_effect=lambda _prompt="": next(answers)):
            ux._tui_review(self.workspace)

        self.assertEqual(
            (self.workspace / "curation" / "decisions.jsonl").read_bytes(),
            decisions_before,
        )
        self.assertEqual(ux.candidate_status(self.workspace, candidate), "applied")
        self.assertTrue((self.workspace / "memory" / "records.jsonl").is_file())

    def test_blank_conflict_resolution_has_no_authority_and_requires_explicit_choice(self):
        semantic_key = "claim:phase11-explicit-conflict"
        self.create_memory("conflict-old", semantic_key=semantic_key)
        candidate = self.propose("conflict-new", semantic_key=semantic_key)

        answers = iter([
            "1",                  # candidate
            "approve",            # decision
            "",                   # blank must NOT default to coexist
            "coexist",            # explicit authority-bearing choice
            "explicit-note",      # notes
            "YES",                # record review
            "NO",                 # do not apply yet
        ])
        with mock.patch.object(builtins, "input", side_effect=lambda _prompt="": next(answers)):
            ux._tui_review(self.workspace)

        decisions = [
            row
            for row in core.read_jsonl(self.workspace / "curation" / "decisions.jsonl")
            if row.get("candidate_id") == candidate
        ]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["conflict_resolution"], "coexist")
        self.assertEqual(decisions[0]["notes"], "explicit-note")
        self.assertEqual(ux.candidate_status(self.workspace, candidate), "approved")

    @unittest.skipIf(os.name == "nt", "symlink governance-log test requires POSIX symlinks")
    def test_read_only_status_rejects_symlinked_governance_log(self):
        target = self.root / "foreign-decisions.jsonl"
        target.write_text("", encoding="utf-8")
        decisions = self.workspace / "curation" / "decisions.jsonl"
        if decisions.exists():
            decisions.unlink()
        decisions.symlink_to(target)

        with self.assertRaises(ux.UXError) as caught:
            ux.workspace_status(self.workspace)

        self.assertIn("must not be a symlink", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
