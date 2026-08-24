import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import routing  # noqa: E402

CORE = TOOLS / "ai_context.py"
CURATION = TOOLS / "curation.py"


def run_cli(script, *args, expect=0):
    import subprocess

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


class Phase6CodexHardeningTests(unittest.TestCase):
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
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def write_json(self, path, value):
        Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def create_memory(
        self,
        name,
        *,
        record_type="claim",
        content=None,
        sensitivity="private",
        tags=None,
        semantic_key=None,
    ):
        payload = content if content is not None else {"subject": name}
        content_path = self.root / f"{name}.json"
        self.write_json(content_path, payload)
        args = [
            "propose", self.workspace,
            "--content-file", content_path,
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
            "--actor-label", "phase6-codex-test",
        )
        result = json.loads(
            run_cli(CURATION, "apply", self.workspace, "--candidate", candidate).stdout
        )
        return result["memory_id"]

    def bundle(self, *, task=None, tags=None, target=None, expect=0, name="bundle.json"):
        output = self.root / name
        args = ["bundle", self.workspace, "--profile", "general", "--output", output]
        if task is not None:
            args += ["--task", task]
        if tags is not None:
            args += ["--tags", tags]
        if target is not None:
            args += ["--target", target]
        result = run_cli(CORE, *args, expect=expect)
        payload = None
        if expect == 0:
            payload = json.loads(output.read_text(encoding="utf-8"))
        return payload, result

    def test_dependency_relationship_records_are_not_directly_disclosed_by_default(self):
        root_id = self.create_memory("root", tags=["root"])
        dependency_id = self.create_memory("dependency")
        relationship_id = self.create_memory(
            "relationship",
            record_type="relationship",
            content={
                "relation": "requires",
                "from_memory_id": root_id,
                "to_memory_id": dependency_id,
            },
        )
        payload, _ = self.bundle(tags="root")
        ids = {record["id"] for record in payload["records"]}
        self.assertIn(root_id, ids)
        self.assertIn(dependency_id, ids)
        self.assertNotIn(relationship_id, ids)

    def test_disabled_task_selector_rejects_even_empty_workspace(self):
        profile_path = self.workspace / "profiles" / "general.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["task_selector"]["enabled"] = False
        self.write_json(profile_path, profile)
        _, failed = self.bundle(task="anything", expect=2)
        self.assertIn("disables task selection", failed.stderr)

    def test_duplicate_canonical_memory_ids_fail_closed_before_routing(self):
        memory_id = self.create_memory("duplicate")
        memory_path = self.workspace / "memory" / "records.jsonl"
        line = memory_path.read_text(encoding="utf-8").strip()
        memory_path.write_text(line + "\n" + line + "\n", encoding="utf-8")
        _, failed = self.bundle(expect=2)
        self.assertIn("duplicate canonical memory id", failed.stderr)
        self.assertIn(memory_id, failed.stderr)

    def test_provider_requires_a_valid_phase5_application_authority_chain(self):
        memory_id = self.create_memory(
            "provider-safe",
            sensitivity="public",
            semantic_key="claim:provider-safe",
        )
        applications = self.workspace / "curation" / "applications.jsonl"
        applications.write_text(
            json.dumps({"memory_id": memory_id}) + "\n",
            encoding="utf-8",
        )
        _, failed = self.bundle(target="provider-default", expect=2)
        self.assertTrue(
            "row missing id" in failed.stderr
            or "application" in failed.stderr.lower(),
            failed.stderr,
        )

    def test_explicit_application_semantic_key_suppresses_derived_fallback_key(self):
        root_id = self.create_memory("semantic-root", tags=["semantic-root"])
        self.create_memory(
            "semantic-target",
            content={"subject": "DerivedTarget"},
            semantic_key="claim:explicit:target",
        )
        self.create_memory(
            "semantic-rel",
            record_type="relationship",
            content={
                "relation": "requires",
                "from_memory_id": root_id,
                "to_semantic_key": "claim:subject:DerivedTarget",
            },
        )
        _, failed = self.bundle(tags="semantic-root", expect=2)
        self.assertIn("missing semantic endpoint", failed.stderr)
        self.assertIn("claim:subject:DerivedTarget", failed.stderr)

    def test_dependency_expansion_memoizes_converging_dag_nodes(self):
        records = {
            name: {
                "id": name,
                "record_type": "claim",
                "approval": "approved",
                "lifecycle": {"state": "active"},
                "sensitivity": "public",
                "tags": [],
                "source_refs": [],
                "epistemic_state": "user_asserted",
                "confidence": 1.0,
                "content": {"subject": name},
            }
            for name in ("root", "a", "b", "c", "d")
        }
        for rel in ("ra", "rb", "ac", "bc", "cd"):
            records[rel] = {
                "id": rel,
                "record_type": "relationship",
                "approval": "approved",
                "lifecycle": {"state": "active"},
                "sensitivity": "public",
                "tags": [],
                "source_refs": [],
                "epistemic_state": "user_asserted",
                "confidence": 1.0,
                "content": {"relation": "requires"},
            }
        graph = {
            "root": [
                {"target_id": "a", "relationship_id": "ra", "relation": "requires"},
                {"target_id": "b", "relationship_id": "rb", "relation": "requires"},
            ],
            "a": [{"target_id": "c", "relationship_id": "ac", "relation": "requires"}],
            "b": [{"target_id": "c", "relationship_id": "bc", "relation": "requires"}],
            "c": [{"target_id": "d", "relationship_id": "cd", "relation": "requires"}],
        }
        selected = {"root": records["root"]}
        diagnostics = {
            "root": {
                "memory_id": "root",
                "included_by": ["profile_baseline"],
                "tag_matches": [],
                "task_matches": [],
                "dependency_of": [],
                "dependency_depth": None,
            }
        }
        profile = {
            "dependency_policy": {
                "enabled": True,
                "max_depth": 8,
                "fail_on_cycle": True,
                "include_relationship_records": False,
            }
        }
        calls = []
        original = routing.exclusion_reason

        def allow(record, **kwargs):
            calls.append(record["id"])
            return None

        routing.exclusion_reason = allow
        try:
            routing._expand_dependencies(
                self.workspace,
                selected,
                diagnostics,
                records=records,
                graph=graph,
                profile=profile,
                target={},
                routing_policy={},
                application_ids=set(),
            )
        finally:
            routing.exclusion_reason = original

        self.assertEqual(calls.count("cd"), 1)
        self.assertEqual(set(selected), {"root", "a", "b", "c", "d"})
        self.assertEqual(diagnostics["c"]["dependency_of"], ["a", "b"])

    def test_content_path_schema_and_runtime_both_reject_single_wildcard(self):
        schema = json.loads(
            (ROOT / "spec" / "disclosure-exclusions.schema.json").read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema)
        self.assertTrue(list(validator.iter_errors({"content_paths": ["*"]})))
        with self.assertRaises(routing.RoutingError):
            routing.validate_hard_exclusions({"content_paths": ["*"]}, "test")

    def test_unicode_task_tokenization_routes_cjk_content(self):
        memory_id = self.create_memory(
            "unicode",
            content={"subject": "量子計算"},
        )
        payload, _ = self.bundle(task="量子計算")
        self.assertEqual([record["id"] for record in payload["records"]], [memory_id])
        self.assertEqual(payload["routing"]["task_terms"], ["量子計算"])


if __name__ == "__main__":
    unittest.main()
