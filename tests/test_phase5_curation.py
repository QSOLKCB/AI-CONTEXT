import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

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


class Phase5CurationTests(unittest.TestCase):
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

    def import_note(self, name, text):
        source = self.root / name
        source.write_text(text, encoding="utf-8")
        run_cli(CORE, "import", self.workspace, source)
        observations = self.read_jsonl("staging/observations.jsonl")
        return observations[-1]["id"]

    def propose(self, obs, semantic_key=None, record_type="claim"):
        args = ["propose", self.workspace, "--observation", obs, "--record-type", record_type]
        if semantic_key:
            args += ["--semantic-key", semantic_key]
        return json.loads(run_cli(CURATION, *args).stdout)["candidate_id"]

    def approve_and_apply(self, candidate, conflict_resolution="none"):
        args = [
            "review", self.workspace,
            "--candidate", candidate,
            "--decision", "approve",
            "--actor-type", "human",
            "--actor-label", "synthetic-reviewer",
            "--conflict-resolution", conflict_resolution,
        ]
        run_cli(CURATION, *args)
        return json.loads(run_cli(CURATION, "apply", self.workspace, "--candidate", candidate).stdout)["memory_id"]

    def validate(self):
        run_cli(CORE, "validate", self.workspace)
        return json.loads(run_cli(VALIDATE, self.workspace).stdout)

    def test_candidate_generator_cannot_self_promote(self):
        obs = self.import_note("note.txt", "Synthetic evidence only.\n")
        candidate = self.propose(obs)
        self.assertTrue(candidate.startswith("candidate.sha256:"))
        self.assertFalse((self.workspace / "memory" / "records.jsonl").exists())
        queue = json.loads(run_cli(CURATION, "queue", self.workspace).stdout)
        self.assertEqual(queue["count"], 1)
        self.assertEqual(queue["candidates"][0]["status"], "pending")
        failed = run_cli(CURATION, "apply", self.workspace, "--candidate", candidate, expect=2)
        self.assertIn("explicit approve decision", failed.stderr)

    def test_human_review_then_apply_creates_memory_and_application_receipt(self):
        obs = self.import_note("approved.txt", "Approved synthetic evidence.\n")
        candidate = self.propose(obs, semantic_key="claim:synthetic")
        memory_id = self.approve_and_apply(candidate)
        memories = self.read_jsonl("memory/records.jsonl")
        self.assertEqual([row["id"] for row in memories], [memory_id])
        self.assertEqual(memories[0]["approval"], "approved")
        applications = self.read_jsonl("curation/applications.jsonl")
        self.assertEqual(applications[0]["candidate_id"], candidate)
        self.assertEqual(applications[0]["actor"]["type"], "human")
        self.assertEqual(self.validate()["applications"], 1)

    def test_local_llm_is_advisory_only(self):
        obs = self.import_note("llm.txt", "Evidence for local LLM review.\n")
        candidate = self.propose(obs)
        request = self.root / "llm-request.json"
        run_cli(CURATION, "llm-request", self.workspace, "--candidate", candidate, "--output", request)
        response = self.root / "llm-response.json"
        response.write_text(json.dumps({
            "protocol": "AI-CONTEXT/CURATOR-RESPONSE",
            "schema_version": "0.1.0",
            "candidate_id": candidate,
            "recommendation": "approve",
            "rationale": "Synthetic advisory approval only.",
            "model": {"id": "synthetic-local-model", "runtime": "offline-test"}
        }), encoding="utf-8")
        result = json.loads(run_cli(CURATION, "llm-import", self.workspace, "--input", response).stdout)
        self.assertFalse(result["canonical_memory_changed"])
        self.assertFalse((self.workspace / "memory" / "records.jsonl").exists())
        failed = run_cli(CURATION, "apply", self.workspace, "--candidate", candidate, expect=2)
        self.assertIn("explicit approve decision", failed.stderr)
        self.assertEqual(len(self.read_jsonl("curation/llm-suggestions.jsonl")), 1)

    def test_conflict_requires_explicit_resolution_and_supersession_graph(self):
        first_obs = self.import_note("first.txt", "Alpha state is one.\n")
        first_candidate = self.propose(first_obs, semantic_key="project:alpha")
        first_memory = self.approve_and_apply(first_candidate)

        second_obs = self.import_note("second.txt", "Alpha state is two.\n")
        second_candidate = self.propose(second_obs, semantic_key="project:alpha")
        conflicts = json.loads(run_cli(CURATION, "conflicts", self.workspace, "--candidate", second_candidate).stdout)
        self.assertEqual(len(conflicts["conflicts"]), 1)
        failed = run_cli(
            CURATION, "review", self.workspace,
            "--candidate", second_candidate,
            "--decision", "approve",
            expect=2,
        )
        self.assertIn("explicit conflict resolution", failed.stderr)
        second_memory = self.approve_and_apply(second_candidate, conflict_resolution="supersede_existing")
        memories = {row["id"]: row for row in self.read_jsonl("memory/records.jsonl")}
        self.assertEqual(memories[first_memory]["lifecycle"]["state"], "superseded")
        self.assertEqual(memories[second_memory]["lifecycle"]["state"], "active")
        edges = self.read_jsonl("curation/supersession.jsonl")
        self.assertEqual(edges[0]["from_memory_id"], first_memory)
        self.assertEqual(edges[0]["to_memory_id"], second_memory)
        self.validate()

    def test_verification_and_confidence_update_are_receipted(self):
        obs = self.import_note("verify.txt", "Verification evidence.\n")
        candidate = self.propose(obs)
        memory = self.approve_and_apply(candidate)
        run_cli(
            CURATION, "verify", self.workspace,
            "--memory", memory,
            "--confidence", "0.95",
            "--evidence", obs,
            "--reason", "synthetic verification",
        )
        record = self.read_jsonl("memory/records.jsonl")[0]
        self.assertEqual(record["epistemic_state"], "verified")
        self.assertEqual(record["confidence"], 0.95)
        self.assertIsNotNone(record["last_verified"])
        mutations = self.read_jsonl("curation/mutations.jsonl")
        self.assertEqual(mutations[0]["kind"], "verification")
        self.assertEqual(mutations[0]["evidence_refs"], [obs])
        self.validate()

    def test_retention_policy_expires_records(self):
        run_cli(CURATION, "retention-policy", self.workspace, "--default-days", "0")
        obs = self.import_note("expiry.txt", "Short-lived evidence.\n")
        candidate = self.propose(obs)
        memory = self.approve_and_apply(candidate)
        run_cli(CURATION, "enforce-retention", self.workspace)
        record = {row["id"]: row for row in self.read_jsonl("memory/records.jsonl")}[memory]
        self.assertEqual(record["lifecycle"]["state"], "expired")
        bundle = self.root / "bundle.json"
        run_cli(CORE, "bundle", self.workspace, "--output", bundle)
        self.assertEqual(json.loads(bundle.read_text(encoding="utf-8"))["records"], [])
        self.validate()

    def test_tombstone_emits_receipt_and_disappears_from_bundles(self):
        obs = self.import_note("delete.txt", "Tombstone evidence.\n")
        candidate = self.propose(obs)
        memory = self.approve_and_apply(candidate)
        run_cli(CURATION, "tombstone", self.workspace, "--memory", memory, "--reason", "synthetic removal")
        record = self.read_jsonl("memory/records.jsonl")[0]
        self.assertEqual(record["lifecycle"]["state"], "tombstoned")
        receipt = self.read_jsonl("curation/tombstones.jsonl")[0]
        self.assertEqual(receipt["memory_id"], memory)
        bundle = self.root / "bundle.json"
        run_cli(CORE, "bundle", self.workspace, "--output", bundle)
        self.assertEqual(json.loads(bundle.read_text(encoding="utf-8"))["records"], [])
        self.validate()

    def test_why_remembered_explanation_reaches_candidate_and_evidence(self):
        obs = self.import_note("why.txt", "Why remembered evidence.\n")
        candidate = self.propose(obs)
        memory = self.approve_and_apply(candidate)
        text = run_cli(CURATION, "explain", self.workspace, "--memory", memory).stdout
        self.assertIn("Remembered through candidate", text)
        self.assertIn(candidate, text)
        self.assertIn(obs, text)
        structured = json.loads(run_cli(CURATION, "explain", self.workspace, "--memory", memory, "--format", "json").stdout)
        self.assertEqual(structured["applications"][0]["candidate_id"], candidate)
        self.assertEqual(structured["source_chain"][0]["observation"]["id"], obs)


