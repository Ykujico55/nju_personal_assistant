from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from personal_assistant.domain import Sensitivity
from personal_assistant.infrastructure.filesystem import LocalArtifactBlobStore


class ArtifactStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_content_is_addressed_and_verified_without_exposing_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalArtifactBlobStore(Path(temporary))
            first = await store.put(
                b"managed content",
                media_type="text/plain",
                sensitivity=Sensitivity.PERSONAL,
            )
            second = await store.put(
                b"managed content",
                media_type="text/plain",
                sensitivity=Sensitivity.PERSONAL,
            )
            self.assertEqual(first.storage_key, second.storage_key)
            self.assertNotIn(str(Path(temporary)), first.storage_key)
            self.assertEqual(b"managed content", await store.read(first))
            await store.delete(first)
            self.assertEqual(b"managed content", await store.read(second))
            await store.delete(second)
            with self.assertRaises(FileNotFoundError):
                await store.read(second)


if __name__ == "__main__":
    unittest.main()
