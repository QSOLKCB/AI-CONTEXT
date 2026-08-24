import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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


class Phase8CodexHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli(CORE, "init", self.workspace)
        run_cli(CURATION, "init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def create_source_backed_memory(self):
        source = self.root / "source.json"
        source.write_text(json.dumps({"subject": "evidence-backed"}), encoding="utf-8")
        run_cli(CORE, "import", self.workspace, source, "--adapter", "generic-json")
        observations = ai_context.read_jsonl(self.workspace / "staging" / "observations.jsonl")
        self.assertTrue(observations)
        observation_id = observations[0]["id"]
        proposed = json.loads(
            run_cli(
                CURATION,
                "propose",
                self.workspace,
                "--observation",
                observation_id,
                "--record-type",
                "claim",
                "--sensitivity",
                "public",
                "--epistemic-state",
                "parsed",
                "--confidence",
                "1",
                "--semantic-key",
                "claim:evidence-backed",
            ).stdout
        )
        candidate_id = proposed["candidate_id"]
        run_cli(
            CURATION,
            "review",
            self.workspace,
            "--candidate",
            candidate_id,
            "--decision",
            "approve",
            "--actor-type",
            "human",
            "--actor-label",
            "phase8-codex",
        )
        applied = json.loads(
            run_cli(CURATION, "apply", self.workspace, "--candidate", candidate_id).stdout
        )
        return applied["memory_id"]

    def export(self, mode="minimum", name="restore.aicr"):
        archive = self.root / name
        run_cli(RESTORE, "export", self.workspace, archive, "--mode", mode)
        return archive

    def test_minimum_restore_keeps_provenance_and_application_authority(self):
        memory_id = self.create_source_backed_memory()
        before = routing.plan_bundle(
            self.workspace,
            profile_name="general",
            target_name="provider-default",
        )
        self.assertIn(memory_id, {row["id"] for row in before["records"]})

        archive = self.export("minimum")
        inspected = json.loads(run_cli(RESTORE, "inspect", archive).stdout)
        paths = {item["path"] for item in inspected["manifest"]["artifacts"]}
        self.assertIn("staging/observations.jsonl", paths)
        self.assertIn("curation/applications.jsonl", paths)
        self.assertIn("curation/candidates.jsonl", paths)
        self.assertIn("curation/decisions.jsonl", paths)
        self.assertIn("receipts/imports.jsonl", paths)

        restored = self.root / "restored"
        run_cli(RESTORE, "restore", archive, restored)
        run_cli(CORE, "validate", restored)
        after = routing.plan_bundle(
            restored,
            profile_name="general",
            target_name="provider-default",
        )
        self.assertEqual(
            [row["id"] for row in before["records"]],
            [row["id"] for row in after["records"]],
        )

    def test_full_manifest_must_contain_minimum_base_set(self):
        migration = restore._make_migration()
        artifact = {
            "path": "memory/records.jsonl",
            "class": "canonical_memory",
            "sha256": "0" * 64,
            "bytes": 0,
            "required": True,
        }
        manifest = {
            "protocol": restore.RESTORE_PROTOCOL,
            "schema_version": restore.RESTORE_VERSION,
            "workspace_protocol_version": ai_context.PROTOCOL_VERSION,
            "continuity_class": "full",
            "restore_claim": restore.RESTORE_CLAIM,
            "provider_memory_dependency": restore.PROVIDER_DEPENDENCY,
            "created_at": ai_context.utc_now(),
            "migration_manifest_sha256": restore.migration_manifest_sha256(migration),
            "artifacts": [artifact],
        }
        manifest["snapshot_id"] = restore.stable_id(
            "restore", restore._manifest_identity_core(manifest)
        )
        with self.assertRaisesRegex(restore.RestoreError, "full continuity set missing"):
            restore._validate_manifest(manifest, migration)

    def test_validate_rejects_semantically_privileged_enrichment(self):
        enrichment = self.root / "style.json"
        enrichment.write_text(
            json.dumps({
                "protocol": restore.ENRICHMENT_PROTOCOL,
                "schema_version": restore.RESTORE_VERSION,
                "class": "style_culture",
                "factual_authority": "none",
                "apply_scope": "presentation_only",
                "entries": [],
            }),
            encoding="utf-8",
        )
        archive = self.root / "enriched.aicr"
        run_cli(
            RESTORE,
            "export",
            self.workspace,
            archive,
            "--mode",
            "minimum",
            "--enrichment",
            enrichment,
        )

        def mutate(members):
            payload_name = "payload/enrichment/style-culture.json"
            bad = json.loads(members[payload_name])
            bad["factual_authority"] = "fact"
            bad["apply_scope"] = "canonical_memory"
            payload = ai_context.canonical_bytes(bad) + b"\n"
            members[payload_name] = payload
            manifest = json.loads(members["manifest.json"])
            for item in manifest["artifacts"]:
                if item["path"] == "enrichment/style-culture.json":
                    item["sha256"] = ai_context.sha256_bytes(payload)
                    item["bytes"] = len(payload)
            manifest["snapshot_id"] = restore.stable_id(
                "restore", restore._manifest_identity_core(manifest)
            )
            members["manifest.json"] = ai_context.canonical_bytes(manifest) + b"\n"
            return members

        rewrite_zip(archive, mutate)
        failed = run_cli(RESTORE, "validate", archive, expect=2)
        self.assertIn("zero factual authority", failed.stderr)

    def test_export_enforces_reader_member_limit(self):
        archive = self.root / "too-many.aicr"
        with mock.patch.object(restore.legacy, "MAX_ARCHIVE_MEMBERS", 3):
            with self.assertRaisesRegex(restore.RestoreError, "member-count"):
                restore.export_restore(self.workspace, archive, mode="minimum")
        self.assertFalse(archive.exists())

    def test_migration_extensions_do_not_change_snapshot_identity(self):
        archive = self.export("minimum")
        before = json.loads(run_cli(RESTORE, "inspect", archive).stdout)
        snapshot_id = before["manifest"]["snapshot_id"]

        def mutate(members):
            migration = json.loads(members["migration.json"])
            migration["extensions"] = {
                "future_tool_hint": "opaque",
                "nested": {"revision": 3},
            }
            members["migration.json"] = ai_context.canonical_bytes(migration) + b"\n"
            return members

        rewrite_zip(archive, mutate)
        validated = json.loads(run_cli(RESTORE, "validate", archive).stdout)
        self.assertEqual(validated["snapshot_id"], snapshot_id)

    def test_full_reexport_preserves_installed_enrichment(self):
        enrichment_dir = self.workspace / "enrichment"
        enrichment_dir.mkdir(exist_ok=True)
        enrichment = {
            "protocol": restore.ENRICHMENT_PROTOCOL,
            "schema_version": restore.RESTORE_VERSION,
            "class": "style_culture",
            "factual_authority": "none",
            "apply_scope": "presentation_only",
            "entries": [
                {"label": "voice", "value": "precise", "tags": ["style"]}
            ],
        }
        (enrichment_dir / "style-culture.json").write_text(
            json.dumps(enrichment, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        archive = self.export("full", name="full.aicr")
        inspected = json.loads(run_cli(RESTORE, "inspect", archive).stdout)
        paths = {item["path"] for item in inspected["manifest"]["artifacts"]}
        self.assertIn("enrichment/style-culture.json", paths)

        restored = self.root / "full-restored"
        run_cli(RESTORE, "restore", archive, restored)
        self.assertEqual(
            json.loads((restored / "enrichment" / "style-culture.json").read_text()),
            enrichment,
        )


if __name__ == "__main__":
    unittest.main()
