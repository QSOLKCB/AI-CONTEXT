import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import storage  # noqa: E402

STORAGE_CLI = TOOLS / "storage.py"


def run_storage(*args, expect=0):
    result = subprocess.run(
        [sys.executable, str(STORAGE_CLI), *map(str, args)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"expected {expect}, got {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class Phase7StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = self.root / "master.key"
        self.key_id = storage.generate_key_file(self.key)
        self.store = self.root / "store"
        self.manifest = storage.init_encrypted_store(self.store, self.key)

    def tearDown(self):
        self.temp.cleanup()

    def backend(self):
        return storage.EncryptedDirectoryBackend(self.store, self.key)

    def test_backend_interface_and_encrypted_roundtrip(self):
        plain = storage.FilesystemBackend(self.root / "plain")
        encrypted = self.backend()
        self.assertIsInstance(plain, storage.StorageBackend)
        self.assertIsInstance(encrypted, storage.StorageBackend)

        info = encrypted.put_bytes("memory/records.jsonl", b"synthetic-private-memory")
        self.assertEqual(encrypted.get_bytes("memory/records.jsonl"), b"synthetic-private-memory")
        object_path = encrypted._object("memory/records.jsonl")
        raw = object_path.read_bytes()
        self.assertNotIn(b"synthetic-private-memory", raw)
        self.assertNotIn(b"memory/records.jsonl", raw)
        self.assertNotIn(self.key.read_text(encoding="ascii").strip().encode("ascii"), raw)
        envelope = json.loads(object_path.read_text(encoding="utf-8"))
        self.assertEqual(envelope["store_id"], self.manifest["store_id"])
        self.assertEqual(info["key_id"], self.key_id)
        self.assertEqual(encrypted.validate()["objects"], 1)

    def test_wrong_key_and_ciphertext_tamper_fail_closed(self):
        encrypted = self.backend()
        encrypted.put_bytes("a.txt", b"hello")
        other_key = self.root / "other.key"
        storage.generate_key_file(other_key)
        with self.assertRaises(storage.StorageError):
            storage.EncryptedDirectoryBackend(self.store, other_key)

        object_path = encrypted._object("a.txt")
        envelope = json.loads(object_path.read_text(encoding="utf-8"))
        envelope["ciphertext_b64"] = envelope["ciphertext_b64"][:-4] + "AAAA"
        object_path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaises(storage.StorageError):
            encrypted.get_bytes("a.txt")

    def test_unsafe_logical_paths_and_key_inside_store_are_rejected(self):
        encrypted = self.backend()
        for logical in ("../x", "/x", "a\\b", "a/../b"):
            with self.subTest(logical=logical):
                with self.assertRaises(storage.StorageError):
                    encrypted.put_bytes(logical, b"x")

        inside = self.store / "bad.key"
        inside.write_text(self.key.read_text(encoding="ascii"), encoding="ascii")
        if os.name != "nt":
            os.chmod(inside, 0o600)
        with self.assertRaises(storage.StorageError):
            storage.load_key_file(inside, store_root=self.store)

    @unittest.skipIf(os.name == "nt", "POSIX permission check")
    def test_key_file_requires_owner_only_permissions(self):
        os.chmod(self.key, 0o644)
        with self.assertRaises(storage.StorageError):
            storage.load_key_file(self.key)

    def test_key_rotation_reencrypts_objects_and_records_history(self):
        encrypted = self.backend()
        encrypted.put_bytes("a", b"a")
        encrypted.put_bytes("b", b"b")
        new_key = self.root / "new.key"
        new_key_id = storage.generate_key_file(new_key)

        result = storage.begin_rotation(self.store, self.key, new_key)
        self.assertEqual(result["objects_total"], 2)
        self.assertEqual(result["to_key_id"], new_key_id)
        self.assertEqual(storage.EncryptedDirectoryBackend(self.store, new_key).get_bytes("a"), b"a")
        with self.assertRaises(storage.StorageError):
            storage.EncryptedDirectoryBackend(self.store, self.key)

        manifest = storage.load_json(self.store / "manifest.json")
        self.assertEqual(manifest["active_key_id"], new_key_id)
        old = next(item for item in manifest["key_history"] if item["key_id"] == self.key_id)
        self.assertEqual(old["status"], "retired")
        self.assertEqual(old["replaced_by"], new_key_id)

        receipt = storage.attest_key_destruction(
            self.store,
            key_id=self.key_id,
            actor="synthetic-test-user",
            reason="external key copies destroyed for test",
        )
        self.assertEqual(receipt["claim_strength"], "self-attested-external-action")
        storage.validate_store(self.store, new_key)

    def test_active_key_cannot_be_attested_destroyed(self):
        with self.assertRaises(storage.StorageError):
            storage.attest_key_destruction(
                self.store,
                key_id=self.key_id,
                actor="synthetic-test-user",
                reason="must fail",
            )

    def test_rotation_journal_blocks_normal_access(self):
        encrypted = self.backend()
        encrypted.put_bytes("a", b"a")
        new_key = self.root / "new.key"
        new_key_id = storage.generate_key_file(new_key)
        journal = {
            "protocol": "AI-CONTEXT/STORAGE-ROTATION",
            "schema_version": storage.STORAGE_VERSION,
            "store_id": self.manifest["store_id"],
            "from_key_id": self.key_id,
            "to_key_id": new_key_id,
            "started_at": storage.utc_now(),
            "object_path_hashes": [storage.logical_path_sha256("a")],
        }
        storage.atomic_write_json(self.store / "rotation.json", journal, mode=0o600)
        with self.assertRaises(storage.StorageError):
            storage.EncryptedDirectoryBackend(self.store, self.key)
        with self.assertRaises(storage.StorageError):
            storage.validate_store(self.store, self.key)

    def test_deletion_receipt_is_conservative_and_path_private(self):
        encrypted = self.backend()
        info = encrypted.put_bytes("private/x", b"x")
        receipt = encrypted.delete("private/x", reason="synthetic user deletion")
        self.assertFalse(encrypted._object("private/x").exists())
        self.assertEqual(receipt["object_id"], info["object_id"])
        self.assertNotIn("logical_path", receipt)
        self.assertEqual(
            receipt["erasure_claim"],
            "primary-store-ciphertext-removed-key-destruction-not-claimed",
        )
        self.assertEqual(storage.validate_deletion_receipts(self.store), 1)

    def test_manifest_contains_key_identifier_not_key_material(self):
        key_hex = self.key.read_text(encoding="ascii").strip()
        rendered = (self.store / "manifest.json").read_text(encoding="utf-8")
        self.assertNotIn(key_hex, rendered)
        self.assertIn(self.key_id, rendered)

    def test_deletion_receipt_is_not_persisted_before_durable_unlink(self):
        encrypted = self.backend()
        encrypted.put_bytes("delete-me", b"payload")
        object_path = encrypted._object("delete-me")
        original_unlink = Path.unlink

        def fail_object_unlink(path, *args, **kwargs):
            if path == object_path:
                raise OSError("synthetic unlink failure")
            return original_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", fail_object_unlink):
            with self.assertRaises(OSError):
                encrypted.delete("delete-me", reason="crash-window test")
        self.assertTrue(object_path.exists())
        self.assertEqual(storage.read_jsonl(self.store / "receipts" / "deletions.jsonl"), [])
        pending = self.store / "receipts" / "pending-deletions" / f"{storage.logical_path_sha256('delete-me')}.json"
        self.assertTrue(pending.exists())

        receipt = encrypted.delete("delete-me", reason="crash-window test")
        self.assertFalse(object_path.exists())
        self.assertEqual(storage.read_jsonl(self.store / "receipts" / "deletions.jsonl")[0]["id"], receipt["id"])

    def test_foreign_and_malformed_deletion_receipts_fail_validation(self):
        encrypted = self.backend()
        encrypted.put_bytes("x", b"x")
        receipt = encrypted.delete("x", reason="make receipt")

        other_store = self.root / "other-store"
        storage.init_encrypted_store(other_store, self.key)
        storage.atomic_write_bytes(
            other_store / "receipts" / "deletions.jsonl",
            storage.canonical_bytes(receipt) + b"\n",
            mode=0o600,
        )
        with self.assertRaises(storage.StorageError):
            storage.validate_deletion_receipts(other_store)

        malformed = dict(receipt)
        malformed.pop("reason")
        malformed_core = {key: value for key, value in malformed.items() if key != "id"}
        malformed["id"] = storage.stable_id("storage-delete", malformed_core)
        storage.atomic_write_bytes(
            self.store / "receipts" / "deletions.jsonl",
            storage.canonical_bytes(malformed) + b"\n",
            mode=0o600,
        )
        with self.assertRaises(storage.StorageError):
            storage.validate_deletion_receipts(self.store)

    def test_rotation_cleanup_after_manifest_commit_is_resumable(self):
        encrypted = self.backend()
        encrypted.put_bytes("a", b"a")
        new_key = self.root / "new.key"
        new_key_id = storage.generate_key_file(new_key)
        path_hash = storage.logical_path_sha256("a")
        storage.begin_rotation(self.store, self.key, new_key)

        journal = {
            "protocol": "AI-CONTEXT/STORAGE-ROTATION",
            "schema_version": storage.STORAGE_VERSION,
            "store_id": self.manifest["store_id"],
            "from_key_id": self.key_id,
            "to_key_id": new_key_id,
            "started_at": storage.utc_now(),
            "object_path_hashes": [path_hash],
        }
        storage.atomic_write_json(self.store / "rotation.json", journal, mode=0o600)
        result = storage.resume_rotation(self.store, self.key, new_key)
        self.assertTrue(result["cleanup_only"])
        self.assertFalse((self.store / "rotation.json").exists())
        self.assertEqual(storage.EncryptedDirectoryBackend(self.store, new_key).get_bytes("a"), b"a")

    def test_encrypted_object_cannot_be_substituted_across_stores_with_same_key(self):
        first = self.backend()
        first.put_bytes("same", b"first")
        second_store = self.root / "second-store"
        storage.init_encrypted_store(second_store, self.key)
        second = storage.EncryptedDirectoryBackend(second_store, self.key)
        second.put_bytes("same", b"second")
        shutil.copyfile(first._object("same"), second._object("same"))
        with self.assertRaises(storage.StorageError):
            second.get_bytes("same")

    def test_key_generation_is_exclusive_under_concurrent_create(self):
        target = self.root / "race.key"
        barrier = threading.Barrier(2)
        results = []

        def worker():
            barrier.wait()
            try:
                results.append(("ok", storage.generate_key_file(target)))
            except storage.StorageError as exc:
                results.append(("error", str(exc)))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(kind == "ok" for kind, _ in results), 1)
        self.assertEqual(sum(kind == "error" for kind, _ in results), 1)
        storage.load_key_file(target)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_plaintext_listing_rejects_symlink_escape(self):
        outside = self.root / "outside.txt"
        outside.write_bytes(b"outside")
        plain = storage.FilesystemBackend(self.root / "plain")
        os.symlink(outside, plain.root / "escape.txt")
        with self.assertRaises(storage.StorageError):
            plain.list_objects()

    def test_stale_backend_rechecks_live_rotation_and_active_key(self):
        stale = self.backend()
        stale.put_bytes("a", b"a")
        new_key = self.root / "new.key"
        storage.generate_key_file(new_key)
        storage.begin_rotation(self.store, self.key, new_key)
        with self.assertRaises(storage.StorageError):
            stale.put_bytes("late", b"must fail")
        with self.assertRaises(storage.StorageError):
            stale.get_bytes("a")

    def test_validation_rejects_object_moved_to_noncanonical_physical_path(self):
        encrypted = self.backend()
        encrypted.put_bytes("a", b"a")
        original = encrypted._object("a")
        envelope = json.loads(original.read_text(encoding="utf-8"))
        moved = self.store / "objects" / "extra" / envelope["path_sha256"][:2] / original.name
        moved.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(original, moved)
        with self.assertRaises(storage.StorageError):
            encrypted.validate()

    def test_retired_key_cannot_be_reused_for_rotation(self):
        encrypted = self.backend()
        encrypted.put_bytes("a", b"a")
        new_key = self.root / "new.key"
        storage.generate_key_file(new_key)
        storage.begin_rotation(self.store, self.key, new_key)
        with self.assertRaises(storage.StorageError):
            storage.begin_rotation(self.store, new_key, self.key)
        self.assertFalse((self.store / "rotation.json").exists())

    def test_get_cli_refuses_to_overwrite_key_file(self):
        encrypted = self.backend()
        encrypted.put_bytes("a", b"not-a-key")
        original_key = self.key.read_bytes()
        failed = run_storage(
            "get", self.store, "a", self.key, "--key-file", self.key, expect=2
        )
        self.assertIn("active key file", failed.stderr)
        self.assertEqual(self.key.read_bytes(), original_key)
        self.assertEqual(self.backend().get_bytes("a"), b"not-a-key")


class Phase7StorageSchemaTests(unittest.TestCase):
    SCHEMAS = [
        "storage-manifest.schema.json",
        "storage-object.schema.json",
        "storage-deletion-receipt.schema.json",
        "storage-key-event.schema.json",
        "storage-rotation.schema.json",
    ]

    def test_storage_schemas_are_valid_draft_2020_12(self):
        for name in self.SCHEMAS:
            with self.subTest(schema=name):
                schema = json.loads((ROOT / "spec" / name).read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)

    def test_storage_schemas_have_no_key_material_fields(self):
        forbidden = {"key", "key_hex", "key_b64", "key_material", "passphrase", "recovery_key"}
        for name in self.SCHEMAS:
            rendered = json.dumps(json.loads((ROOT / "spec" / name).read_text(encoding="utf-8")))
            for field in forbidden:
                self.assertNotIn(f'"{field}"', rendered, f"{name} exposes forbidden key material field {field}")

    def test_encrypted_object_schema_binds_store_identity(self):
        schema = json.loads((ROOT / "spec" / "storage-object.schema.json").read_text(encoding="utf-8"))
        self.assertIn("store_id", schema["required"])
        self.assertIn("store_id", schema["properties"])


if __name__ == "__main__":
    unittest.main()
