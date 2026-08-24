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

import interop  # noqa: E402

CORE = TOOLS / "ai_context.py"
READONLY_VALIDATOR = TOOLS / "validate_bundle_readonly.py"
FIXTURE = ROOT / "fixtures" / "interoperability" / "conformance-v0.1.0.json"


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


class Phase10CodexHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli(CORE, "init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def make_bundle(self):
        bundle = self.root / "bundle.json"
        run_cli(
            CORE,
            "bundle",
            self.workspace,
            "--profile",
            "general",
            "--target",
            "local-default",
            "--output",
            bundle,
        )
        return bundle

    def test_duplicate_json_members_are_rejected_before_bundle_signing(self):
        bundle = self.make_bundle()
        original = bundle.read_bytes()
        self.assertTrue(original.startswith(b"{"))
        bundle.write_bytes(b'{"records":[{"id":"withheld-first-value"}],' + original[1:])
        private_key = self.root / "signing.key"
        public_key = self.root / "signing.pub"
        interop.generate_signing_key(private_key, public_key)

        with self.assertRaises(interop.InteropError) as caught:
            interop.sign_bundle(bundle, private_key)

        self.assertIn("duplicate JSON object member", str(caught.exception))

    def test_keygen_race_never_unlinks_competing_private_key_file(self):
        private_key = self.root / "race.key"
        public_key = self.root / "race.pub"
        real_os_open = os.open
        injected = False

        def racing_open(path, flags, mode=0o777):
            nonlocal injected
            if Path(path) == private_key and not injected:
                injected = True
                private_key.write_text("competitor-owned-file\n", encoding="utf-8")
                raise FileExistsError("synthetic competing creator")
            return real_os_open(path, flags, mode)

        with mock.patch.object(interop.os, "open", side_effect=racing_open):
            with self.assertRaises(interop.InteropError):
                interop.generate_signing_key(private_key, public_key)

        self.assertEqual(private_key.read_text(encoding="utf-8"), "competitor-owned-file\n")
        self.assertFalse(public_key.exists())

    def test_conformance_fixture_must_declare_python_json_v0_1(self):
        value = json.loads(FIXTURE.read_text(encoding="utf-8"))
        value["canonicalizer"] = "RFC8785-JCS"

        with self.assertRaises(interop.InteropError) as caught:
            interop.validate_conformance_vectors(value)

        self.assertIn("canonicalizer", str(caught.exception))

    def test_capability_consumer_rules_cannot_disable_routing_or_add_provider_memory(self):
        value = interop.capability_manifest()
        value["consumer_rules"]["index_results_require_routing"] = False
        with self.assertRaises(interop.InteropError):
            interop.validate_capability_manifest(value)

        value = interop.capability_manifest()
        value["consumer_rules"]["provider_memory_dependency"] = "required"
        with self.assertRaises(interop.InteropError):
            interop.validate_capability_manifest(value)

    def test_advertised_bundle_validator_does_not_initialize_missing_routing_state(self):
        bundle = self.make_bundle()
        gitignore = self.workspace / ".gitignore"
        profile = self.workspace / "profiles" / "general.json"
        policy = self.workspace / "routing" / "policy.json"
        before_gitignore = gitignore.read_bytes()
        before_profile = profile.read_bytes()
        policy.unlink()

        failed = run_cli(READONLY_VALIDATOR, self.workspace, bundle, expect=2)

        self.assertIn("read-only validation will not create", failed.stderr)
        self.assertFalse(policy.exists())
        self.assertEqual(gitignore.read_bytes(), before_gitignore)
        self.assertEqual(profile.read_bytes(), before_profile)

    def test_advertised_bundle_validator_recomputes_valid_bundle_without_writes(self):
        bundle = self.make_bundle()
        watched = [
            self.workspace / ".gitignore",
            self.workspace / "profiles" / "general.json",
            self.workspace / "routing" / "policy.json",
            self.workspace / "memory" / "records.jsonl",
        ]
        before = {path: path.read_bytes() if path.exists() else None for path in watched}

        result = json.loads(run_cli(READONLY_VALIDATOR, self.workspace, bundle).stdout)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["read_only"])
        after = {path: path.read_bytes() if path.exists() else None for path in watched}
        self.assertEqual(after, before)

    def test_adapter_examples_point_bundle_validation_at_read_only_entrypoint(self):
        mcp = json.loads((ROOT / "examples" / "mcp" / "tools.json").read_text(encoding="utf-8"))
        generic = json.loads((ROOT / "examples" / "tool-adapter.json").read_text(encoding="utf-8"))
        mcp_validator = next(tool for tool in mcp["tools"] if tool["name"] == "ai_context_validate_bundle")
        self.assertTrue(mcp_validator["read_only"])
        self.assertIn("tools/validate_bundle_readonly.py", mcp_validator["command_template"])
        generic_validator = generic["operations"]["validate_routed_bundle"]
        self.assertTrue(generic_validator["read_only"])
        self.assertIn("tools/validate_bundle_readonly.py", generic_validator["command_template"])


if __name__ == "__main__":
    unittest.main()