class Phase5SchemaTests(unittest.TestCase):
    def load_schema(self, name):
        return json.loads((ROOT / "spec" / name).read_text(encoding="utf-8"))

    def test_phase5_schemas_are_valid_draft_2020_12(self):
        names = [
            "curation-policy.schema.json",
            "curation-candidate.schema.json",
            "curation-decision.schema.json",
            "curation-conflict.schema.json",
            "curation-mutation.schema.json",
            "curation-application.schema.json",
            "curation-supersession.schema.json",
            "tombstone-receipt.schema.json",
            "curator-response.schema.json",
        ]
        for name in names:
            with self.subTest(schema=name):
                schema = self.load_schema(name)
                Draft202012Validator.check_schema(schema)

    def test_candidate_schema_forbids_preapproved_generated_memory(self):
        schema = self.load_schema("curation-candidate.schema.json")
        proposed = schema["properties"]["proposed_memory"]
        self.assertEqual(proposed["properties"]["approval"]["const"], "pending")

    def test_decision_schema_forbids_llm_as_review_authority(self):
        schema = self.load_schema("curation-decision.schema.json")
        actor_type = schema["properties"]["actor"]["properties"]["type"]["enum"]
        self.assertEqual(set(actor_type), {"human", "policy"})

    def test_mutation_schema_cannot_rewrite_content(self):
        schema = self.load_schema("curation-mutation.schema.json")
        patch_properties = set(schema["properties"]["patch"]["properties"])
        self.assertNotIn("content", patch_properties)
        self.assertNotIn("sensitivity", patch_properties)
        self.assertNotIn("source_refs", patch_properties)


if __name__ == "__main__":
    unittest.main()
