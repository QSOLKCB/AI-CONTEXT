import copy
import subprocess
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from test_phase13_archive import ROOT, load


class Phase13CodexHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = load("phase13_build_hardened", ROOT / "tools/build_phase13_artifacts.py")
        cls.verify = load("phase13_verify_hardened", ROOT / "tools/verify_phase13_artifacts.py")

    def test_formal_export_comes_from_bound_commit_not_worktree(self):
        tracked = ROOT / "lean-toolchain"
        original = tracked.read_bytes()
        probe = ROOT / "formal" / "_phase13_untracked_probe.txt"
        self.assertFalse(probe.exists())
        try:
            tracked.write_text("worktree-corruption\n", encoding="utf-8")
            probe.write_text("untracked-private-probe\n", encoding="utf-8")
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "formal"
                self.build.export_formal_layer(dest)
                expected = subprocess.run(
                    ["git", "show", f"{self.build.FORMAL_COMMIT}:lean-toolchain"],
                    cwd=ROOT,
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout
                self.assertEqual((dest / "lean-toolchain").read_bytes(), expected)
                self.assertFalse((dest / "formal" / probe.name).exists())
        finally:
            tracked.write_bytes(original)
            probe.unlink(missing_ok=True)

    def test_verifier_rejects_formal_bytes_not_from_bound_commit(self):
        expected = self.verify.git_archive_files(self.verify.FORMAL_COMMIT, self.verify.FORMAL_PATHS)
        files = {f"formal/{name}": data for name, data in expected.items()}
        self.verify.compare_formal_against_commit(files)
        victim = sorted(files)[0]
        tampered = dict(files)
        tampered[victim] = tampered[victim] + b"tamper"
        with self.assertRaisesRegex(RuntimeError, "formal/"):
            self.verify.compare_formal_against_commit(tampered)

    def test_verifier_rejects_moved_frozen_tag(self):
        with mock.patch.object(self.verify, "run_git", return_value="0" * 40):
            with self.assertRaisesRegex(RuntimeError, "v1.0.0 resolves"):
                self.verify.assert_frozen_git_bindings()

    def test_duplicate_zip_members_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "dup.zip"
            info = zipfile.ZipInfo("reference-v1.0.0/A.txt", self.verify.FIXED_ZIP_DT)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (self.verify.EXPECTED_FILE_MODE & 0xFFFF) << 16
            info.create_system = 3
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(path, "w") as zf:
                    zf.writestr(info, b"A")
                    zf.writestr(info, b"B")
            with self.assertRaisesRegex(RuntimeError, "duplicate normalized member"):
                self.verify.verify_zip(path)

    def test_zip_resource_limits_are_checked_before_payload_read(self):
        old_limit = self.verify.MAX_ZIP_MEMBER_BYTES
        self.verify.MAX_ZIP_MEMBER_BYTES = 4
        try:
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "large.zip"
                info = zipfile.ZipInfo("reference-v1.0.0/A.txt", self.verify.FIXED_ZIP_DT)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (self.verify.EXPECTED_FILE_MODE & 0xFFFF) << 16
                info.create_system = 3
                with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr(info, b"12345")
                with self.assertRaisesRegex(RuntimeError, "expanded-size limit"):
                    self.verify.verify_zip(path)
        finally:
            self.verify.MAX_ZIP_MEMBER_BYTES = old_limit

    def test_manifest_identity_fields_are_all_enforced(self):
        manifest = self.verify.expected_manifest_identity()
        manifest["files"] = []
        self.verify.validate_manifest_identity(manifest)
        altered = copy.deepcopy(manifest)
        altered["doi"] = "10.0000/not-the-record"
        with self.assertRaisesRegex(RuntimeError, "identity field drift: doi"):
            self.verify.validate_manifest_identity(altered)
        altered = copy.deepcopy(manifest)
        altered["formalization"]["lean_toolchain"] = "leanprover/lean4:unbound"
        with self.assertRaisesRegex(RuntimeError, "identity field drift: formalization"):
            self.verify.validate_manifest_identity(altered)

    def test_artifact_directory_rejects_any_extra_entry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in self.verify.EXPECTED_ARTIFACTS:
                (root / name).write_bytes(b"placeholder")
            (root / "extra-payload").mkdir()
            with self.assertRaisesRegex(RuntimeError, "exactly"):
                self.verify.verify_artifact_directory(root)

    def test_builder_requires_yes_and_dedicated_existing_output(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            out = base / "artifacts"
            out.mkdir()
            for name in self.build.EXPECTED_ARTIFACTS:
                (out / name).write_bytes(b"old")
            with self.assertRaisesRegex(RuntimeError, "--yes"):
                self.build.prepare_output_dir(out, False)
            prepared = self.build.prepare_output_dir(out, True)
            self.assertEqual(prepared, out.absolute())
            self.assertEqual(list(prepared.iterdir()), [])

            unrelated = base / "unrelated"
            unrelated.mkdir()
            (unrelated / "keep-me.txt").write_text("important\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "non-dedicated"):
                self.build.prepare_output_dir(unrelated, True)
            self.assertTrue((unrelated / "keep-me.txt").exists())

    def test_builder_rejects_symlink_output(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            target = base / "target"
            target.mkdir()
            link = base / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                self.build.prepare_output_dir(link, True)


if __name__ == "__main__":
    unittest.main()
