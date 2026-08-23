import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec"
FIXTURES = ROOT / "fixtures" / "conformance"

CASES = {
    "policy": "policy.schema.json",
    "import-receipt": "import-receipt.schema.json",
    "observation": "observation.schema.json",
    "memory-record": "memory-record.schema.json",
    "bundle": "bundle.schema.json",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class SchemaConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {name: load_json(SPEC / filename) for name, filename in CASES.items()}
        registry = Registry()
        for schema in cls.schemas.values():
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        cls.registry = registry

    def validator(self, name):
        return Draft202012Validator(
            self.schemas[name],
            registry=self.registry,
            format_checker=FormatChecker(),
        )

    def test_protocol_schemas_are_valid_draft_2020_12(self):
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)

    def test_valid_standalone_fixtures_conform(self):
        for name in CASES:
            with self.subTest(fixture=name):
                instance = load_json(FIXTURES / "valid" / f"{name}.json")
                self.validator(name).validate(instance)

    def test_invalid_standalone_fixtures_are_rejected(self):
        for name in CASES:
            with self.subTest(fixture=name):
                instance = load_json(FIXTURES / "invalid" / f"{name}.json")
                with self.assertRaises(ValidationError):
                    self.validator(name).validate(instance)


if __name__ == "__main__":
    unittest.main()
