import json
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

import indexes  # noqa: E402

CORE = TOOLS / "ai_context.py"
CURATION = TOOLS / "curation.py"


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


class Phase9CodexHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli(CORE, "init", self.workspace)
        run_cli(CURATION, "init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def create_memory(self, name):
        path = self.root / f"{name}.json"
        path.write_text(json.dumps({"subject": name}), encoding="utf-8")
        candidate = json.loads(
            run_cli(
                CURATION,
                "propose",
                self.workspace,
                "--content-file",
                path,
                "--record-type",
                "claim",
                "--sensitivity",
                "private",
                "--epistemic-state",
                "user_asserted",
                "--confidence",
                "1",
            ).stdout
        )["candidate_id"]
        run_cli(
            CURATION,
            "review",
            self.workspace,
            "--candidate",
            candidate,
            "--decision",
            "approve",
            "--actor-type",
            "human",
            "--actor-label",
            "phase9-hardening-test",
        )
        return json.loads(
            run_cli(CURATION, "apply", self.workspace, "--candidate", candidate).stdout
        )["memory_id"]

    def test_missing_gitignore_is_created_before_private_indexes_are_written(self):
        self.create_memory("private-index-content")
        gitignore = self.workspace / ".gitignore"
        gitignore.unlink()

        result = indexes.build_indexes(self.workspace)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(gitignore.is_file())
        rules = gitignore.read_text(encoding="utf-8").splitlines()
        self.assertIn("indexes/", rules)
        self.assertIn(".indexes-build-*/", rules)
        self.assertIn(".indexes-old-*/", rules)
        self.assertTrue((self.workspace / "indexes" / "search.json").is_file())

    def test_invalid_workspace_does_not_modify_unrelated_gitignore(self):
        bogus = self.root / "not-a-workspace"
        bogus.mkdir()
        gitignore = bogus / ".gitignore"
        gitignore.write_text("keep-this-line\n", encoding="utf-8")
        before = gitignore.read_bytes()

        with self.assertRaises(Exception):
            indexes.build_indexes(bogus)

        self.assertEqual(gitignore.read_bytes(), before)
        self.assertFalse((bogus / "indexes").exists())

    def test_build_uses_one_snapshot_and_refuses_publish_after_midbuild_mutation(self):
        self.create_memory("snapshot-a")
        original = indexes._build_vector_from_active
        mutated = False

        def mutate_then_build(active, fingerprint):
            nonlocal mutated
            if not mutated:
                mutated = True
                self.create_memory("snapshot-b")
            return original(active, fingerprint)

        with mock.patch.object(indexes, "_build_vector_from_active", side_effect=mutate_then_build):
            with self.assertRaises(indexes.IndexError) as caught:
                indexes.build_indexes(self.workspace)

        self.assertIn("changed during index build", str(caught.exception))
        self.assertFalse((self.workspace / "indexes").exists())

    def test_validation_hashes_exact_artifact_bytes_and_requires_canonical_encoding(self):
        self.create_memory("byte-integrity")
        indexes.build_indexes(self.workspace)
        vector_path = self.workspace / "indexes" / "vector.json"
        value = json.loads(vector_path.read_text(encoding="utf-8"))
        vector_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

        with self.assertRaises(indexes.IndexError) as caught:
            indexes.validate_indexes(self.workspace)

        self.assertIn("canonical byte encoding", str(caught.exception))

    def test_search_uses_the_exact_cache_snapshot_that_was_validated(self):
        memory_id = self.create_memory("validated-snapshot-alpha")
        indexes.build_indexes(self.workspace)
        original = indexes._validate_index_snapshot

        def validate_then_tamper(workspace):
            summary, values = original(workspace)
            # Simulate another actor replacing the cache immediately after validation.
            # The query must use `values["search"]`, not re-read this unvalidated file.
            (self.workspace / "indexes" / "search.json").write_text("{}\n", encoding="utf-8")
            return summary, values

        with mock.patch.object(indexes, "_validate_index_snapshot", side_effect=validate_then_tamper):
            result = indexes.search_cache(self.workspace, "validated snapshot alpha")

        self.assertIn(memory_id, {row["memory_id"] for row in result["results"]})
        self.assertEqual(result["authority"], "candidate-retrieval-only")


if __name__ == "__main__":
    unittest.main()
