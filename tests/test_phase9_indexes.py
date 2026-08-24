import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import indexes  # noqa: E402
import routing  # noqa: E402

CORE = TOOLS / "ai_context.py"
CURATION = TOOLS / "curation.py"
INDEXES = TOOLS / "indexes.py"


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


class Phase9IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli(CORE, "init", self.workspace)
        run_cli(CURATION, "init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def create_memory(
        self,
        name,
        *,
        content=None,
        record_type="claim",
        sensitivity="private",
        tags=None,
        semantic_key=None,
    ):
        path = self.root / f"{name}.json"
        path.write_text(
            json.dumps(content if content is not None else {"subject": name}),
            encoding="utf-8",
        )
        args = [
            "propose", self.workspace,
            "--content-file", path,
            "--record-type", record_type,
            "--sensitivity", sensitivity,
            "--epistemic-state", "user_asserted",
            "--confidence", "1",
            "--tags", ",".join(tags or []),
        ]
        if semantic_key:
            args += ["--semantic-key", semantic_key]
        candidate = json.loads(run_cli(CURATION, *args).stdout)["candidate_id"]
        run_cli(
            CURATION, "review", self.workspace,
            "--candidate", candidate,
            "--decision", "approve",
            "--actor-type", "human",
            "--actor-label", "phase9-test",
        )
        result = json.loads(
            run_cli(CURATION, "apply", self.workspace, "--candidate", candidate).stdout
        )
        return result["memory_id"]

    def test_build_is_deterministic_and_private_by_default(self):
        self.create_memory(
            "alpha",
            content={"subject": "Alpha parser", "language": "Rust"},
            tags=["coding"],
        )
        first = json.loads(run_cli(INDEXES, "build", self.workspace).stdout)
        before = {
            path.name: path.read_bytes()
            for path in sorted((self.workspace / "indexes").iterdir())
            if path.is_file()
        }
        second = json.loads(run_cli(INDEXES, "build", self.workspace).stdout)
        after = {
            path.name: path.read_bytes()
            for path in sorted((self.workspace / "indexes").iterdir())
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(first["manifest_id"], second["manifest_id"])
        self.assertIn("indexes/", (self.workspace / ".gitignore").read_text(encoding="utf-8"))
        validated = json.loads(run_cli(INDEXES, "validate", self.workspace).stdout)
        self.assertEqual(validated["status"], "ok")

    def test_search_hit_is_candidate_only_and_cannot_bypass_provider_policy(self):
        memory_id = self.create_memory(
            "private-alpha",
            content={"subject": "Private Alpha launch plan"},
            sensitivity="private",
        )
        run_cli(INDEXES, "build", self.workspace)
        result = json.loads(run_cli(INDEXES, "search", self.workspace, "alpha launch").stdout)
        self.assertIn(memory_id, {row["memory_id"] for row in result["results"]})
        self.assertEqual(result["authority"], "candidate-retrieval-only")
        self.assertTrue(result["disclosure_requires_routing"])

        provider = routing.plan_bundle(
            self.workspace,
            profile_name="general",
            target_name="provider-default",
        )
        self.assertNotIn(memory_id, {row["id"] for row in provider["records"]})

    def test_any_canonical_mutation_stales_existing_indexes(self):
        memory_id = self.create_memory("mutable", content={"subject": "Mutable index source"})
        run_cli(INDEXES, "build", self.workspace)
        run_cli(
            CURATION, "confidence", self.workspace,
            "--memory", memory_id,
            "--confidence", "0.5",
            "--reason", "synthetic Phase 9 stale-index test",
        )
        failed = run_cli(INDEXES, "validate", self.workspace, expect=2)
        self.assertIn("stale", failed.stderr.lower())
        failed_search = run_cli(INDEXES, "search", self.workspace, "mutable", expect=2)
        self.assertIn("stale", failed_search.stderr.lower())

    def test_rebuild_after_tombstone_removes_record_from_every_retrieval_projection(self):
        memory_id = self.create_memory(
            "obsolete",
            content={"subject": "Obsolete quantum index signal"},
        )
        run_cli(INDEXES, "build", self.workspace)
        before = json.loads(run_cli(INDEXES, "search", self.workspace, "obsolete quantum").stdout)
        self.assertIn(memory_id, {row["memory_id"] for row in before["results"]})

        run_cli(
            CURATION, "tombstone", self.workspace,
            "--memory", memory_id,
            "--reason", "synthetic Phase 9 tombstone",
        )
        failed = run_cli(INDEXES, "validate", self.workspace, expect=2)
        self.assertIn("stale", failed.stderr.lower())

        rebuilt = json.loads(run_cli(INDEXES, "build", self.workspace).stdout)
        self.assertEqual(rebuilt["retrieval_records"], 0)
        run_cli(INDEXES, "validate", self.workspace)
        after = json.loads(run_cli(INDEXES, "search", self.workspace, "obsolete quantum").stdout)
        self.assertEqual(after["results"], [])

        vector = json.loads((self.workspace / "indexes" / "vector.json").read_text())
        graph = json.loads((self.workspace / "indexes" / "graph.json").read_text())
        search = json.loads((self.workspace / "indexes" / "search.json").read_text())
        self.assertNotIn(memory_id, {row["memory_id"] for row in vector["records"]})
        self.assertNotIn(f"memory:{memory_id}", {row["id"] for row in graph["nodes"]})
        self.assertNotIn(memory_id, {row["memory_id"] for row in search["records"]})

    def test_graph_projects_explicit_relationship_without_resolving_authority(self):
        left = self.create_memory("left", content={"subject": "left"})
        right = self.create_memory("right", content={"subject": "right"})
        relationship = self.create_memory(
            "requires-link",
            record_type="relationship",
            content={
                "relation": "requires",
                "from_memory_id": left,
                "to_memory_id": right,
            },
        )
        run_cli(INDEXES, "build", self.workspace)
        graph = json.loads((self.workspace / "indexes" / "graph.json").read_text())
        matches = [
            edge for edge in graph["edges"]
            if edge["relation"] == "requires" and edge["relationship_memory_id"] == relationship
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["from"], f"memory:{left}")
        self.assertEqual(matches[0]["to"], f"memory:{right}")
        self.assertEqual(graph["authority"], "derived-retrieval-only")

    def test_source_fingerprint_changes_when_canonical_store_changes(self):
        memory_id = self.create_memory("fingerprint")
        run_cli(INDEXES, "build", self.workspace)
        first = json.loads((self.workspace / "indexes" / "manifest.json").read_text())
        run_cli(
            CURATION, "tombstone", self.workspace,
            "--memory", memory_id,
            "--reason", "fingerprint change",
        )
        run_cli(INDEXES, "build", self.workspace)
        second = json.loads((self.workspace / "indexes" / "manifest.json").read_text())
        self.assertNotEqual(first["source_fingerprint"]["id"], second["source_fingerprint"]["id"])
        self.assertNotEqual(first["id"], second["id"])


class Phase9SchemaTests(unittest.TestCase):
    SCHEMAS = [
        "derived-source-fingerprint.schema.json",
        "vector-index.schema.json",
        "graph-index.schema.json",
        "search-cache.schema.json",
        "index-manifest.schema.json",
    ]

    def test_phase9_schemas_are_valid_draft_2020_12(self):
        for name in self.SCHEMAS:
            with self.subTest(schema=name):
                schema = json.loads((ROOT / "spec" / name).read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)

    def test_generated_projection_instances_conform_to_published_schemas(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            workspace = root / "workspace"
            run_cli(CORE, "init", workspace)
            run_cli(CURATION, "init", workspace)
            run_cli(INDEXES, "build", workspace)

            loaded = {
                name: json.loads((ROOT / "spec" / name).read_text(encoding="utf-8"))
                for name in self.SCHEMAS
            }
            registry = Registry()
            for schema in loaded.values():
                registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))

            pairs = [
                ("vector-index.schema.json", workspace / "indexes" / "vector.json"),
                ("graph-index.schema.json", workspace / "indexes" / "graph.json"),
                ("search-cache.schema.json", workspace / "indexes" / "search.json"),
                ("index-manifest.schema.json", workspace / "indexes" / "manifest.json"),
            ]
            for schema_name, artifact_path in pairs:
                with self.subTest(schema=schema_name):
                    validator = Draft202012Validator(loaded[schema_name], registry=registry)
                    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                    self.assertEqual(list(validator.iter_errors(artifact)), [])


if __name__ == "__main__":
    unittest.main()
