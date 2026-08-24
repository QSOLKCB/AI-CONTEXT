import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "tools" / "ai_context.py"
CURATION = ROOT / "tools" / "curation.py"
ROUTING = ROOT / "tools" / "routing.py"
VALIDATE = ROOT / "tools" / "validate_routing.py"


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


class Phase6RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli(CORE, "init", self.workspace)
        run_cli(CURATION, "init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def read_json(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def write_json(self, path, value):
        Path(path).write_text(json.dumps(value), encoding="utf-8")

    def create_memory(
        self,
        name,
        *,
        record_type="claim",
        content=None,
        sensitivity="private",
        tags=None,
        semantic_key=None,
        epistemic_state="user_asserted",
        conflict_resolution="none",
    ):
        content_path = self.root / f"{name}.json"
        self.write_json(content_path, content or {"subject": name})
        args = [
            "propose", self.workspace,
            "--content-file", content_path,
            "--record-type", record_type,
            "--sensitivity", sensitivity,
            "--epistemic-state", epistemic_state,
            "--confidence", "1",
            "--tags", ",".join(tags or []),
        ]
        if semantic_key:
            args += ["--semantic-key", semantic_key]
        candidate = json.loads(run_cli(CURATION, *args).stdout)["candidate_id"]
        review = [
            "review", self.workspace,
            "--candidate", candidate,
            "--decision", "approve",
            "--actor-type", "human",
            "--actor-label", "phase6-test",
            "--conflict-resolution", conflict_resolution,
        ]
        run_cli(CURATION, *review)
        return json.loads(
            run_cli(CURATION, "apply", self.workspace, "--candidate", candidate).stdout
        )["memory_id"]

    def bundle(self, name="bundle.json", *, task=None, tags=None, target=None, profile="general", expect=0):
        path = self.root / name
        args = ["bundle", self.workspace, "--profile", profile, "--output", path]
        if task:
            args += ["--task", task]
        if tags:
            args += ["--tags", tags]
        if target:
            args += ["--target", target]
        result = run_cli(CORE, *args, expect=expect)
        return path, result

    def test_init_creates_phase6_profile_and_private_routing_policy(self):
        profile = self.read_json(self.workspace / "profiles" / "general.json")
        self.assertIn("task_selector", profile)
        self.assertIn("hard_exclusions", profile)
        self.assertIn("dependency_policy", profile)
        policy = self.read_json(self.workspace / "routing" / "policy.json")
        self.assertEqual(policy["default_target"], "local-default")
        self.assertEqual(policy["targets"]["provider-default"]["max_sensitivity"], "public")
        ignores = (self.workspace / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("routing/", ignores)

    def test_task_selector_and_diagnostics_choose_minimum_context(self):
        rust = self.create_memory(
            "rust", content={"subject": "Rust parser", "state": "needs deterministic parsing"}, tags=["coding"]
        )
        self.create_memory(
            "music", content={"subject": "Album mastering", "state": "mix pass"}, tags=["music"]
        )
        path, _ = self.bundle(task="debug rust parser")
        payload = self.read_json(path)
        self.assertEqual([record["id"] for record in payload["records"]], [rust])
        diagnostic = payload["diagnostics"][0]
        self.assertEqual(diagnostic["memory_id"], rust)
        self.assertIn("task_selector", diagnostic["included_by"])
        self.assertTrue({"rust", "parser"}.issubset(set(diagnostic["task_matches"])))
        self.assertEqual(payload["routing"]["target"], "local-default")

    def test_profile_declared_semantic_alias_expands_task_terms(self):
        rust = self.create_memory(
            "alias-rust", content={"subject": "Rust borrow checker"}, tags=["engineering"]
        )
        profile_path = self.workspace / "profiles" / "general.json"
        profile = self.read_json(profile_path)
        profile["task_selector"]["aliases"] = {"programming": ["rust", "python", "software"]}
        self.write_json(profile_path, profile)
        path, _ = self.bundle(task="programming")
        payload = self.read_json(path)
        self.assertEqual([record["id"] for record in payload["records"]], [rust])
        self.assertIn("rust", payload["routing"]["task_terms"])

    def test_tag_selector_preserves_existing_behavior(self):
        coding = self.create_memory("coding", content={"subject": "compiler"}, tags=["coding"])
        self.create_memory("other", content={"subject": "garden"}, tags=["home"])
        path, _ = self.bundle(tags="coding")
        self.assertEqual([record["id"] for record in self.read_json(path)["records"]], [coding])

    def test_provider_and_local_targets_have_different_default_disclosure(self):
        public = self.create_memory("public", content={"subject": "public datum"}, sensitivity="public")
        private = self.create_memory("private", content={"subject": "private datum"}, sensitivity="private")
        local_path, _ = self.bundle(name="local.json", target="local-default")
        provider_path, _ = self.bundle(name="provider.json", target="provider-default")
        self.assertEqual({record["id"] for record in self.read_json(local_path)["records"]}, {public, private})
        self.assertEqual([record["id"] for record in self.read_json(provider_path)["records"]], [public])
        self.assertEqual(self.read_json(provider_path)["routing"]["target_kind"], "provider")

    def test_hard_exclusions_block_content_paths_beyond_tags_and_sensitivity(self):
        self.create_memory(
            "credential-shaped",
            content={"subject": "account", "credentials": {"hint": "do not route"}},
            sensitivity="private",
        )
        safe = self.create_memory("safe", content={"subject": "ordinary context"}, sensitivity="private")
        profile_path = self.workspace / "profiles" / "general.json"
        profile = self.read_json(profile_path)
        profile["hard_exclusions"]["content_paths"] = ["credentials"]
        self.write_json(profile_path, profile)
        path, _ = self.bundle()
        self.assertEqual([record["id"] for record in self.read_json(path)["records"]], [safe])

    def test_exact_dependency_expands_without_bypassing_positive_task_selector(self):
        source = self.create_memory("source", content={"subject": "Alpha deployment"}, sensitivity="public")
        dependency = self.create_memory("dependency", record_type="instruction", content={"subject": "Rollback procedure"}, sensitivity="public")
        self.create_memory(
            "relationship",
            record_type="relationship",
            content={
                "relation": "requires",
                "from_memory_id": source,
                "to_memory_id": dependency,
            },
            sensitivity="public",
        )
        path, _ = self.bundle(task="alpha deployment")
        payload = self.read_json(path)
        self.assertEqual({record["id"] for record in payload["records"]}, {source, dependency})
        dep_diag = next(item for item in payload["diagnostics"] if item["memory_id"] == dependency)
        self.assertIn("dependency_expansion", dep_diag["included_by"])
        self.assertEqual(dep_diag["dependency_of"], [source])
        self.assertEqual(dep_diag["dependency_depth"], 1)

    def test_required_dependency_cannot_bypass_provider_sensitivity_ceiling(self):
        source = self.create_memory("public-source", content={"subject": "Public launch"}, sensitivity="public")
        dependency = self.create_memory("private-dep", content={"subject": "Private prerequisite"}, sensitivity="private")
        self.create_memory(
            "private-edge",
            record_type="relationship",
            content={"relation": "depends_on", "from_memory_id": source, "to_memory_id": dependency},
            sensitivity="public",
        )
        _, failed = self.bundle(task="public launch", target="provider-default", expect=2)
        self.assertIn("required dependency", failed.stderr)
        self.assertIn("exceeds effective ceiling public", failed.stderr)

    def test_semantic_dependency_endpoint_fails_closed_when_ambiguous(self):
        first = self.create_memory(
            "amb-one",
            content={"subject": "Shared prerequisite", "value": "one"},
            sensitivity="public",
            semantic_key="claim:shared-prerequisite",
        )
        second = self.create_memory(
            "amb-two",
            content={"subject": "Shared prerequisite", "value": "two"},
            sensitivity="public",
            semantic_key="claim:shared-prerequisite",
            conflict_resolution="coexist",
        )
        self.assertNotEqual(first, second)
        source = self.create_memory("amb-source", content={"subject": "Ambiguous route"}, sensitivity="public")
        self.create_memory(
            "amb-edge",
            record_type="relationship",
            content={
                "relation": "depends_on",
                "from_memory_id": source,
                "to_semantic_key": "claim:shared-prerequisite",
            },
            sensitivity="public",
        )
        _, failed = self.bundle(task="ambiguous route", expect=2)
        self.assertIn("ambiguous semantic endpoint", failed.stderr)

    def test_bundle_is_deterministic_and_validator_detects_policy_drift(self):
        self.create_memory("stable", content={"subject": "Stable route"}, sensitivity="public")
        first, _ = self.bundle(name="first.json", task="stable route")
        second, _ = self.bundle(name="second.json", task="stable route")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        run_cli(VALIDATE, self.workspace, "--bundle", first)

        policy_path = self.workspace / "routing" / "policy.json"
        policy = self.read_json(policy_path)
        policy["targets"]["local-default"]["min_confidence"] = 0.5
        self.write_json(policy_path, policy)
        failed = run_cli(VALIDATE, self.workspace, "--bundle", first, expect=2)
        self.assertIn("stale or does not match", failed.stderr)


class Phase6SchemaTests(unittest.TestCase):
    def test_phase6_schemas_are_draft_2020_12_and_resolve(self):
        names = [
            "memory-record.schema.json",
            "routing-diagnostic.schema.json",
            "disclosure-exclusions.schema.json",
            "profile.schema.json",
            "routing-policy.schema.json",
            "bundle.schema.json",
        ]
        schemas = {
            name: json.loads((ROOT / "spec" / name).read_text(encoding="utf-8"))
            for name in names
        }
        registry = Registry()
        for schema in schemas.values():
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        for name, schema in schemas.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema, registry=registry)


if __name__ == "__main__":
    unittest.main()
