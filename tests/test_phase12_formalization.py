import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import validate_formalization  # noqa: E402

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

    def test_theorem_discovery_ignores_comments_and_strings(self):
        source = '''
/- theorem blockBogus : True := by trivial
   /- theorem nestedBogus : True := by trivial -/
-/
-- theorem lineBogus : True := by trivial
def prose := "theorem stringBogus : True := by trivial"
theorem realDeclaration : True := by trivial
'''
        semantic = validate_formalization.strip_lean_comments_and_strings(source)
        self.assertEqual(validate_formalization.THEOREM_RE.findall(semantic), ["realDeclaration"])
        self.assertIsNone(validate_formalization.FORBIDDEN_PROOF_RE.search(semantic))


if __name__ == "__main__":
    unittest.main()
