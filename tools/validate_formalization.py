#!/usr/bin/env python3
"""Validate the post-tag Lean 4 formalization inventory and archival constraints."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal"
INVENTORY = FORMAL / "theorem-inventory.json"
TOOLCHAIN = "leanprover/lean4:v4.30.0"
TARGET_TAG = "v1.0.0"
TARGET_COMMIT = "53d7d69dfacecf6f8605f5b6a51b2c68ee66572a"
TARGET_TREE = "2c0592cbd074d7596e70681cc5ed869d6b9b00e4"

THEOREM_RE = re.compile(r"(?m)^\s*theorem\s+([A-Za-z_][A-Za-z0-9_']*)")
EXAMPLE_RE = re.compile(r"(?m)^\s*example\b")
FORBIDDEN_PROOF_RE = re.compile(r"\b(sorry|admit|axiom)\b")


def fail(message: str) -> None:
    raise RuntimeError(message)


def strip_lean_comments_and_strings(text: str) -> str:
    """Neutralize Lean line/block comments and string literals while preserving lines.

    Lean block comments may nest. Preserving newlines keeps declaration locations stable,
    while replacing other ignored characters with spaces prevents prose or string data from
    being mistaken for declarations or proof placeholders.
    """
    out: list[str] = []
    i = 0
    block_depth = 0
    in_string = False
    escaped = False

    while i < len(text):
        if block_depth:
            if text.startswith("/-", i):
                block_depth += 1
                out.extend("  ")
                i += 2
                continue
            if text.startswith("-/", i):
                block_depth -= 1
                out.extend("  ")
                i += 2
                continue
            ch = text[i]
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue

        if in_string:
            ch = text[i]
            if ch == "\n":
                out.append("\n")
            else:
                out.append(" ")
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if text.startswith("--", i):
            out.extend("  ")
            i += 2
            while i < len(text) and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if text.startswith("/-", i):
            block_depth = 1
            out.extend("  ")
            i += 2
            continue
        if text[i] == '"':
            in_string = True
            escaped = False
            out.append(" ")
            i += 1
            continue

        out.append(text[i])
        i += 1

    if block_depth:
        fail("unterminated Lean block comment in formal source")
    if in_string:
        fail("unterminated Lean string literal in formal source")
    return "".join(out)


def main() -> int:
    if (ROOT / "lean-toolchain").read_text(encoding="utf-8").strip() != TOOLCHAIN:
        fail("lean-toolchain does not match the pinned Phase 12 toolchain")
    if not (ROOT / "lakefile.lean").is_file():
        fail("lakefile.lean is missing")

    lean_files = sorted(FORMAL.rglob("*.lean"))
    if not lean_files:
        fail("no Lean sources found")

    declarations: set[str] = set()
    example_count = 0
    for path in lean_files:
        text = path.read_text(encoding="utf-8")
        semantic_text = strip_lean_comments_and_strings(text)
        if FORBIDDEN_PROOF_RE.search(semantic_text):
            fail(f"unresolved or untrusted proof placeholder in {path.relative_to(ROOT)}")
        declarations.update(THEOREM_RE.findall(semantic_text))
        example_count += len(EXAMPLE_RE.findall(semantic_text))

    if example_count < 8:
        fail("finite counterexample suite must contain at least eight checked Lean examples")

    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    if inventory.get("protocol") != "AI-CONTEXT/FORMAL-THEOREM-INVENTORY":
        fail("unexpected theorem inventory protocol")
    target = inventory.get("formalization_target", {})
    expected_target = {
        "tag": TARGET_TAG,
        "commit_sha": TARGET_COMMIT,
        "git_tree_sha": TARGET_TREE,
        "reference_protocol_version": "0.1.0",
        "lean_toolchain": TOOLCHAIN,
    }
    if target != expected_target:
        fail("theorem inventory target does not exactly bind frozen v1.0.0")

    freeze = json.loads((ROOT / "release" / "v1-freeze.json").read_text(encoding="utf-8"))
    binding = freeze.get("tag_binding", {})
    if binding.get("tag") != TARGET_TAG:
        fail("release freeze tag does not match theorem target")
    if binding.get("formalization_target_commit_sha") != TARGET_COMMIT:
        fail("release freeze commit does not match theorem target")
    if binding.get("git_tree_sha") != TARGET_TREE:
        fail("release freeze tree does not match theorem target")

    entries = inventory.get("theorems")
    if not isinstance(entries, list) or not entries:
        fail("theorem inventory must contain entries")
    names = [entry.get("declaration") for entry in entries]
    if any(not isinstance(name, str) or not name for name in names):
        fail("every theorem inventory entry needs a declaration")
    if len(names) != len(set(names)):
        fail("duplicate theorem declaration in inventory")

    inventory_names = set(names)
    if inventory_names != declarations:
        missing = sorted(declarations - inventory_names)
        extra = sorted(inventory_names - declarations)
        fail(f"theorem inventory mismatch; missing={missing}, extra={extra}")

    for entry in entries:
        if not entry.get("invariant"):
            fail(f"{entry['declaration']} lacks invariant mapping")
        refs = entry.get("reference_implementation")
        tests = entry.get("adversarial_tests")
        if not isinstance(refs, list) or not refs or not all(isinstance(x, str) and x for x in refs):
            fail(f"{entry['declaration']} lacks reference implementation mapping")
        if not isinstance(tests, list) or not tests or not all(isinstance(x, str) and x for x in tests):
            fail(f"{entry['declaration']} lacks adversarial test mapping")

    core = (FORMAL / "AIContextFormal" / "Core.lean").read_text(encoding="utf-8")
    for literal in (TARGET_TAG, TARGET_COMMIT, TARGET_TREE):
        if literal not in core:
            fail(f"formal core is not visibly bound to {literal}")

    print(json.dumps({
        "status": "ok",
        "lean_toolchain": TOOLCHAIN,
        "formalization_target": TARGET_COMMIT,
        "theorems": len(declarations),
        "finite_counterexamples": example_count,
        "proof_placeholders": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
