import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
FIXTURES = ROOT / "fixtures" / "provider-drift"
CORE_CLI = TOOLS / "ai_context.py"
PROVIDER_CLI = TOOLS / "provider_import.py"

sys.path.insert(0, str(TOOLS))
import provider_adapters as adapters  # noqa: E402


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


class ProviderDriftTests(unittest.TestCase):
    def load(self, rel):
        return json.loads((FIXTURES / rel).read_text(encoding="utf-8"))

    def test_manifested_provider_drift_contract(self):
        manifest = self.load("manifest.json")
        parsers = {
            "chatgpt": adapters.parse_chatgpt,
            "claude": adapters.parse_claude,
            "gemini": adapters.parse_gemini_takeout,
            "grok": adapters.parse_grok_export,
        }
        for case in manifest["cases"]:
            with self.subTest(adapter=case["adapter"], path=case["path"]):
                parser = parsers[case["adapter"]]
                data = self.load(case["path"])
                if case["expected_status"] == "reject":
                    with self.assertRaises(adapters.AdapterError):
                        parser(data)
                    continue
                result = parser(data)
                self.assertEqual(result.parse_status, case["expected_status"])
                self.assertEqual(len(result.messages), case["expected_messages"])
                self.assertEqual(result.adapter_id, case["adapter"])
                self.assertEqual(result.adapter_version, "0.1.0")

    def test_core_chatgpt_and_claude_importers_obey_drift_manifest(self):
        manifest = self.load("manifest.json")
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            for case in manifest["cases"]:
                if case["adapter"] not in {"chatgpt", "claude"}:
                    continue
                workspace = temp_root / f"workspace-{case['adapter']}-{Path(case['path']).stem}"
                run_cli(CORE_CLI, "init", workspace)
                source = FIXTURES / case["path"]
                if case["expected_status"] == "reject":
                    run_cli(
                        CORE_CLI,
                        "import",
                        workspace,
                        source,
                        "--adapter",
                        case["adapter"],
                        expect=2,
                    )
                    continue
                result = json.loads(run_cli(
                    CORE_CLI,
                    "import",
                    workspace,
                    source,
                    "--adapter",
                    case["adapter"],
                ).stdout)
                self.assertEqual(result["parse_status"], case["expected_status"])
                self.assertEqual(result["observations_total"], case["expected_messages"])
                run_cli(CORE_CLI, "validate", workspace)

    def test_grok_thinking_trace_never_enters_normalized_messages(self):
        raw = (FIXTURES / "grok" / "thinking-trace-v1.json").read_text(encoding="utf-8")
        result = adapters.parse_grok_export(json.loads(raw))
        serialized = json.dumps([message.__dict__ for message in result.messages], sort_keys=True)
        self.assertIn("Visible synthetic answer", serialized)
        self.assertNotIn("synthetic private reasoning", serialized)
        self.assertNotIn("synthetic trace fragment", serialized)
        self.assertEqual(result.parse_status, "partial")
        self.assertTrue(any("intentionally excluded" in warning for warning in result.warnings))

    def test_grok_unknown_response_payload_marks_partial(self):
        data = {
            "conversations": [{
                "conversation": {"id": "conv-1", "title": "Drift"},
                "responses": [
                    {"response": {"_id": "m1", "sender": "human", "message": "hello"}},
                    {"response": {"_id": "m2", "sender": "assistant", "future_content": "not understood"}},
                ],
            }]
        }
        result = adapters.parse_grok_export(data)
        self.assertEqual(result.parse_status, "partial")
        self.assertEqual(len(result.messages), 1)
        self.assertTrue(any("unrecognized Grok response payload" in warning for warning in result.warnings))

    def test_browser_html_role_attributes(self):
        html = (FIXTURES / "browser-chat" / "role-attributes.html").read_text(encoding="utf-8")
        result = adapters.parse_browser_html(html, source_id="fixture.html")
        self.assertEqual(result.parse_status, "exact")
        self.assertEqual([message.actor for message in result.messages], ["user", "assistant"])
        self.assertEqual(len(result.messages), 2)

    def test_browser_html_void_elements_do_not_break_capture_depth(self):
        html = (
            '<div data-message-author-role="user">Hello<br>world</div>'
            '<div data-message-author-role="assistant">Hi there</div>'
        )
        result = adapters.parse_browser_html(html, source_id="void.html")
        self.assertEqual(result.parse_status, "exact")
        self.assertEqual(len(result.messages), 2)
        self.assertEqual(result.messages[0].actor, "user")
        self.assertIn("Hello", result.messages[0].text)
        self.assertIn("world", result.messages[0].text)
        self.assertEqual(result.messages[1].actor, "assistant")

    def test_browser_role_prefixed_text(self):
        text = (FIXTURES / "browser-chat" / "role-prefixed.txt").read_text(encoding="utf-8")
        result = adapters.parse_role_prefixed_text(text, source_id="fixture.txt")
        self.assertEqual(result.parse_status, "exact")
        self.assertEqual(len(result.messages), 4)

    def test_browser_unlabelled_text_is_partial_document(self):
        result = adapters.parse_role_prefixed_text("A plain archived page with no roles.", source_id="plain.txt")
        self.assertEqual(result.parse_status, "partial")
        self.assertEqual(result.messages[0].kind, "browser_chat_document")
        self.assertIsNone(result.messages[0].actor)

    def test_plugin_descriptor_conforms_to_schema(self):
        schema = json.loads((ROOT / "spec" / "adapter-plugin.schema.json").read_text(encoding="utf-8"))
        descriptor = self.load("plugin/example-adapter.json")
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(descriptor)
        adapters.validate_plugin_descriptor(descriptor)

    def test_plugin_runtime_validator_rejects_unknown_schema_property(self):
        descriptor = self.load("plugin/example-adapter.json")
        descriptor["message"]["actor_paths"] = "role"
        with self.assertRaises(adapters.AdapterError):
            adapters.validate_plugin_descriptor(descriptor)

    def test_plugin_mapping(self):
        descriptor = self.load("plugin/example-adapter.json")
        export = self.load("plugin/synthetic-community-export.json")
        result = adapters.parse_plugin_json(export, descriptor)
        self.assertEqual(result.parse_status, "exact")
        self.assertEqual(result.adapter_id, "plugin:community.synthetic-chat@1.0.0")
        self.assertEqual([message.actor for message in result.messages], ["user", "assistant"])
        self.assertEqual(len(result.messages), 2)

    def test_plugin_missing_optional_conversation_id_path_uses_synthetic_id(self):
        descriptor = self.load("plugin/example-adapter.json")
        descriptor["conversation"].pop("id_path")
        data = {
            "conversations": [{
                "title": "No ID",
                "messages": [{
                    "message_id": "m1",
                    "role": "human",
                    "created_at": "2026-01-01T00:00:00Z",
                    "content": {"text": "hello"},
                }],
            }]
        }
        result = adapters.parse_plugin_json(data, descriptor)
        self.assertEqual(result.parse_status, "exact")
        self.assertEqual(result.messages[0].conversation_id, "conversation-0")


class ProviderImportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        run_cli(CORE_CLI, "init", self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def receipts(self):
        path = self.workspace / "receipts" / "imports.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def observations(self):
        path = self.workspace / "staging" / "observations.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def set_file_limit(self, limit):
        policy_path = self.workspace / "policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["max_repo_file_bytes"] = limit
        policy_path.write_text(json.dumps(policy), encoding="utf-8")

    def test_gemini_import_stages_partial_receipt_and_validates(self):
        source = FIXTURES / "gemini" / "details-v1.json"
        result = json.loads(run_cli(PROVIDER_CLI, self.workspace, source, "--adapter", "gemini").stdout)
        self.assertEqual(result["adapter"], "gemini")
        self.assertEqual(result["parse_status"], "partial")
        self.assertEqual(result["observations_total"], 2)
        receipt = self.receipts()[0]
        self.assertEqual(receipt["adapter_layout"], "gemini-myactivity-mixed-v1")
        run_cli(CORE_CLI, "validate", self.workspace)

    def test_gemini_extracted_takeout_prefers_gemini_apps_activity(self):
        takeout = self.root / "Takeout"
        gemini = takeout / "Gemini Apps"
        other = takeout / "YouTube"
        gemini.mkdir(parents=True)
        other.mkdir(parents=True)
        shutil.copy(FIXTURES / "gemini" / "details-v1.json", gemini / "MyActivity.json")
        (other / "MyActivity.json").write_text(json.dumps([{"products": ["YouTube"]}]), encoding="utf-8")
        result = json.loads(run_cli(PROVIDER_CLI, self.workspace, takeout, "--adapter", "gemini").stdout)
        self.assertEqual(result["adapter"], "gemini")
        self.assertEqual(result["observations_total"], 2)
        run_cli(CORE_CLI, "validate", self.workspace)

    def test_provider_directory_candidate_obeys_per_file_limit(self):
        takeout = self.root / "Takeout" / "Gemini Apps"
        takeout.mkdir(parents=True)
        (takeout / "MyActivity.json").write_text("[" + (" " * 128) + "]", encoding="utf-8")
        self.set_file_limit(32)
        failed = run_cli(PROVIDER_CLI, self.workspace, self.root / "Takeout", "--adapter", "gemini", expect=2)
        self.assertIn("max_repo_file_bytes", failed.stderr)
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.observations(), [])

    def test_grok_import_excludes_thinking_trace_and_validates(self):
        source = FIXTURES / "grok" / "thinking-trace-v1.json"
        result = json.loads(run_cli(PROVIDER_CLI, self.workspace, source, "--adapter", "grok").stdout)
        self.assertEqual(result["parse_status"], "partial")
        staged = json.dumps(self.observations(), sort_keys=True)
        self.assertIn("Visible synthetic answer", staged)
        self.assertNotIn("synthetic private reasoning", staged)
        self.assertNotIn("synthetic trace fragment", staged)
        run_cli(CORE_CLI, "validate", self.workspace)

    def test_browser_chat_html_and_text_can_share_workspace(self):
        html = FIXTURES / "browser-chat" / "role-attributes.html"
        text = FIXTURES / "browser-chat" / "role-prefixed.txt"
        first = json.loads(run_cli(PROVIDER_CLI, self.workspace, html, "--adapter", "browser-chat").stdout)
        second = json.loads(run_cli(PROVIDER_CLI, self.workspace, text, "--adapter", "browser-chat").stdout)
        self.assertEqual(first["parse_status"], "exact")
        self.assertEqual(second["parse_status"], "exact")
        self.assertEqual(len(self.observations()), 6)
        run_cli(CORE_CLI, "validate", self.workspace)

    def test_browser_chat_directory_excludes_dot_git_content(self):
        source = self.root / "browser"
        (source / ".git").mkdir(parents=True)
        (source / ".git" / "notes.md").write_text("User: private git note", encoding="utf-8")
        (source / "chat.txt").write_text("User: hello\nAssistant: hi", encoding="utf-8")
        result = json.loads(run_cli(PROVIDER_CLI, self.workspace, source, "--adapter", "browser-chat").stdout)
        self.assertEqual(result["observations_total"], 2)
        staged = json.dumps(self.observations(), sort_keys=True)
        self.assertNotIn("private git note", staged)
        run_cli(CORE_CLI, "validate", self.workspace)

    def test_plugin_descriptor_hash_is_bound_to_receipt_identity(self):
        source = FIXTURES / "plugin" / "synthetic-community-export.json"
        original_plugin = FIXTURES / "plugin" / "example-adapter.json"
        first = json.loads(run_cli(
            PROVIDER_CLI,
            self.workspace,
            source,
            "--adapter", "plugin",
            "--plugin", original_plugin,
        ).stdout)

        changed_plugin = self.root / "changed-plugin.json"
        descriptor = json.loads(original_plugin.read_text(encoding="utf-8"))
        descriptor["version"] = "1.0.1"
        changed_plugin.write_text(json.dumps(descriptor), encoding="utf-8")
        second = json.loads(run_cli(
            PROVIDER_CLI,
            self.workspace,
            source,
            "--adapter", "plugin",
            "--plugin", changed_plugin,
        ).stdout)

        self.assertNotEqual(first["receipt_id"], second["receipt_id"])
        self.assertTrue(all(receipt["requested_adapter"].startswith("plugin:") for receipt in self.receipts()))
        run_cli(CORE_CLI, "validate", self.workspace)

    def test_plugin_duplicate_messages_are_deduplicated_before_receipt(self):
        descriptor = FIXTURES / "plugin" / "example-adapter.json"
        source = self.root / "duplicate-export.json"
        message = {
            "message_id": "m1",
            "role": "human",
            "created_at": "2026-01-01T00:00:00Z",
            "content": {"text": "same"},
        }
        source.write_text(json.dumps({
            "conversations": [{
                "conversation_id": "c1",
                "title": "Dupes",
                "messages": [message, dict(message)],
            }]
        }), encoding="utf-8")
        result = json.loads(run_cli(
            PROVIDER_CLI,
            self.workspace,
            source,
            "--adapter", "plugin",
            "--plugin", descriptor,
        ).stdout)
        self.assertEqual(result["observations_total"], 1)
        self.assertEqual(self.receipts()[0]["observation_count"], 1)
        self.assertEqual(len(self.observations()), 1)
        run_cli(CORE_CLI, "validate", self.workspace)

    def test_plugin_zip_uses_declared_filename(self):
        import zipfile

        archive = self.root / "community.zip"
        source = FIXTURES / "plugin" / "synthetic-community-export.json"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("other.json", json.dumps({"not": "the source"}))
            handle.write(source, arcname="nested/synthetic-community-export.json")
        result = json.loads(run_cli(
            PROVIDER_CLI,
            self.workspace,
            archive,
            "--adapter", "plugin",
            "--plugin", FIXTURES / "plugin" / "example-adapter.json",
        ).stdout)
        self.assertEqual(result["observations_total"], 2)
        run_cli(CORE_CLI, "validate", self.workspace)


if __name__ == "__main__":
    unittest.main()
