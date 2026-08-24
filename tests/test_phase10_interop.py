import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import interop  # noqa: E402

CORE = TOOLS / "ai_context.py"
INTEROP = TOOLS / "interop.py"
FIXTURE = ROOT / "fixtures" / "interoperability" / "conformance-v0.1.0.json"
CAPABILITY_FIXTURE = ROOT / "fixtures" / "interoperability" / "capability-manifest.python.json"


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


class Phase10InteropTests(unittest.TestCase):
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

    def test_capability_manifest_is_stable_and_matches_fixture(self):
        generated = interop.capability_manifest()
        fixture = json.loads(CAPABILITY_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(generated, fixture)
        interop.validate_capability_manifest(generated)
        self.assertFalse(generated["consumer_rules"]["signature_grants_disclosure"])
        self.assertFalse(generated["consumer_rules"]["signature_grants_epistemic_authority"])

    def test_capability_manifest_cannot_upgrade_signature_authority(self):
        value = interop.capability_manifest()
        value["consumer_rules"]["signature_grants_disclosure"] = True
        with self.assertRaises(interop.InteropError):
            interop.validate_capability_manifest(value)

    def test_language_neutral_vectors_and_public_signature_validate(self):
        value = json.loads(FIXTURE.read_text(encoding="utf-8"))
        result = interop.validate_conformance_vectors(value)
        self.assertEqual(result["canonicalization_vectors"], 4)
        self.assertEqual(result["signed_receipt_public_vector"], "verified")
        rendered = FIXTURE.read_text(encoding="utf-8").casefold()
        self.assertNotIn("private_key", rendered)
        self.assertNotIn("private-key", rendered)

    def test_signed_bundle_receipt_roundtrip_with_external_trust_anchor(self):
        bundle = self.make_bundle()
        private_key = self.root / "signing.key"
        public_key = self.root / "signing.pub"
        receipt_path = self.root / "bundle.receipt.json"
        key_info = interop.generate_signing_key(private_key, public_key)
        receipt = interop.sign_bundle(bundle, private_key)
        receipt_path.write_bytes(interop.canonical_bytes(receipt) + b"\n")
        verified = interop.verify_signed_receipt(
            bundle,
            receipt_path,
            public_key_path=public_key,
        )
        self.assertTrue(verified["cryptographically_valid"])
        self.assertTrue(verified["external_trust_anchor"])
        self.assertEqual(verified["signer_key_id"], key_info["key_id"])
        self.assertEqual(verified["authority"], "integrity-attestation-only")
        self.assertFalse(verified["disclosure_authority_granted"])
        self.assertFalse(verified["epistemic_authority_granted"])

    def test_exact_bundle_byte_tamper_fails_receipt_verification(self):
        bundle = self.make_bundle()
        private_key = self.root / "signing.key"
        public_key = self.root / "signing.pub"
        receipt_path = self.root / "bundle.receipt.json"
        interop.generate_signing_key(private_key, public_key)
        receipt = interop.sign_bundle(bundle, private_key)
        receipt_path.write_bytes(interop.canonical_bytes(receipt) + b"\n")
        bundle.write_bytes(bundle.read_bytes() + b" \n")
        with self.assertRaises(interop.InteropError):
            interop.verify_signed_receipt(bundle, receipt_path, public_key_path=public_key)

    def test_wrong_external_public_key_is_rejected(self):
        bundle = self.make_bundle()
        private_key = self.root / "signing.key"
        public_key = self.root / "signing.pub"
        other_private = self.root / "other.key"
        other_public = self.root / "other.pub"
        receipt_path = self.root / "bundle.receipt.json"
        interop.generate_signing_key(private_key, public_key)
        interop.generate_signing_key(other_private, other_public)
        receipt = interop.sign_bundle(bundle, private_key)
        receipt_path.write_bytes(interop.canonical_bytes(receipt) + b"\n")
        with self.assertRaises(interop.InteropError):
            interop.verify_signed_receipt(bundle, receipt_path, public_key_path=other_public)

    @unittest.skipIf(os.name == "nt", "POSIX signing-key permission check")
    def test_signing_private_key_requires_owner_only_permissions(self):
        private_key = self.root / "signing.key"
        public_key = self.root / "signing.pub"
        interop.generate_signing_key(private_key, public_key)
        os.chmod(private_key, 0o644)
        with self.assertRaises(interop.InteropError):
            interop.sign_bundle(self.make_bundle(), private_key)

    def test_adapter_examples_are_read_only_and_authority_preserving(self):
        mcp = json.loads((ROOT / "examples" / "mcp" / "tools.json").read_text(encoding="utf-8"))
        generic = json.loads((ROOT / "examples" / "tool-adapter.json").read_text(encoding="utf-8"))
        self.assertTrue(all(tool["read_only"] for tool in mcp["tools"]))
        self.assertFalse(generic["authority_rules"]["tool_call_grants_memory_authority"])
        self.assertFalse(generic["authority_rules"]["tool_call_grants_disclosure_authority"])
        forbidden_tokens = {"promote", "review", "apply", "tombstone", "confidence"}
        rendered = json.dumps(mcp).casefold()
        for token in forbidden_tokens:
            self.assertNotIn(f'"{token}"', rendered)

    def test_jcs_evaluation_is_explicitly_non_adopting(self):
        text = (ROOT / "docs" / "JCS-EVALUATION.md").read_text(encoding="utf-8")
        self.assertIn("JCS is evaluated but not adopted", text)
        self.assertIn("python-json-v0.1", text)
        self.assertIn("migration", text.casefold())


class Phase10SchemaTests(unittest.TestCase):
    SCHEMAS = [
        "capability-manifest.schema.json",
        "signed-bundle-receipt.schema.json",
        "interop-conformance.schema.json",
    ]

    def test_phase10_schemas_are_valid_draft_2020_12(self):
        for name in self.SCHEMAS:
            with self.subTest(schema=name):
                schema = json.loads((ROOT / "spec" / name).read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)

    def test_capability_fixture_conforms_to_schema(self):
        schema = json.loads((ROOT / "spec" / "capability-manifest.schema.json").read_text())
        fixture = json.loads(CAPABILITY_FIXTURE.read_text())
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(fixture)), [])

    def test_interop_fixture_conforms_to_schema(self):
        schema = json.loads((ROOT / "spec" / "interop-conformance.schema.json").read_text())
        fixture = json.loads(FIXTURE.read_text())
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(fixture)), [])


if __name__ == "__main__":
    unittest.main()
