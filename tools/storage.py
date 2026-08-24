#!/usr/bin/env python3
"""Hardened Phase 7 storage entrypoint.

The original Phase 7 implementation is retained in ``storage_legacy.py`` for audit and
compatibility. This module enforces crash-safety, store-identity binding, concurrency
refresh, strict audit validation, and key-file safety invariants discovered in review.
"""
from __future__ import annotations

import base64
import errno
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

import storage_legacy as legacy
from storage_legacy import *  # noqa: F401,F403

_ORIGINAL_BUILD_PARSER = legacy.build_parser
_ORIGINAL_ROTATION_JOURNAL = legacy._rotation_journal
_validate_manifest = legacy._validate_manifest


def generate_key_file(path: Path) -> str:
    """Create a key file exactly once; never replace a concurrently created key."""
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(resolved, flags, 0o600)
    except FileExistsError as exc:
        raise StorageError(f"refusing to overwrite existing key file: {resolved}") from exc
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise StorageError(f"refusing to overwrite existing key file: {resolved}") from exc
        raise StorageError(f"cannot create key file: {exc}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(key.hex().encode("ascii") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(resolved, 0o600)
        legacy._fsync_dir(resolved.parent)
    except Exception:
        try:
            resolved.unlink()
        except FileNotFoundError:
            pass
        raise
    return derive_key_id(key)


def _aad(*, store_id: str, key_id: str, path_sha256: str) -> bytes:
    return canonical_bytes({
        "protocol": "AI-CONTEXT/ENCRYPTED-OBJECT",
        "schema_version": STORAGE_VERSION,
        "algorithm": ALGORITHM,
        "store_id": store_id,
        "key_id": key_id,
        "path_sha256": path_sha256,
    })


def _encrypt_inner(
    inner: dict[str, Any], *, key: bytes, key_id: str, path_hash: str, store_id: str
) -> dict[str, Any]:
    legacy._require_crypto()
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = legacy.AESGCM(key).encrypt(
        nonce,
        canonical_bytes(inner),
        _aad(store_id=store_id, key_id=key_id, path_sha256=path_hash),
    )
    core = {
        "protocol": "AI-CONTEXT/ENCRYPTED-OBJECT",
        "schema_version": STORAGE_VERSION,
        "algorithm": ALGORITHM,
        "store_id": store_id,
        "key_id": key_id,
        "path_sha256": path_hash,
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        "ciphertext_sha256": sha256_bytes(ciphertext),
    }
    return {"id": stable_id("storage-object", core), **core}


def _validate_envelope(value: Any, *, expected_store_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StorageError("encrypted object envelope must be an object")
    required = {
        "id", "protocol", "schema_version", "algorithm", "store_id", "key_id",
        "path_sha256", "nonce_b64", "ciphertext_b64", "ciphertext_sha256",
    }
    if set(value) != required:
        raise StorageError("encrypted object envelope has missing/unknown fields")
    if value.get("protocol") != "AI-CONTEXT/ENCRYPTED-OBJECT" or value.get("schema_version") != STORAGE_VERSION:
        raise StorageError("unsupported encrypted object protocol/schema")
    if value.get("algorithm") != ALGORITHM:
        raise StorageError("unsupported encrypted object algorithm")
    store_id = value.get("store_id")
    if not isinstance(store_id, str) or not store_id.startswith("store."):
        raise StorageError("invalid encrypted object store_id")
    if expected_store_id is not None and store_id != expected_store_id:
        raise StorageError("encrypted object belongs to a different store")
    if not isinstance(value.get("key_id"), str) or not value["key_id"].startswith(KEY_ID_PREFIX):
        raise StorageError("invalid encrypted object key_id")
    path_hash = value.get("path_sha256")
    if not isinstance(path_hash, str) or len(path_hash) != 64:
        raise StorageError("invalid encrypted object path hash")
    try:
        nonce = base64.b64decode(value["nonce_b64"], validate=True)
        ciphertext = base64.b64decode(value["ciphertext_b64"], validate=True)
    except Exception as exc:
        raise StorageError("invalid encrypted object base64") from exc
    if len(nonce) != NONCE_BYTES:
        raise StorageError("invalid AES-GCM nonce length")
    if value.get("ciphertext_sha256") != sha256_bytes(ciphertext):
        raise StorageError("encrypted object ciphertext hash mismatch")
    core = {key: value[key] for key in required if key != "id"}
    if value.get("id") != stable_id("storage-object", core):
        raise StorageError("encrypted object id/hash mismatch")
    return value


def _decrypt_envelope(
    envelope: dict[str, Any], *, key: bytes, expected_path: str | None = None,
    expected_store_id: str | None = None,
) -> dict[str, Any]:
    envelope = _validate_envelope(envelope, expected_store_id=expected_store_id)
    key_id = derive_key_id(key)
    if envelope["key_id"] != key_id:
        raise StorageError(f"wrong key for encrypted object: expected {envelope['key_id']}, got {key_id}")
    nonce = base64.b64decode(envelope["nonce_b64"])
    ciphertext = base64.b64decode(envelope["ciphertext_b64"])
    try:
        plaintext = legacy.AESGCM(key).decrypt(
            nonce,
            ciphertext,
            _aad(
                store_id=envelope["store_id"],
                key_id=key_id,
                path_sha256=envelope["path_sha256"],
            ),
        )
    except legacy.InvalidTag as exc:
        raise StorageError("encrypted object authentication failed") from exc
    try:
        inner = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError("decrypted object payload is invalid JSON") from exc
    if not isinstance(inner, dict) or set(inner) != {"logical_path", "content_b64", "content_sha256"}:
        raise StorageError("decrypted object payload has invalid fields")
    logical = normalize_logical_path(inner["logical_path"])
    if logical_path_sha256(logical) != envelope["path_sha256"]:
        raise StorageError("encrypted object logical path/hash mismatch")
    if expected_path is not None and logical != normalize_logical_path(expected_path):
        raise StorageError("encrypted object logical path does not match request")
    try:
        data = base64.b64decode(inner["content_b64"], validate=True)
    except Exception as exc:
        raise StorageError("decrypted object content is invalid base64") from exc
    if inner.get("content_sha256") != sha256_bytes(data):
        raise StorageError("decrypted object content hash mismatch")
    return {"logical_path": logical, "data": data, "content_sha256": inner["content_sha256"]}


def _pending_deletions_root(root: Path) -> Path:
    return root / "receipts" / "pending-deletions"


def _pending_deletion_path(root: Path, path_hash: str) -> Path:
    return _pending_deletions_root(root) / f"{path_hash}.json"


def _validate_deletion_receipt(row: Any, *, store_id: str) -> dict[str, Any]:
    required = {
        "id", "protocol", "schema_version", "store_id", "object_id", "path_sha256",
        "key_id", "ciphertext_sha256", "deletion_mode", "erasure_claim", "reason", "deleted_at",
    }
    if not isinstance(row, dict) or set(row) != required:
        raise StorageError("storage deletion receipt has missing/unknown fields")
    if row.get("protocol") != "AI-CONTEXT/STORAGE-DELETION" or row.get("schema_version") != STORAGE_VERSION:
        raise StorageError("unsupported storage deletion receipt")
    if row.get("store_id") != store_id:
        raise StorageError("storage deletion receipt belongs to a different store")
    if not isinstance(row.get("object_id"), str) or not row["object_id"].startswith("storage-object.sha256:"):
        raise StorageError("storage deletion receipt object_id invalid")
    if not isinstance(row.get("path_sha256"), str) or len(row["path_sha256"]) != 64:
        raise StorageError("storage deletion receipt path hash invalid")
    if not isinstance(row.get("key_id"), str) or not row["key_id"].startswith(KEY_ID_PREFIX):
        raise StorageError("storage deletion receipt key_id invalid")
    if not isinstance(row.get("ciphertext_sha256"), str) or len(row["ciphertext_sha256"]) != 64:
        raise StorageError("storage deletion receipt ciphertext hash invalid")
    if row.get("deletion_mode") != "primary-ciphertext-removal":
        raise StorageError("storage deletion receipt deletion_mode invalid")
    if row.get("erasure_claim") != "primary-store-ciphertext-removed-key-destruction-not-claimed":
        raise StorageError("storage deletion receipt overstates erasure claim")
    if not isinstance(row.get("reason"), str) or not row["reason"].strip():
        raise StorageError("storage deletion receipt reason invalid")
    if not isinstance(row.get("deleted_at"), str) or not row["deleted_at"]:
        raise StorageError("storage deletion receipt timestamp invalid")
    core = {key: row[key] for key in required if key != "id"}
    if row.get("id") != stable_id("storage-delete", core):
        raise StorageError("storage deletion receipt id/hash mismatch")
    return row


def _validate_pending_deletion(value: Any, *, store_id: str, path_hash: str) -> dict[str, Any]:
    required = {"protocol", "schema_version", "store_id", "path_sha256", "receipt"}
    if not isinstance(value, dict) or set(value) != required:
        raise StorageError("pending deletion journal has missing/unknown fields")
    if value.get("protocol") != "AI-CONTEXT/STORAGE-DELETION-PENDING" or value.get("schema_version") != STORAGE_VERSION:
        raise StorageError("unsupported pending deletion journal")
    if value.get("store_id") != store_id or value.get("path_sha256") != path_hash:
        raise StorageError("pending deletion journal/store/path mismatch")
    _validate_deletion_receipt(value.get("receipt"), store_id=store_id)
    return value


def _canonical_object_files(root: Path, *, store_id: str) -> list[tuple[Path, dict[str, Any]]]:
    rows: list[tuple[Path, dict[str, Any]]] = []
    objects_root = legacy._objects_root(root).resolve()
    for path in sorted(legacy._objects_root(root).rglob(f"*{OBJECT_SUFFIX}")):
        if path.is_symlink():
            raise StorageError(f"encrypted object path must not be a symlink: {path}")
        envelope = _validate_envelope(load_json(path), expected_store_id=store_id)
        expected = legacy._object_path(root, envelope["path_sha256"]).resolve()
        if path.resolve() != expected:
            raise StorageError(f"encrypted object stored under incorrect path: {path}")
        if not legacy._is_within(path.resolve(), objects_root):
            raise StorageError(f"encrypted object escapes object root: {path}")
        rows.append((path, envelope))
    return rows


class FilesystemBackend(legacy.FilesystemBackend):
    def list_objects(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.root.rglob("*")):
            if path.is_symlink():
                raise StorageError(f"filesystem backend refuses symlink object: {path}")
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not legacy._is_within(resolved, self.root):
                raise StorageError(f"filesystem object escapes backend root: {path}")
            rel = path.relative_to(self.root).as_posix()
            data = path.read_bytes()
            rows.append({"logical_path": rel, "content_sha256": sha256_bytes(data), "size": len(data)})
        return rows


class EncryptedDirectoryBackend:
    def __init__(self, root: Path, key_file: Path, *, allow_rotation: bool = False):
        legacy._require_crypto()
        self.root = root.expanduser().resolve()
        self.key_file = key_file.expanduser().resolve()
        self.key, self.key_id = load_key_file(self.key_file, store_root=self.root)
        self.allow_rotation = allow_rotation
        self.manifest = _validate_manifest(load_json(legacy._manifest_path(self.root)))
        self.store_id = self.manifest["store_id"]
        self._refresh_live_state()

    def _refresh_live_state(self) -> dict[str, Any]:
        manifest = _validate_manifest(load_json(legacy._manifest_path(self.root)))
        if manifest["store_id"] != self.store_id:
            raise StorageError("encrypted store identity changed while backend was open")
        rotation_exists = legacy._rotation_path(self.root).exists()
        if rotation_exists and not self.allow_rotation:
            raise StorageError("key rotation is in progress; resume or complete it before normal store access")
        if not self.allow_rotation and self.key_id != manifest["active_key_id"]:
            raise StorageError(
                f"key is not the active store key: active={manifest['active_key_id']} supplied={self.key_id}"
            )
        self.manifest = manifest
        return manifest

    def _object(self, logical_path: str) -> Path:
        return legacy._object_path(self.root, logical_path_sha256(logical_path))

    def _assert_no_pending_for(self, path_hash: str) -> None:
        if _pending_deletion_path(self.root, path_hash).exists():
            raise StorageError("deletion for this logical object is pending; repeat delete to resume it")

    def put_bytes(self, logical_path: str, data: bytes) -> dict[str, Any]:
        self._refresh_live_state()
        logical = normalize_logical_path(logical_path)
        path_hash = logical_path_sha256(logical)
        self._assert_no_pending_for(path_hash)
        inner = {
            "logical_path": logical,
            "content_b64": base64.b64encode(data).decode("ascii"),
            "content_sha256": sha256_bytes(data),
        }
        envelope = _encrypt_inner(
            inner,
            key=self.key,
            key_id=self.key_id,
            path_hash=path_hash,
            store_id=self.store_id,
        )
        atomic_write_json(legacy._object_path(self.root, path_hash), envelope, mode=0o600)
        return {
            "logical_path": logical,
            "path_sha256": path_hash,
            "object_id": envelope["id"],
            "content_sha256": inner["content_sha256"],
            "key_id": self.key_id,
            "encrypted": True,
        }

    def get_bytes(self, logical_path: str) -> bytes:
        self._refresh_live_state()
        logical = normalize_logical_path(logical_path)
        path_hash = logical_path_sha256(logical)
        self._assert_no_pending_for(path_hash)
        path = legacy._object_path(self.root, path_hash)
        if not path.exists():
            raise StorageError(f"encrypted object not found: {logical}")
        if path.is_symlink() or path.resolve() != legacy._object_path(self.root, path_hash).resolve():
            raise StorageError("encrypted object is not stored at canonical path")
        return _decrypt_envelope(
            load_json(path), key=self.key, expected_path=logical, expected_store_id=self.store_id
        )["data"]

    def delete(self, logical_path: str, *, reason: str) -> dict[str, Any]:
        self._refresh_live_state()
        if not isinstance(reason, str) or not reason.strip():
            raise StorageError("deletion requires a non-empty reason")
        logical = normalize_logical_path(logical_path)
        path_hash = logical_path_sha256(logical)
        path = legacy._object_path(self.root, path_hash)
        pending_path = _pending_deletion_path(self.root, path_hash)

        if pending_path.exists():
            pending = _validate_pending_deletion(
                load_json(pending_path), store_id=self.store_id, path_hash=path_hash
            )
            receipt = pending["receipt"]
        else:
            if not path.exists():
                raise StorageError(f"encrypted object not found: {logical}")
            envelope = _validate_envelope(load_json(path), expected_store_id=self.store_id)
            _decrypt_envelope(
                envelope, key=self.key, expected_path=logical, expected_store_id=self.store_id
            )
            core = {
                "protocol": "AI-CONTEXT/STORAGE-DELETION",
                "schema_version": STORAGE_VERSION,
                "store_id": self.store_id,
                "object_id": envelope["id"],
                "path_sha256": envelope["path_sha256"],
                "key_id": envelope["key_id"],
                "ciphertext_sha256": envelope["ciphertext_sha256"],
                "deletion_mode": "primary-ciphertext-removal",
                "erasure_claim": "primary-store-ciphertext-removed-key-destruction-not-claimed",
                "reason": reason.strip(),
                "deleted_at": utc_now(),
            }
            receipt = {"id": stable_id("storage-delete", core), **core}
            pending = {
                "protocol": "AI-CONTEXT/STORAGE-DELETION-PENDING",
                "schema_version": STORAGE_VERSION,
                "store_id": self.store_id,
                "path_sha256": path_hash,
                "receipt": receipt,
            }
            atomic_write_json(pending_path, pending, mode=0o600)

        if path.exists():
            envelope = _validate_envelope(load_json(path), expected_store_id=self.store_id)
            if envelope["id"] != receipt["object_id"]:
                raise StorageError("pending deletion object identity changed; refusing to delete replacement")
            path.unlink()
            legacy._fsync_dir(path.parent)

        append_jsonl_unique(legacy._deletion_receipts_path(self.root), [receipt])
        try:
            pending_path.unlink()
            legacy._fsync_dir(pending_path.parent)
        except FileNotFoundError:
            pass
        return receipt

    def list_objects(self) -> list[dict[str, Any]]:
        self._refresh_live_state()
        rows: list[dict[str, Any]] = []
        for path, envelope in _canonical_object_files(self.root, store_id=self.store_id):
            inner = _decrypt_envelope(envelope, key=self.key, expected_store_id=self.store_id)
            rows.append({
                "logical_path": inner["logical_path"],
                "path_sha256": envelope["path_sha256"],
                "object_id": envelope["id"],
                "content_sha256": inner["content_sha256"],
                "size": len(inner["data"]),
                "key_id": envelope["key_id"],
            })
        return sorted(rows, key=lambda row: row["logical_path"])

    def validate(self) -> dict[str, Any]:
        self._refresh_live_state()
        pending_root = _pending_deletions_root(self.root)
        pending = list(pending_root.glob("*.json")) if pending_root.exists() else []
        if pending:
            raise StorageError("pending deletion journal present; repeat delete for the affected object before validation")
        objects = self.list_objects()
        paths = [row["logical_path"] for row in objects]
        if len(paths) != len(set(paths)):
            raise StorageError("duplicate logical paths in encrypted store")
        return {
            "status": "ok",
            "backend": "encrypted-directory",
            "algorithm": ALGORITHM,
            "store_id": self.store_id,
            "active_key_id": self.manifest["active_key_id"],
            "objects": len(objects),
            "rotation_in_progress": legacy._rotation_path(self.root).exists(),
        }


def _rotation_journal(root: Path) -> dict[str, Any]:
    value = _ORIGINAL_ROTATION_JOURNAL(root)
    manifest = _validate_manifest(load_json(legacy._manifest_path(root)))
    if value["store_id"] != manifest["store_id"]:
        raise StorageError("rotation journal/store mismatch")
    return value


def _object_hashes(root: Path) -> list[str]:
    manifest = _validate_manifest(load_json(legacy._manifest_path(root)))
    return [envelope["path_sha256"] for _, envelope in _canonical_object_files(root, store_id=manifest["store_id"])]


def begin_rotation(root: Path, old_key_file: Path, new_key_file: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if legacy._rotation_path(root).exists():
        raise StorageError("rotation already in progress; use resume-rotation")
    manifest = _validate_manifest(load_json(legacy._manifest_path(root)))
    old_key, old_id = load_key_file(old_key_file, store_root=root)
    new_key, new_id = load_key_file(new_key_file, store_root=root)
    del old_key, new_key
    if old_id != manifest["active_key_id"]:
        raise StorageError("old key does not match active store key")
    history_ids = {entry["key_id"] for entry in manifest["key_history"]}
    if new_id in history_ids:
        raise StorageError("replacement key has already been used by this store")
    journal = {
        "protocol": "AI-CONTEXT/STORAGE-ROTATION",
        "schema_version": STORAGE_VERSION,
        "store_id": manifest["store_id"],
        "from_key_id": old_id,
        "to_key_id": new_id,
        "started_at": utc_now(),
        "object_path_hashes": _object_hashes(root),
    }
    atomic_write_json(legacy._rotation_path(root), journal, mode=0o600)
    return resume_rotation(root, old_key_file, new_key_file)


def _rotation_cleanup_committed(
    root: Path, journal: dict[str, Any], manifest: dict[str, Any], new_key: bytes, new_id: str
) -> dict[str, Any]:
    history = {entry["key_id"]: entry for entry in manifest["key_history"]}
    old_id = journal["from_key_id"]
    if old_id not in history or history[old_id]["status"] != "retired" or history[old_id].get("replaced_by") != new_id:
        raise StorageError("committed rotation manifest lacks retired predecessor metadata")
    if new_id not in history or history[new_id]["status"] != "active":
        raise StorageError("committed rotation manifest lacks active replacement metadata")
    for path_hash in journal["object_path_hashes"]:
        path = legacy._object_path(root, path_hash)
        if not path.exists():
            raise StorageError(f"rotation object disappeared: {path_hash}")
        if path.is_symlink() or path.resolve() != legacy._object_path(root, path_hash).resolve():
            raise StorageError("rotation object is not stored at canonical path")
        envelope = _validate_envelope(load_json(path), expected_store_id=manifest["store_id"])
        if envelope["path_sha256"] != path_hash or envelope["key_id"] != new_id:
            raise StorageError("committed rotation object state does not match replacement key")
        _decrypt_envelope(envelope, key=new_key, expected_store_id=manifest["store_id"])
    legacy._rotation_path(root).unlink()
    legacy._fsync_dir(root)
    return {
        "status": "ok",
        "store_id": manifest["store_id"],
        "from_key_id": old_id,
        "to_key_id": new_id,
        "objects_rotated_this_run": 0,
        "objects_total": len(journal["object_path_hashes"]),
        "cleanup_only": True,
    }


def resume_rotation(root: Path, old_key_file: Path, new_key_file: Path) -> dict[str, Any]:
    legacy._require_crypto()
    root = root.expanduser().resolve()
    journal = _rotation_journal(root)
    manifest = _validate_manifest(load_json(legacy._manifest_path(root)))
    old_key, old_id = load_key_file(old_key_file, store_root=root)
    new_key, new_id = load_key_file(new_key_file, store_root=root)
    if old_id != journal["from_key_id"] or new_id != journal["to_key_id"]:
        raise StorageError("supplied keys do not match rotation journal")
    if manifest["active_key_id"] == new_id:
        return _rotation_cleanup_committed(root, journal, manifest, new_key, new_id)
    if manifest["active_key_id"] != old_id:
        raise StorageError("rotation journal active-key precondition no longer holds")
    if new_id in {entry["key_id"] for entry in manifest["key_history"]}:
        raise StorageError("replacement key has already been used by this store")

    rotated = 0
    for path_hash in journal["object_path_hashes"]:
        path = legacy._object_path(root, path_hash)
        if not path.exists():
            raise StorageError(f"rotation object disappeared: {path_hash}")
        if path.is_symlink() or path.resolve() != legacy._object_path(root, path_hash).resolve():
            raise StorageError("rotation object is not stored at canonical path")
        envelope = _validate_envelope(load_json(path), expected_store_id=manifest["store_id"])
        if envelope["path_sha256"] != path_hash:
            raise StorageError("rotation object path/hash mismatch")
        if envelope["key_id"] == new_id:
            _decrypt_envelope(envelope, key=new_key, expected_store_id=manifest["store_id"])
            continue
        if envelope["key_id"] != old_id:
            raise StorageError(f"rotation encountered unexpected key id: {envelope['key_id']}")
        inner = _decrypt_envelope(envelope, key=old_key, expected_store_id=manifest["store_id"])
        replacement = _encrypt_inner(
            {
                "logical_path": inner["logical_path"],
                "content_b64": base64.b64encode(inner["data"]).decode("ascii"),
                "content_sha256": inner["content_sha256"],
            },
            key=new_key,
            key_id=new_id,
            path_hash=path_hash,
            store_id=manifest["store_id"],
        )
        atomic_write_json(path, replacement, mode=0o600)
        rotated += 1

    now = utc_now()
    history: list[dict[str, Any]] = []
    found_old = False
    for entry in manifest["key_history"]:
        item = dict(entry)
        if item["key_id"] == old_id:
            item.update({"status": "retired", "retired_at": now, "replaced_by": new_id})
            found_old = True
        history.append(item)
    if not found_old:
        raise StorageError("active key missing from key history")
    history.append({
        "key_id": new_id,
        "status": "active",
        "activated_at": now,
        "retired_at": None,
        "replaced_by": None,
    })
    updated = dict(manifest)
    updated.update({"active_key_id": new_id, "key_history": history, "updated_at": now})
    _validate_manifest(updated)
    atomic_write_json(legacy._manifest_path(root), updated, mode=0o600)
    legacy._rotation_path(root).unlink()
    legacy._fsync_dir(root)
    return {
        "status": "ok",
        "store_id": updated["store_id"],
        "from_key_id": old_id,
        "to_key_id": new_id,
        "objects_rotated_this_run": rotated,
        "objects_total": len(journal["object_path_hashes"]),
        "cleanup_only": False,
    }


def validate_deletion_receipts(root: Path) -> int:
    root = root.expanduser().resolve()
    manifest = _validate_manifest(load_json(legacy._manifest_path(root)))
    objects = {
        envelope["id"]
        for _, envelope in _canonical_object_files(root, store_id=manifest["store_id"])
    }
    count = 0
    for row in read_jsonl(legacy._deletion_receipts_path(root)):
        _validate_deletion_receipt(row, store_id=manifest["store_id"])
        if row["object_id"] in objects:
            raise StorageError("storage deletion receipt exists but ciphertext object is still present")
        count += 1
    return count


def validate_store(root: Path, key_file: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if legacy._rotation_path(root).exists():
        raise StorageError("rotation journal present; normal validation requires completed rotation")
    backend = EncryptedDirectoryBackend(root, key_file)
    result = backend.validate()
    result["deletion_receipts"] = validate_deletion_receipts(root)
    result["key_event_receipts"] = legacy.validate_key_event_receipts(root)
    return result


legacy.generate_key_file = generate_key_file
legacy._aad = _aad
legacy._encrypt_inner = _encrypt_inner
legacy._validate_envelope = _validate_envelope
legacy._decrypt_envelope = _decrypt_envelope
legacy.FilesystemBackend = FilesystemBackend
legacy.EncryptedDirectoryBackend = EncryptedDirectoryBackend
legacy._object_hashes = _object_hashes
legacy.begin_rotation = begin_rotation
legacy.resume_rotation = resume_rotation
legacy.validate_deletion_receipts = validate_deletion_receipts
legacy.validate_store = validate_store


def build_parser():
    return _ORIGINAL_BUILD_PARSER()


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "keygen":
            key_id = generate_key_file(Path(args.key_file))
            result = {"status": "ok", "key_id": key_id, "key_file": str(Path(args.key_file).expanduser().resolve())}
        elif args.command == "init":
            manifest = init_encrypted_store(Path(args.store), Path(args.key_file))
            result = {"status": "ok", "store_id": manifest["store_id"], "active_key_id": manifest["active_key_id"]}
        elif args.command == "put":
            backend = EncryptedDirectoryBackend(Path(args.store), Path(args.key_file))
            data = Path(args.input).expanduser().resolve().read_bytes()
            result = backend.put_bytes(args.logical_path, data)
        elif args.command == "get":
            key_path = Path(args.key_file).expanduser().resolve()
            output = Path(args.output).expanduser().resolve()
            store_root = Path(args.store).expanduser().resolve()
            if output == key_path:
                raise StorageError("refusing to write decrypted output over the active key file")
            if legacy._is_within(output, store_root):
                raise StorageError("refusing to write plaintext retrieval output inside the encrypted store")
            backend = EncryptedDirectoryBackend(Path(args.store), Path(args.key_file))
            data = backend.get_bytes(args.logical_path)
            atomic_write_bytes(output, data, mode=0o600)
            result = {"status": "ok", "output": str(output), "bytes": len(data), "content_sha256": sha256_bytes(data)}
        elif args.command == "list":
            backend = EncryptedDirectoryBackend(Path(args.store), Path(args.key_file))
            objects = backend.list_objects()
            result = {"status": "ok", "objects": objects, "count": len(objects)}
        elif args.command == "validate":
            result = validate_store(Path(args.store), Path(args.key_file))
        elif args.command == "delete":
            result = EncryptedDirectoryBackend(Path(args.store), Path(args.key_file)).delete(
                args.logical_path, reason=args.reason
            )
        elif args.command == "rotate-key":
            result = begin_rotation(Path(args.store), Path(args.old_key_file), Path(args.new_key_file))
        elif args.command == "resume-rotation":
            result = resume_rotation(Path(args.store), Path(args.old_key_file), Path(args.new_key_file))
        elif args.command == "attest-key-destruction":
            result = legacy.attest_key_destruction(
                Path(args.store), key_id=args.key_id, actor=args.actor, reason=args.reason
            )
        else:
            raise StorageError(f"unsupported command: {args.command}")
    except (StorageError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
