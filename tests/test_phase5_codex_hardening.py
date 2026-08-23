import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "tools" / "ai_context.py"
CURATION = ROOT / "tools" / "curation.py"
VALIDATE = ROOT / "tools" / "validate_curation.py"


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


class Phase5CodexHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli(CORE, "init", self.workspace)
        run_cli(CURATION, "init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def read_jsonl(self, rel):
        path = self.workspace / rel
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def import_obs(self, name, text):
        source = self.root / name
        source.write_text(text, encoding="utf-8")
        run_cli(CORE, "import", self.workspace, source)
        return self.read_jsonl("staging/observations.jsonl")[-1]["id"]

    def propose(self, obs, key, *, sensitivity="private"):
        result = run_cli(
            CURATION, "propose", self.workspace,
            "--observation", obs,
            "--record-type", "claim",
            "--semantic-key", key,
            "--sensitivity", sensitivity,
        )
        return json.loads(result.stdout)["candidate_id"]

    def review(self, candidate, *, resolution="none", supersede=None):
        args = [
            "review", self.workspace,
            "--candidate", candidate,
            "--decision", "approve",
            "--actor-type", "human",
            "--actor-label", "reviewer",
            "--conflict-resolution", resolution,
        ]
        if supersede:
            args += ["--supersede", supersede]
        return run_cli(CURATION, *args)

    def apply(self, candidate, expect=0):
        return run_cli(CURATION, "apply", self.workspace, "--candidate", candidate, expect=expect)

    def test_public_legacy_promote_is_disabled_and_curation_is_ignored(self):
        candidate = self.root / "legacy.json"
        candidate.write_text(json.dumps({
            "record_type": "preference",
            "content": {"editor": "vim"},
            "approval": "approved",
            "tags": [],
        }), encoding="utf-8")
        failed = run_cli(CORE, "promote", self.workspace, "--input", candidate, expect=2)
        self.assertIn("legacy promote is disabled", failed.stderr)
        self.assertFalse((self.workspace / "memory" / "records.jsonl").exists())
        ignores = (self.workspace / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("curation/", ignores)

    def test_invalid_supersession_target_leaves_no_partial_application(self):
        obs = self.import_obs("atomic.txt", "Atomic candidate.\n")
        candidate = self.propose(obs, "claim:atomic")
        self.review(candidate, supersede="memory.missing")
        failed = self.apply(candidate, expect=2)
        self.assertIn("superseded memory does not exist", failed.stderr)
        self.assertEqual(self.read_jsonl("memory/records.jsonl"), [])
        self.assertEqual(self.read_jsonl("curation/applications.jsonl"), [])
        self.assertEqual(self.read_jsonl("curation/mutations.jsonl"), [])
        self.assertEqual(self.read_jsonl("curation/supersession.jsonl"), [])

    def test_new_conflict_after_coexist_approval_requires_rereview(self):
        first_obs = self.import_obs("first.txt", "First state.\n")
        first = self.propose(first_obs, "claim:late-conflict")
        self.review(first, resolution="coexist")

        second_obs = self.import_obs("second.txt", "Second state.\n")
        second = self.propose(second_obs, "claim:late-conflict")
        self.review(second)
        self.apply(second)

        failed = self.apply(first, expect=2)
        self.assertIn("another review decision", failed.stderr)
        self.assertEqual(len(self.read_jsonl("memory/records.jsonl")), 1)

    def test_self_supersession_is_rejected_without_mutation(self):
        obs = self.import_obs("self.txt", "Self supersession.\n")
        candidate = self.propose(obs, "claim:self")
        self.review(candidate)
        memory = json.loads(self.apply(candidate).stdout)["memory_id"]
        failed = run_cli(
            CURATION, "supersede", self.workspace,
            "--old", memory, "--new", memory,
            "--reason", "must fail",
            expect=2,
        )
        self.assertIn("cannot supersede itself", failed.stderr)
        record = self.read_jsonl("memory/records.jsonl")[0]
        self.assertEqual(record["lifecycle"]["state"], "active")
        self.assertEqual(self.read_jsonl("curation/supersession.jsonl"), [])

    def test_same_content_lower_sensitivity_is_a_conflict(self):
        obs = self.import_obs("same.txt", "Same classified content.\n")
        restricted = self.propose(obs, "claim:sensitivity", sensitivity="restricted")
        self.review(restricted)
        self.apply(restricted)

        lower = self.propose(obs, "claim:sensitivity", sensitivity="private")
        result = json.loads(run_cli(CURATION, "conflicts", self.workspace, "--candidate", lower).stdout)
        self.assertEqual(len(result["conflicts"]), 1)
        failed = self.review(lower)
        self.assertEqual(failed.returncode, 2)

    def test_invalid_non_null_expiry_is_rejected_before_candidate_creation(self):
        obs = self.import_obs("llm.txt", "LLM evidence.\n")
        parent = self.propose(obs, "claim:llm")
        response = self.root / "response.json"
        response.write_text(json.dumps({
            "protocol": "AI-CONTEXT/CURATOR-RESPONSE",
            "schema_version": "0.1.0",
            "candidate_id": parent,
            "recommendation": "revise",
            "rationale": "synthetic",
            "model": {"id": "local-test", "runtime": "offline"},
            "semantic_key": "claim:llm",
            "proposed_memory": {
                "record_type": "claim",
                "content": {"text": "LLM evidence."},
                "sensitivity": "private",
                "epistemic_state": "inferred",
                "confidence": 0.5,
                "approval": "pending",
                "source_refs": [obs],
                "tags": [],
                "lifecycle": {"state": "active", "expires_at": "not-a-date", "supersedes": None},
                "notes": ""
            }
        }), encoding="utf-8")
        failed = run_cli(
            CURATION, "llm-import", self.workspace,
            "--input", response,
            "--create-candidate",
            expect=2,
        )
        self.assertIn("invalid timestamp", failed.stderr)
        self.assertEqual(len(self.read_jsonl("curation/candidates.jsonl")), 1)

    def test_validator_rejects_future_version_for_every_record_family(self):
        families = {
            "conflicts.jsonl": "AI-CONTEXT/CONFLICT",
            "applications.jsonl": "AI-CONTEXT/APPLICATION",
            "mutations.jsonl": "AI-CONTEXT/CURATION-MUTATION",
            "supersession.jsonl": "AI-CONTEXT/SUPERSESSION",
            "tombstones.jsonl": "AI-CONTEXT/TOMBSTONE",
            "llm-suggestions.jsonl": "AI-CONTEXT/LLM-SUGGESTION",
        }
        for filename, protocol in families.items():
            with self.subTest(filename=filename):
                path = self.workspace / "curation" / filename
                path.write_text(json.dumps({
                    "id": "future.fixture",
                    "protocol": protocol,
                    "schema_version": "9.0.0"
                }) + "\n", encoding="utf-8")
                failed = run_cli(VALIDATE, self.workspace, expect=2)
                self.assertIn("unsupported", failed.stderr)
                path.unlink()


if __name__ == "__main__":
    unittest.main()
