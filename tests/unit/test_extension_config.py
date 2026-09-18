"""F05: generic extension configuration store and JSON-Schema-subset validator."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_assistant.core.extensions.config import (
    ExtensionConfigError,
    validate_extension_config,
)
from personal_assistant.infrastructure.extensions.config_store import (
    FileExtensionConfigStore,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "roots": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 512},
                    "label": {"type": "string", "maxLength": 64},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        "include_hidden": {"type": "boolean"},
    },
    "required": ["roots"],
    "additionalProperties": False,
}


class ConfigValidationTests(unittest.TestCase):
    def test_valid_config_round_trips(self) -> None:
        config = {"roots": [{"path": "C:/notes", "label": "notes"}], "include_hidden": False}
        self.assertEqual(config, validate_extension_config(SCHEMA, config))

    def test_missing_required_root_is_rejected(self) -> None:
        with self.assertRaises(ExtensionConfigError) as captured:
            validate_extension_config(SCHEMA, {})
        self.assertIn("roots", str(captured.exception))

    def test_empty_roots_are_rejected(self) -> None:
        with self.assertRaises(ExtensionConfigError):
            validate_extension_config(SCHEMA, {"roots": []})

    def test_unknown_property_is_rejected(self) -> None:
        with self.assertRaises(ExtensionConfigError):
            validate_extension_config(
                SCHEMA, {"roots": [{"path": "x", "extra": 1}]}
            )

    def test_wrong_type_is_rejected(self) -> None:
        with self.assertRaises(ExtensionConfigError):
            validate_extension_config(SCHEMA, {"roots": "C:/notes"})

    def test_non_object_config_is_rejected(self) -> None:
        with self.assertRaises(ExtensionConfigError):
            validate_extension_config(SCHEMA, ["C:/notes"])

    def test_null_schema_accepts_any_json_object(self) -> None:
        self.assertEqual({"a": [1, 2]}, validate_extension_config(None, {"a": [1, 2]}))

    def test_unsupported_schema_keyword_fails_closed(self) -> None:
        schema = {"type": "object", "oneOf": [{"type": "object"}]}
        with self.assertRaises(ExtensionConfigError):
            validate_extension_config(schema, {})

    def test_unsupported_nested_keyword_fails_even_when_property_is_absent(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "optional": {"type": "string", "oneOf": [{"type": "string"}]}
            },
        }
        with self.assertRaises(ExtensionConfigError):
            validate_extension_config(schema, {})

    def test_non_finite_numbers_are_not_json_configuration(self) -> None:
        schema = {
            "type": "object",
            "properties": {"timeout": {"type": "number", "minimum": 1, "maximum": 10}},
        }
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ExtensionConfigError):
                validate_extension_config(schema, {"timeout": value})

    def test_oversized_config_is_rejected(self) -> None:
        schema = {
            "type": "object",
            "properties": {"blob": {"type": "string", "maxLength": 10_000_000}},
        }
        with self.assertRaises(ExtensionConfigError):
            validate_extension_config(schema, {"blob": "x" * 70_000})

    def test_pattern_and_bounds_are_enforced(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["full", "incremental"]},
                "workers": {"type": "integer", "minimum": 1, "maximum": 4},
            },
        }
        with self.assertRaises(ExtensionConfigError):
            validate_extension_config(schema, {"mode": "partial"})
        with self.assertRaises(ExtensionConfigError):
            validate_extension_config(schema, {"workers": 9})


class FileConfigStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f05_config_"))
        self.store = FileExtensionConfigStore(self.tmp)

    def tearDown(self) -> None:
        resolved = self.tmp.resolve()
        if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise AssertionError(f"refusing to delete {resolved}")
        shutil.rmtree(resolved, ignore_errors=True)

    async def test_missing_config_returns_empty_object(self) -> None:
        self.assertEqual({}, await self.store.get("org.example.knowledge"))

    async def test_round_trip_persists_json(self) -> None:
        config = {"roots": [{"path": "C:/notes"}]}
        await self.store.save("org.example.knowledge", config)
        self.assertEqual(config, await self.store.get("org.example.knowledge"))
        stored = json.loads((self.tmp / "org.example.knowledge.json").read_text("utf-8"))
        self.assertEqual(config, stored)

    async def test_path_traversal_extension_id_is_rejected(self) -> None:
        with self.assertRaises(ExtensionConfigError):
            await self.store.save("../escape", {"roots": []})
        with self.assertRaises(ExtensionConfigError):
            await self.store.get("..\\escape")

    async def test_corrupt_file_is_a_typed_error(self) -> None:
        (self.tmp / "org.example.knowledge.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ExtensionConfigError):
            await self.store.get("org.example.knowledge")

    async def test_store_rejects_non_finite_json_even_when_called_directly(self) -> None:
        with self.assertRaises(ExtensionConfigError):
            await self.store.save("org.example.knowledge", {"timeout": float("nan")})

    async def test_oversized_stored_file_is_rejected(self) -> None:
        (self.tmp / "org.example.knowledge.json").write_text(
            json.dumps({"blob": "x" * 70_000}), encoding="utf-8"
        )
        with self.assertRaises(ExtensionConfigError):
            await self.store.get("org.example.knowledge")

    async def test_concurrent_saves_use_independent_temporary_files(self) -> None:
        real_replace = os.replace
        temporary_paths: list[str] = []
        replacement_lock = threading.Lock()
        active_replacements = 0
        maximum_active = 0

        def synchronized_replace(source: object, destination: object) -> None:
            nonlocal active_replacements, maximum_active
            temporary_paths.append(os.fspath(source))
            with replacement_lock:
                active_replacements += 1
                maximum_active = max(maximum_active, active_replacements)
            try:
                time.sleep(0.05)
                real_replace(source, destination)
            finally:
                with replacement_lock:
                    active_replacements -= 1

        left = {"roots": [{"path": "C:/left"}]}
        right = {"roots": [{"path": "C:/right"}]}
        with patch(
            "personal_assistant.infrastructure.extensions.config_store.os.replace",
            side_effect=synchronized_replace,
        ):
            await asyncio.gather(
                self.store.save("org.example.knowledge", left),
                self.store.save("org.example.knowledge", right),
            )
        self.assertEqual(2, len(set(temporary_paths)))
        self.assertEqual(1, maximum_active)
        self.assertIn(await self.store.get("org.example.knowledge"), (left, right))
        self.assertEqual([], list(self.tmp.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
