import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import ai_context  # noqa: E402
import restore  # noqa: E402
import routing  # noqa: E402

CORE = TOOLS / "ai_context.py"
CURATION = TOOLS / "curation.py"
RESTORE = TOOLS / "restore.py"


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


def rewrite_zip(path: Path, transform):
    with zipfile.ZipFile(path, "r") as src:
        members = {info.filename: src.read(info) for info in src.infolist()}
    members = transform(members)
    temp = path.with_suffix(".tmp")
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_STORED) as dst:
        for name, data in sorted(members.items()):
            dst.writestr(name, data)
    os.replace(temp, path)


class Phase8RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli(CORE, "init", self.workspace)
        run_cli(CURATION, "init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def create_memory(self, name, *, sensitivity="private", semantic_key=None):
        content_file = self.root / f"{name}.json"
        content_file.write_text(json.dumps({"subject": name}), encoding="utf-8")
        args = [
            "propose", self.workspace,
            "--content-file", content_file,
            "--record-type", "claim",
            "--sensitivity", sensitivity,
            "--epistemic-state", "user_asserted",
            "--confidence", "1",
        ]
        if semantic_key:
            args += ["--semantic-key", semantic_key]
        candidate = json.loads(run_cli(CURATION, *args).stdout)["candidate_id"]
        run_cli(
            CURATION, "review", self.workspace,
            "--candidate", candidate,
            "--decision", "approve",
            "--actor-type", "human",
            "--actor-label", "phase8-test",
        )
        result = json.loads(
            run_cli(CURATION, "apply", self.workspace, "--candidate", candidate).stdout
        )
        return result["memory_id"]

    def export(self, mode="minimum", enrichment=None, name="restore.aicr"):
        archive = self.root / name
        args = ["export", self.workspace, archive, "--mode", mode]
        if enrichment:
            args += ["--enrichment", enrichment]
        payload = json.loads(run_cli(RESTORE, *args).stdout)
        return archive, payload

    def test_minimum_cold_start_restore_without_provider_memory(self):
        memory_id = self.create_memory("portable-memory")
        before = routing.plan_bundle(
            self.workspace,
            profile_name="general",
            target_name="local-default",
        )
        archive, exported = self.export("minimum")
        restored = self.root / "cold-start"
        result = json.loads(run_cli(RESTORE, "restore", archive, restored).stdout)
        after = routing.plan_bundle(
            restored,
            profile_name="general",
            target_name="local-default",
        )
        self.assertEqual(exported["continuity_class"], "minimum")
        self.assertEqual(result["provider_memory_dependency"], "none")
        self.assertEqual(result["restore_claim"], "context-continuity-not-model-identity")
        self.assertEqual([r["id"] for r in before["records"]], [r["id"] for r in after["records"]])
        self.assertIn(memory_id, {r["id"] for r in after["records"]})
        self.assertEqual(list((restored / "vault" / "raw").iterdir()), [])
        self.assertFalse((restored / "bundles" / "provider-memory.json").exists())

    def test_full_working_set_preserves_curation_history(self):
        memory_id = self.create_memory("full-state", semantic_key="claim:full-state")
        applications_before = (self.workspace / "curation" / "applications.jsonl").read_bytes()
        archive, _ = self.export("full")
        restored = self.root / "full-restored"
        result = json.loads(run_cli(RESTORE, "restore", archive, restored).stdout)
        self.assertEqual(result["continuity_class"], "full")
        self.assertEqual(
            (restored / "curation" / "applications.jsonl").read_bytes(),
            applications_before,
        )
        self.assertIn(memory_id, {row["id"] for row in ai_context.read_jsonl(restored / "memory" / "records.jsonl")})

    def test_style_culture_enrichment_has_no_factual_authority(self):
        memory_id = self.create_memory("fact-boundary")
        memory_before = (self.workspace / "memory" / "records.jsonl").read_bytes()
        enrichment = self.root / "style.json"
        enrichment.write_text(
            json.dumps({
                "protocol": "AI-CONTEXT/STYLE-CULTURE-ENRICHMENT",
                "schema_version": "0.1.0",
                "class": "style_culture",
                "factual_authority": "none",
                "apply_scope": "presentation_only",
                "entries": [
                    {"label": "voice", "value": "dry technical humour", "tags": ["style"]}
                ],
            }),
            encoding="utf-8",
        )
        archive, _ = self.export("minimum", enrichment=enrichment)
        restored = self.root / "enriched"
        run_cli(RESTORE, "restore", archive, restored)
        self.assertEqual((restored / "memory" / "records.jsonl").read_bytes(), memory_before)
        restored_enrichment = json.loads((restored / "enrichment" / "style-culture.json").read_text())
        self.assertEqual(restored_enrichment["factual_authority"], "none")
        self.assertEqual(restored_enrichment["apply_scope"], "presentation_only")
        self.assertIn(memory_id, {row["id"] for row in ai_context.read_jsonl(restored / "memory" / "records.jsonl")})

    def test_unknown_restore_major_is_rejected(self):
        self.create_memory("major")
        archive, _ = self.export("minimum")

        def mutate(members):
            manifest = json.loads(members["manifest.json"])
            manifest["schema_version"] = "1.0.0"
            members["manifest.json"] = json.dumps(manifest).encode()
            return members

        rewrite_zip(archive, mutate)
        failed = run_cli(RESTORE, "validate", archive, expect=2)
        self.assertIn("unknown restore schema major version", failed.stderr)

    def test_unknown_workspace_major_is_rejected(self):
        migration = restore._make_migration()
        migration["source_workspace_version"] = "1.0.0"
        with self.assertRaises(restore.RestoreError):
            restore._validate_migration(migration)

    def test_additive_extensions_are_non_authoritative_and_compatible(self):
        self.create_memory("extensions")
        archive, exported = self.export("minimum")

        def mutate(members):
            manifest = json.loads(members["manifest.json"])
            manifest["extensions"] = {
                "future_ui_hint": "harmless",
                "nested": {"version": 2},
            }
            members["manifest.json"] = json.dumps(manifest).encode()
            return members

        rewrite_zip(archive, mutate)
        validated = json.loads(run_cli(RESTORE, "validate", archive).stdout)
        self.assertEqual(validated["snapshot_id"], exported["snapshot_id"])
        restored = self.root / "extensions-restored"
        run_cli(RESTORE, "restore", archive, restored)
        self.assertTrue((restored / "memory" / "records.jsonl").exists())

    def test_corrupt_restore_leaves_no_partial_destination(self):
        self.create_memory("atomic")
        archive, _ = self.export("minimum")

        def mutate(members):
            members["payload/memory/records.jsonl"] += b"corruption"
            return members

        rewrite_zip(archive, mutate)
        destination = self.root / "must-not-exist"
        failed = run_cli(RESTORE, "restore", archive, destination, expect=2)
        self.assertIn("artifact", failed.stderr)
        self.assertFalse(destination.exists())

    def test_archive_traversal_member_is_rejected(self):
        self.create_memory("traversal")
        archive, _ = self.export("minimum")

        def mutate(members):
            members["payload/../evil.txt"] = b"evil"
            return members

        rewrite_zip(archive, mutate)
        failed = run_cli(RESTORE, "validate", archive, expect=2)
        self.assertIn("unsafe archive path", failed.stderr)

    def test_cross_provider_restored_context_is_provider_neutral(self):
        memory_id = self.create_memory("cross-provider", sensitivity="public")
        policy_path = self.workspace / "routing" / "policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        template = {
            "kind": "provider",
            "enabled": True,
            "description": "Synthetic provider target for restore conformance.",
            "max_sensitivity": "public",
            "min_confidence": 0.0,
            "allowed_record_types": [],
            "denied_record_types": [],
            "denied_tags": [],
            "denied_epistemic_states": [],
            "require_verified_record_types": [],
            "require_curation_application": False,
            "hard_exclusions": {
                "record_ids": [],
                "record_types": [],
                "epistemic_states": [],
                "source_refs": [],
                "content_paths": [],
            },
        }
        policy["targets"]["provider-alpha"] = dict(template)
        policy["targets"]["provider-beta"] = dict(template)
        policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        archive, _ = self.export("full")
        restored = self.root / "provider-neutral"
        run_cli(RESTORE, "restore", archive, restored)
        alpha = routing.plan_bundle(restored, profile_name="general", target_name="provider-alpha")
        beta = routing.plan_bundle(restored, profile_name="general", target_name="provider-beta")
        alpha_ids = [record["id"] for record in alpha["records"]]
        beta_ids = [record["id"] for record in beta["records"]]
        self.assertEqual(alpha_ids, beta_ids)
        self.assertIn(memory_id, alpha_ids)
        inspected = json.loads(run_cli(RESTORE, "inspect", archive).stdout)
        self.assertEqual(inspected["manifest"]["provider_memory_dependency"], "none")


class Phase8SchemaTests(unittest.TestCase):
    SCHEMAS = [
        "restore-manifest.schema.json",
        "migration-manifest.schema.json",
        "style-culture-enrichment.schema.json",
    ]

    def test_phase8_schemas_are_valid_draft_2020_12(self):
        for name in self.SCHEMAS:
            with self.subTest(schema=name):
                schema = json.loads((ROOT / "spec" / name).read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)

    def test_enrichment_schema_forbids_factual_authority(self):
        schema = json.loads((ROOT / "spec" / "style-culture-enrichment.schema.json").read_text())
        invalid = {
            "protocol": "AI-CONTEXT/STYLE-CULTURE-ENRICHMENT",
            "schema_version": "0.1.0",
            "class": "style_culture",
            "factual_authority": "fact",
            "apply_scope": "presentation_only",
            "entries": [],
        }
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))


if __name__ == "__main__":
    unittest.main()
