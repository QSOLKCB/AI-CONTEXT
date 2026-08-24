import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "tools" / "validate_formalization.py"
TARGET = "53d7d69dfacecf6f8605f5b6a51b2c68ee66572a"
TREE = "2c0592cbd074d7596e70681cc5ed869d6b9b00e4"


class Phase12FormalizationTests(unittest.TestCase):
    def test_formalization_validator_passes(self):
        result = subprocess.run(
            [sys.executable, str(VALIDATOR)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            self.fail(f"validator failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["formalization_target"], TARGET)
        self.assertGreaterEqual(payload["theorems"], 30)
        self.assertGreaterEqual(payload["finite_counterexamples"], 8)
        self.assertEqual(payload["proof_placeholders"], 0)

    def test_inventory_is_bound_to_post_tag_release_target(self):
        inventory = json.loads((ROOT / "formal" / "theorem-inventory.json").read_text(encoding="utf-8"))
        target = inventory["formalization_target"]
        self.assertEqual(target["tag"], "v1.0.0")
        self.assertEqual(target["commit_sha"], TARGET)
        self.assertEqual(target["git_tree_sha"], TREE)
        self.assertEqual(target["lean_toolchain"], "leanprover/lean4:v4.30.0")

    def test_formalization_has_no_mathlib_dependency(self):
        lakefile = (ROOT / "lakefile.lean").read_text(encoding="utf-8")
        self.assertNotIn("mathlib", lakefile.casefold())
        self.assertNotIn("require ", lakefile)


if __name__ == "__main__":
    unittest.main()
