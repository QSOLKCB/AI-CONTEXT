import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Phase13ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = load("phase13_build", ROOT / "tools/build_phase13_artifacts.py")
        cls.verify = load("phase13_verify", ROOT / "tools/verify_phase13_artifacts.py")

    def test_phase13_identity_constants_are_frozen(self):
        self.assertEqual(self.build.REFERENCE_TAG, "v1.0.0")
        self.assertEqual(self.build.REFERENCE_COMMIT, "53d7d69dfacecf6f8605f5b6a51b2c68ee66572a")
        self.assertEqual(self.build.REFERENCE_TREE, "2c0592cbd074d7596e70681cc5ed869d6b9b00e4")
        self.assertEqual(self.build.FORMAL_COMMIT, "b5cf9ae50400b30ec51489bf4d1b51d431f08252")
        self.assertEqual(self.build.DOI, "10.5281/zenodo.22081189")

    def test_overview_pdf_is_deterministic(self):
        entries = [{"declaration": f"theorem{i}", "invariant": f"INVARIANT {i}"} for i in range(1, 36)]
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a.pdf"
            b = Path(td) / "b.pdf"
            self.build.build_overview_pdf(a, entries)
            self.build.build_overview_pdf(b, entries)
            self.assertEqual(hashlib.sha256(a.read_bytes()).digest(), hashlib.sha256(b.read_bytes()).digest())
            self.verify.verify_pdf(a)

    def test_deterministic_zip_has_sorted_fixed_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            stage = td / "stage"
            (stage / "reference-v1.0.0").mkdir(parents=True)
            (stage / "reference-v1.0.0" / "A.txt").write_text("A\n", encoding="utf-8")
            (stage / "formal").mkdir()
            (stage / "formal" / "B.txt").write_text("B\n", encoding="utf-8")
            a = td / "a.zip"
            b = td / "b.zip"
            self.build.deterministic_zip(stage, a)
            self.build.deterministic_zip(stage, b)
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_release_notes_checksum_policy_is_non_circular(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "RELEASE-NOTES.md"
            self.build.build_release_notes(out, "1" * 64, "2" * 64, 35)
            text = out.read_text(encoding="utf-8")
            self.assertIn("1" * 64, text)
            self.assertIn("2" * 64, text)
            self.assertIn("circular self-hash dependency", text)
            self.assertIn("Zenodo records the checksum of this file independently", text)


if __name__ == "__main__":
    unittest.main()
