#!/usr/bin/env python3
"""Phase 7 storage backend interface and reference encrypted directory backend.

The encryption boundary is intentionally below AI-CONTEXT canonical semantics. Keys are
external capabilities, never canonical memory. The encrypted backend uses AES-256-GCM from
the maintained ``cryptography`` package and stores only non-secret key identifiers.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, runtime_checkable

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - CI installs the optional storage dependency.
    InvalidTag = Exception  # type: ignore[assignment]
    AESGCM = None  # type: ignore[assignment]

STORAGE_VERSION = "0.1.0"
ALGORITHM = "AES-256-GCM"
KEY_BYTES = 32
NONCE_BYTES = 12
KEY_ID_PREFIX = "key.sha256:"
OBJECT_SUFFIX = ".aic"


class StorageError(RuntimeError):
    pass


@runtime_checkable
class StorageBackend(Protocol):
    """Reference storage contract implemented by plaintext and encrypted backends."""

    def put_bytes(self, logical_path: str, data: bytes) -> dict[str, Any]: ...
    def get_bytes(self, logical_path: str) -> bytes: ...
    def delete(self, logical_path: str, *, reason: str) -> dict[str, Any]: ...
    def list_objects(self) -> list[dict[str, Any]]: ...
    def validate(self) -> dict[str, Any]: ...


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StorageError(f"value is not canonical JSON: {exc}") from exc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_id(prefix: str, core: dict[str, Any]) -> str:
    return f"{prefix}.sha256:{sha256_bytes(canonical_bytes(core))}"


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None and os.name != "nt":
            os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        _fsync_dir(path.parent)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any, *, mode: int | None = None) -> None:
    atomic_write_bytes(path, canonical_bytes(value) + b"\n", mode=mode)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"cannot read JSON {path}: {exc}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StorageError(f"invalid JSONL {path}:{line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise StorageError(f"JSONL row must be object: {path}:{line_no}")
        rows.append(row)
    return rows


def append_jsonl_unique(path: Path, rows: list[dict[str, Any]]) -> int:
    existing = read_jsonl(path)
    by_id: dict[str, bytes] = {}
    for row in existing:
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            raise StorageError(f"row missing id in {path}")
        by_id[rid] = canonical_bytes(row)
    additions: list[dict[str, Any]] = []
    for row in rows:
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            raise StorageError("receipt row missing id")
        rendered = canonical_bytes(row)
        if rid in by_id:
            if by_id[rid] != rendered:
                raise StorageError(f"id collision with different payload: {rid}")
            continue
        by_id[rid] = rendered
        additions.append(row)
    if additions:
        rendered = b"".join(canonical_bytes(row) + b"\n" for row in [*existing, *additions])
        atomic_write_bytes(path, rendered, mode=0o600)
    return len(additions)


def normalize_logical_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise StorageError("logical path must be a non-empty POSIX-style relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise StorageError(f"unsafe logical path: {value}")
    normalized = pure.as_posix()
    if len(normalized.encode("utf-8")) > 4096:
        raise StorageError("logical path is too long")
    return normalized


def logical_path_sha256(logical_path: str) -> str:
    return sha256_bytes(normalize_logical_path(logical_path).encode("utf-8"))


def derive_key_id(key: bytes) -> str:
    if not isinstance(key, bytes) or len(key) != KEY_BYTES:
        raise StorageError("AES-256-GCM key must be exactly 32 bytes")
    return KEY_ID_PREFIX + sha256_bytes(key)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def load_key_file(path: Path, *, store_root: Path | None = None) -> tuple[bytes, str]:
    resolved = path.expanduser().resolve()
    if store_root is not None and _is_within(resolved, store_root):
        raise StorageError("key file must live outside the encrypted store")
    try:
        text = resolved.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise StorageError(f"cannot read key file: {exc}") from exc
    if len(text) != KEY_BYTES * 2:
        raise StorageError("key file must contain exactly 64 hexadecimal characters")
    try:
        key = bytes.fromhex(text)
    except ValueError as exc:
        raise StorageError("key file is not valid hexadecimal") from exc
    if len(key) != KEY_BYTES:
        raise StorageError("key file does not contain a 32-byte key")
    if os.name != "nt":
        mode = stat.S_IMODE(resolved.stat().st_mode)
        if mode & 0o077:
            raise StorageError("key file permissions are too broad; require owner-only access (0600)")
    return key, derive_key_id(key)


def generate_key_file(path: Path) -> str:
    resolved = path.expanduser().resolve()
    if resolved.exists():
        raise StorageError(f"refusing to overwrite existing key file: {resolved}")
    key = secrets.token_bytes(KEY_BYTES)
    atomic_write_bytes(resolved, key.hex().encode("ascii") + b"\n", mode=0o600)
    return derive_key_id(key)


def _require_crypto() -> None:
    if AESGCM is None:
        raise StorageError("encrypted backend requires the maintained 'cryptography' package; install requirements-storage.txt")


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def _rotation_path(root: Path) -> Path:
    return root / "rotation.json"


def _objects_root(root: Path) -> Path:
    return root / "objects"


def _deletion_receipts_path(root: Path) -> Path:
    return root / "receipts" / "deletions.jsonl"


def _key_receipts_path(root: Path) -> Path:
    return root / "receipts" / "key-events.jsonl"


def _object_path(root: Path, path_hash: str) -> Path:
    return _objects_root(root) / path_hash[:2] / f"{path_hash}{OBJECT_SUFFIX}"


def _validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StorageError("storage manifest must be an object")
    required = {"protocol", "schema_version", "store_id", "backend", "algorithm", "active_key_id", "key_history", "created_at", "updated_at"}
    missing = sorted(required - set(value))
    if missing:
        raise StorageError(f"storage manifest missing: {', '.join(missing)}")
    unknown = sorted(set(value) - required)
    if unknown:
        raise StorageError(f"unknown storage manifest fields: {', '.join(unknown)}")
    if value.get("protocol") != "AI-CONTEXT/STORAGE-MANIFEST" or value.get("schema_version") != STORAGE_VERSION:
        raise StorageError("unsupported storage manifest protocol/schema")
    if value.get("backend") != "encrypted-directory" or value.get("algorithm") != ALGORITHM:
        raise StorageError("unsupported encrypted storage backend/algorithm")
    if not isinstance(value.get("store_id"), str) or not value["store_id"].startswith("store."):
        raise StorageError("invalid store_id")
    if not isinstance(value.get("active_key_id"), str) or not value["active_key_id"].startswith(KEY_ID_PREFIX):
        raise StorageError("invalid active_key_id")
    history = value.get("key_history")
    if not isinstance(history, list) or not history:
        raise StorageError("key_history must be a non-empty array")
    seen: set[str] = set()
    active_count = 0
    for entry in history:
        if not isinstance(entry, dict) or set(entry) != {"key_id", "status", "activated_at", "retired_at", "replaced_by"}:
            raise StorageError("invalid key_history entry fields")
        key_id = entry.get("key_id")
        if not isinstance(key_id, str) or not key_id.startswith(KEY_ID_PREFIX) or key_id in seen:
            raise StorageError("invalid or duplicate key_history key_id")
        seen.add(key_id)
        status_value = entry.get("status")
        if status_value not in {"active", "retired"}:
            raise StorageError("key_history status must be active or retired")
        if status_value == "active":
            active_count += 1
            if key_id != value["active_key_id"] or entry.get("retired_at") is not None:
                raise StorageError("active key history does not match active_key_id")
        elif not isinstance(entry.get("retired_at"), str):
            raise StorageError("retired key history requires retired_at")
    if active_count != 1:
        raise StorageError("storage manifest must contain exactly one active key")
    return value


def init_encrypted_store(root: Path, key_file: Path) -> dict[str, Any]:
    _require_crypto()
    root = root.expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise StorageError(f"refusing to initialize non-empty encrypted store: {root}")
    root.mkdir(parents=True, exist_ok=True)
    key, key_id = load_key_file(key_file, store_root=root)
    del key
    _objects_root(root).mkdir(parents=True, exist_ok=True)
    (root / "receipts").mkdir(parents=True, exist_ok=True)
    now = utc_now()
    manifest = {
        "protocol": "AI-CONTEXT/STORAGE-MANIFEST",
        "schema_version": STORAGE_VERSION,
        "store_id": f"store.{secrets.token_hex(16)}",
        "backend": "encrypted-directory",
        "algorithm": ALGORITHM,
        "active_key_id": key_id,
        "key_history": [{"key_id": key_id, "status": "active", "activated_at": now, "retired_at": None, "replaced_by": None}],
        "created_at": now,
        "updated_at": now,
    }
    atomic_write_json(_manifest_path(root), manifest, mode=0o600)
    (root / "README.txt").write_text(
        "AI-CONTEXT ENCRYPTED STORE\n\nEncrypted payloads live here. Keep all key files outside this directory.\nKey identifiers are metadata; key material is never stored here.\n",
        encoding="utf-8", newline="\n",
    )
    return manifest


def _aad(*, key_id: str, path_sha256: str) -> bytes:
    return canonical_bytes({"protocol": "AI-CONTEXT/ENCRYPTED-OBJECT", "schema_version": STORAGE_VERSION, "algorithm": ALGORITHM, "key_id": key_id, "path_sha256": path_sha256})


def _encrypt_inner(inner: dict[str, Any], *, key: bytes, key_id: str, path_hash: str) -> dict[str, Any]:
    _require_crypto()
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, canonical_bytes(inner), _aad(key_id=key_id, path_sha256=path_hash))
    core = {
        "protocol": "AI-CONTEXT/ENCRYPTED-OBJECT", "schema_version": STORAGE_VERSION, "algorithm": ALGORITHM,
        "key_id": key_id, "path_sha256": path_hash, "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"), "ciphertext_sha256": sha256_bytes(ciphertext),
    }
    return {"id": stable_id("storage-object", core), **core}


def _validate_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StorageError("encrypted object envelope must be an object")
    required = {"id", "protocol", "schema_version", "algorithm", "key_id", "path_sha256", "nonce_b64", "ciphertext_b64", "ciphertext_sha256"}
    if set(value) != required:
        raise StorageError("encrypted object envelope has missing/unknown fields")
    if value.get("protocol") != "AI-CONTEXT/ENCRYPTED-OBJECT" or value.get("schema_version") != STORAGE_VERSION:
        raise StorageError("unsupported encrypted object protocol/schema")
    if value.get("algorithm") != ALGORITHM:
        raise StorageError("unsupported encrypted object algorithm")
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


def _decrypt_envelope(envelope: dict[str, Any], *, key: bytes, expected_path: str | None = None) -> dict[str, Any]:
    envelope = _validate_envelope(envelope)
    key_id = derive_key_id(key)
    if envelope["key_id"] != key_id:
        raise StorageError(f"wrong key for encrypted object: expected {envelope['key_id']}, got {key_id}")
    nonce = base64.b64decode(envelope["nonce_b64"])
    ciphertext = base64.b64decode(envelope["ciphertext_b64"])
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(key_id=key_id, path_sha256=envelope["path_sha256"]))
    except InvalidTag as exc:
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


class FilesystemBackend:
    """Plain filesystem implementation of the same logical storage contract."""
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve(); self.root.mkdir(parents=True, exist_ok=True)
    def _path(self, logical_path: str) -> Path:
        logical = normalize_logical_path(logical_path); path = (self.root / PurePosixPath(logical)).resolve()
        if not _is_within(path, self.root): raise StorageError("logical path escapes filesystem backend root")
        return path
    def put_bytes(self, logical_path: str, data: bytes) -> dict[str, Any]:
        logical = normalize_logical_path(logical_path); atomic_write_bytes(self._path(logical), data, mode=0o600)
        return {"logical_path": logical, "content_sha256": sha256_bytes(data), "encrypted": False}
    def get_bytes(self, logical_path: str) -> bytes:
        try: return self._path(logical_path).read_bytes()
        except OSError as exc: raise StorageError(f"cannot read logical object: {exc}") from exc
    def delete(self, logical_path: str, *, reason: str) -> dict[str, Any]:
        path = self._path(logical_path); data = path.read_bytes(); path.unlink()
        return {"logical_path": normalize_logical_path(logical_path), "content_sha256": sha256_bytes(data), "reason": reason, "encrypted": False}
    def list_objects(self) -> list[dict[str, Any]]:
        rows=[]
        for path in sorted(p for p in self.root.rglob("*") if p.is_file()):
            rel=path.relative_to(self.root).as_posix(); data=path.read_bytes(); rows.append({"logical_path":rel,"content_sha256":sha256_bytes(data),"size":len(data)})
        return rows
    def validate(self) -> dict[str, Any]:
        rows=self.list_objects(); return {"status":"ok","backend":"filesystem","objects":len(rows)}


class EncryptedDirectoryBackend:
    def __init__(self, root: Path, key_file: Path, *, allow_rotation: bool = False):
        _require_crypto(); self.root=root.expanduser().resolve(); self.manifest=_validate_manifest(load_json(_manifest_path(self.root)))
        self.key,self.key_id=load_key_file(key_file,store_root=self.root)
        if self.key_id != self.manifest["active_key_id"] and not allow_rotation:
            raise StorageError(f"key is not the active store key: active={self.manifest['active_key_id']} supplied={self.key_id}")
        if _rotation_path(self.root).exists() and not allow_rotation:
            raise StorageError("key rotation is in progress; resume or complete it before normal store access")
    def _object(self, logical_path: str) -> Path: return _object_path(self.root,logical_path_sha256(logical_path))
    def put_bytes(self, logical_path: str, data: bytes) -> dict[str, Any]:
        logical=normalize_logical_path(logical_path)
        if self.key_id != self.manifest["active_key_id"]: raise StorageError("writes require the active key")
        path_hash=logical_path_sha256(logical); inner={"logical_path":logical,"content_b64":base64.b64encode(data).decode("ascii"),"content_sha256":sha256_bytes(data)}
        envelope=_encrypt_inner(inner,key=self.key,key_id=self.key_id,path_hash=path_hash); atomic_write_json(_object_path(self.root,path_hash),envelope,mode=0o600)
        return {"logical_path":logical,"path_sha256":path_hash,"object_id":envelope["id"],"content_sha256":inner["content_sha256"],"key_id":self.key_id,"encrypted":True}
    def get_bytes(self, logical_path: str) -> bytes:
        path=self._object(logical_path)
        if not path.exists(): raise StorageError(f"encrypted object not found: {normalize_logical_path(logical_path)}")
        return _decrypt_envelope(load_json(path),key=self.key,expected_path=logical_path)["data"]
    def delete(self, logical_path: str, *, reason: str) -> dict[str, Any]:
        if not isinstance(reason,str) or not reason.strip(): raise StorageError("deletion requires a non-empty reason")
        logical=normalize_logical_path(logical_path); path=self._object(logical)
        if not path.exists(): raise StorageError(f"encrypted object not found: {logical}")
        envelope=_validate_envelope(load_json(path)); _decrypt_envelope(envelope,key=self.key,expected_path=logical); deleted_at=utc_now()
        core={"protocol":"AI-CONTEXT/STORAGE-DELETION","schema_version":STORAGE_VERSION,"store_id":self.manifest["store_id"],"object_id":envelope["id"],"path_sha256":envelope["path_sha256"],"key_id":envelope["key_id"],"ciphertext_sha256":envelope["ciphertext_sha256"],"deletion_mode":"primary-ciphertext-removal","erasure_claim":"primary-store-ciphertext-removed-key-destruction-not-claimed","reason":reason.strip(),"deleted_at":deleted_at}
        receipt={"id":stable_id("storage-delete",core),**core}; append_jsonl_unique(_deletion_receipts_path(self.root),[receipt]); path.unlink(); _fsync_dir(path.parent); return receipt
    def list_objects(self) -> list[dict[str, Any]]:
        rows=[]
        for path in sorted(_objects_root(self.root).rglob(f"*{OBJECT_SUFFIX}")):
            envelope=_validate_envelope(load_json(path)); expected=f"{envelope['path_sha256']}{OBJECT_SUFFIX}"
            if path.name != expected or path.parent.name != envelope["path_sha256"][:2]: raise StorageError(f"encrypted object stored under incorrect path: {path}")
            inner=_decrypt_envelope(envelope,key=self.key); rows.append({"logical_path":inner["logical_path"],"path_sha256":envelope["path_sha256"],"object_id":envelope["id"],"content_sha256":inner["content_sha256"],"size":len(inner["data"]),"key_id":envelope["key_id"]})
        return sorted(rows,key=lambda row:row["logical_path"])
    def validate(self) -> dict[str, Any]:
        objects=self.list_objects(); paths=[row["logical_path"] for row in objects]
        if len(paths)!=len(set(paths)): raise StorageError("duplicate logical paths in encrypted store")
        return {"status":"ok","backend":"encrypted-directory","algorithm":ALGORITHM,"store_id":self.manifest["store_id"],"active_key_id":self.manifest["active_key_id"],"objects":len(objects),"rotation_in_progress":_rotation_path(self.root).exists()}


def _rotation_journal(root: Path) -> dict[str, Any]:
    value=load_json(_rotation_path(root)); required={"protocol","schema_version","store_id","from_key_id","to_key_id","started_at","object_path_hashes"}
    if not isinstance(value,dict) or set(value)!=required: raise StorageError("rotation journal has missing/unknown fields")
    if value.get("protocol")!="AI-CONTEXT/STORAGE-ROTATION" or value.get("schema_version")!=STORAGE_VERSION: raise StorageError("unsupported rotation journal protocol/schema")
    hashes=value.get("object_path_hashes")
    if not isinstance(hashes,list) or not all(isinstance(item,str) and len(item)==64 for item in hashes) or len(hashes)!=len(set(hashes)): raise StorageError("rotation journal object_path_hashes invalid")
    return value


def _object_hashes(root: Path) -> list[str]:
    hashes=[]
    for path in sorted(_objects_root(root).rglob(f"*{OBJECT_SUFFIX}")):
        stem=path.name[:-len(OBJECT_SUFFIX)]
        if len(stem)!=64: raise StorageError(f"unexpected encrypted object filename: {path}")
        hashes.append(stem)
    return hashes


def begin_rotation(root: Path, old_key_file: Path, new_key_file: Path) -> dict[str, Any]:
    root=root.expanduser().resolve()
    if _rotation_path(root).exists(): raise StorageError("rotation already in progress; use resume-rotation")
    manifest=_validate_manifest(load_json(_manifest_path(root))); old_key,old_id=load_key_file(old_key_file,store_root=root); new_key,new_id=load_key_file(new_key_file,store_root=root); del old_key,new_key
    if old_id!=manifest["active_key_id"]: raise StorageError("old key does not match active store key")
    if old_id==new_id: raise StorageError("new key must differ from active key")
    journal={"protocol":"AI-CONTEXT/STORAGE-ROTATION","schema_version":STORAGE_VERSION,"store_id":manifest["store_id"],"from_key_id":old_id,"to_key_id":new_id,"started_at":utc_now(),"object_path_hashes":_object_hashes(root)}
    atomic_write_json(_rotation_path(root),journal,mode=0o600); return resume_rotation(root,old_key_file,new_key_file)


def resume_rotation(root: Path, old_key_file: Path, new_key_file: Path) -> dict[str, Any]:
    _require_crypto(); root=root.expanduser().resolve(); journal=_rotation_journal(root); manifest=_validate_manifest(load_json(_manifest_path(root)))
    if journal["store_id"]!=manifest["store_id"]: raise StorageError("rotation journal/store mismatch")
    old_key,old_id=load_key_file(old_key_file,store_root=root); new_key,new_id=load_key_file(new_key_file,store_root=root)
    if old_id!=journal["from_key_id"] or new_id!=journal["to_key_id"]: raise StorageError("supplied keys do not match rotation journal")
    if manifest["active_key_id"]!=old_id: raise StorageError("rotation journal active-key precondition no longer holds")
    rotated=0
    for path_hash in journal["object_path_hashes"]:
        path=_object_path(root,path_hash)
        if not path.exists(): raise StorageError(f"rotation object disappeared: {path_hash}")
        envelope=_validate_envelope(load_json(path))
        if envelope["path_sha256"]!=path_hash: raise StorageError("rotation object path/hash mismatch")
        if envelope["key_id"]==new_id: _decrypt_envelope(envelope,key=new_key); continue
        if envelope["key_id"]!=old_id: raise StorageError(f"rotation encountered unexpected key id: {envelope['key_id']}")
        inner=_decrypt_envelope(envelope,key=old_key); replacement=_encrypt_inner({"logical_path":inner["logical_path"],"content_b64":base64.b64encode(inner["data"]).decode("ascii"),"content_sha256":inner["content_sha256"]},key=new_key,key_id=new_id,path_hash=path_hash); atomic_write_json(path,replacement,mode=0o600); rotated+=1
    now=utc_now(); history=[]; found_old=False
    for entry in manifest["key_history"]:
        item=dict(entry)
        if item["key_id"]==old_id: item.update({"status":"retired","retired_at":now,"replaced_by":new_id}); found_old=True
        history.append(item)
    if not found_old: raise StorageError("active key missing from key history")
    history.append({"key_id":new_id,"status":"active","activated_at":now,"retired_at":None,"replaced_by":None}); updated=dict(manifest); updated.update({"active_key_id":new_id,"key_history":history,"updated_at":now}); _validate_manifest(updated); atomic_write_json(_manifest_path(root),updated,mode=0o600); _rotation_path(root).unlink(); _fsync_dir(root)
    return {"status":"ok","store_id":updated["store_id"],"from_key_id":old_id,"to_key_id":new_id,"objects_rotated_this_run":rotated,"objects_total":len(journal["object_path_hashes"])}


def attest_key_destruction(root: Path, *, key_id: str, actor: str, reason: str) -> dict[str, Any]:
    root=root.expanduser().resolve(); manifest=_validate_manifest(load_json(_manifest_path(root))); history={entry["key_id"]:entry for entry in manifest["key_history"]}
    if not isinstance(key_id,str) or not key_id.startswith(KEY_ID_PREFIX): raise StorageError("invalid key id")
    if key_id not in history: raise StorageError("cannot attest destruction of unknown store key")
    if history[key_id]["status"]!="retired": raise StorageError("active key cannot be recorded as destroyed")
    if not actor.strip() or not reason.strip(): raise StorageError("key-destruction attestation requires actor and reason")
    core={"protocol":"AI-CONTEXT/STORAGE-KEY-EVENT","schema_version":STORAGE_VERSION,"store_id":manifest["store_id"],"event":"external-key-destruction-attestation","key_id":key_id,"claim_strength":"self-attested-external-action","actor":actor.strip(),"reason":reason.strip(),"created_at":utc_now()}; receipt={"id":stable_id("storage-key-event",core),**core}; append_jsonl_unique(_key_receipts_path(root),[receipt]); return receipt


def validate_deletion_receipts(root: Path) -> int:
    objects={load_json(path)["id"] for path in _objects_root(root).rglob(f"*{OBJECT_SUFFIX}")}; count=0
    for row in read_jsonl(_deletion_receipts_path(root)):
        core={key:value for key,value in row.items() if key!="id"}
        if row.get("protocol")!="AI-CONTEXT/STORAGE-DELETION" or row.get("schema_version")!=STORAGE_VERSION: raise StorageError("unsupported storage deletion receipt")
        if row.get("id")!=stable_id("storage-delete",core): raise StorageError("storage deletion receipt id/hash mismatch")
        if row.get("object_id") in objects: raise StorageError("storage deletion receipt exists but ciphertext object is still present")
        if row.get("erasure_claim")!="primary-store-ciphertext-removed-key-destruction-not-claimed": raise StorageError("storage deletion receipt overstates erasure claim")
        count+=1
    return count


def validate_key_event_receipts(root: Path) -> int:
    manifest=_validate_manifest(load_json(_manifest_path(root))); history={entry["key_id"]:entry for entry in manifest["key_history"]}; count=0
    for row in read_jsonl(_key_receipts_path(root)):
        core={key:value for key,value in row.items() if key!="id"}
        if row.get("protocol")!="AI-CONTEXT/STORAGE-KEY-EVENT" or row.get("schema_version")!=STORAGE_VERSION: raise StorageError("unsupported storage key-event receipt")
        if row.get("id")!=stable_id("storage-key-event",core): raise StorageError("storage key-event receipt id/hash mismatch")
        key_id=row.get("key_id")
        if key_id not in history or history[key_id]["status"]!="retired": raise StorageError("key-destruction attestation references non-retired key")
        if row.get("claim_strength")!="self-attested-external-action": raise StorageError("key-destruction attestation claim strength invalid")
        count+=1
    return count


def validate_store(root: Path, key_file: Path) -> dict[str, Any]:
    root=root.expanduser().resolve()
    if _rotation_path(root).exists(): raise StorageError("rotation journal present; normal validation requires completed rotation")
    backend=EncryptedDirectoryBackend(root,key_file); result=backend.validate(); result["deletion_receipts"]=validate_deletion_receipts(root); result["key_event_receipts"]=validate_key_event_receipts(root); return result


def build_parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser(description="AI-CONTEXT Phase 7 storage boundary"); sub=parser.add_subparsers(dest="command",required=True)
    keygen=sub.add_parser("keygen",help="generate a 256-bit external key file"); keygen.add_argument("key_file")
    init=sub.add_parser("init",help="initialize encrypted directory store"); init.add_argument("store"); init.add_argument("--key-file",required=True)
    put=sub.add_parser("put"); put.add_argument("store"); put.add_argument("logical_path"); put.add_argument("input"); put.add_argument("--key-file",required=True)
    get=sub.add_parser("get"); get.add_argument("store"); get.add_argument("logical_path"); get.add_argument("output"); get.add_argument("--key-file",required=True)
    ls=sub.add_parser("list"); ls.add_argument("store"); ls.add_argument("--key-file",required=True)
    validate=sub.add_parser("validate"); validate.add_argument("store"); validate.add_argument("--key-file",required=True)
    delete=sub.add_parser("delete"); delete.add_argument("store"); delete.add_argument("logical_path"); delete.add_argument("--key-file",required=True); delete.add_argument("--reason",required=True)
    rotate=sub.add_parser("rotate-key"); rotate.add_argument("store"); rotate.add_argument("--old-key-file",required=True); rotate.add_argument("--new-key-file",required=True)
    resume=sub.add_parser("resume-rotation"); resume.add_argument("store"); resume.add_argument("--old-key-file",required=True); resume.add_argument("--new-key-file",required=True)
    attest=sub.add_parser("attest-key-destruction"); attest.add_argument("store"); attest.add_argument("--key-id",required=True); attest.add_argument("--actor",required=True); attest.add_argument("--reason",required=True)
    return parser


def main() -> int:
    args=build_parser().parse_args()
    try:
        if args.command=="keygen":
            key_id=generate_key_file(Path(args.key_file)); result={"status":"ok","key_id":key_id,"key_file":str(Path(args.key_file).expanduser().resolve())}
        elif args.command=="init":
            manifest=init_encrypted_store(Path(args.store),Path(args.key_file)); result={"status":"ok","store_id":manifest["store_id"],"active_key_id":manifest["active_key_id"]}
        elif args.command=="put":
            backend=EncryptedDirectoryBackend(Path(args.store),Path(args.key_file)); data=Path(args.input).expanduser().resolve().read_bytes(); result=backend.put_bytes(args.logical_path,data)
        elif args.command=="get":
            backend=EncryptedDirectoryBackend(Path(args.store),Path(args.key_file)); data=backend.get_bytes(args.logical_path); output=Path(args.output).expanduser().resolve(); atomic_write_bytes(output,data,mode=0o600); result={"status":"ok","output":str(output),"bytes":len(data),"content_sha256":sha256_bytes(data)}
        elif args.command=="list":
            backend=EncryptedDirectoryBackend(Path(args.store),Path(args.key_file)); objects=backend.list_objects(); result={"status":"ok","objects":objects,"count":len(objects)}
        elif args.command=="validate": result=validate_store(Path(args.store),Path(args.key_file))
        elif args.command=="delete": result=EncryptedDirectoryBackend(Path(args.store),Path(args.key_file)).delete(args.logical_path,reason=args.reason)
        elif args.command=="rotate-key": result=begin_rotation(Path(args.store),Path(args.old_key_file),Path(args.new_key_file))
        elif args.command=="resume-rotation": result=resume_rotation(Path(args.store),Path(args.old_key_file),Path(args.new_key_file))
        elif args.command=="attest-key-destruction": result=attest_key_destruction(Path(args.store),key_id=args.key_id,actor=args.actor,reason=args.reason)
        else: raise StorageError(f"unsupported command: {args.command}")
    except (StorageError,OSError) as exc:
        print(f"error: {exc}",file=sys.stderr); return 2
    print(json.dumps(result,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False)); return 0


if __name__=="__main__":
    raise SystemExit(main())
