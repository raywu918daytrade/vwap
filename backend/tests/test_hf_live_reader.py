from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import hf_live_reader


class HfLiveReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        hf_live_reader._LAST_CHECK.clear()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_downloads_snapshot_atomically(self):
        source = self.root / "source.parquet"
        source.write_bytes(b"parquet-data")
        with (
            patch.object(hf_live_reader, "_ROOT", self.root),
            patch.dict(os.environ, {"HF_REPO_ID": "owner/data", "HF_TOKEN": "token"}),
            patch("huggingface_hub.hf_hub_download", return_value=str(source)),
        ):
            self.assertTrue(hf_live_reader.sync_live_m1("2026-09-26"))

        target = self.root / "db/m1_live/2026-09-26.parquet"
        self.assertEqual(target.read_bytes(), b"parquet-data")
        self.assertFalse(target.with_suffix(".parquet.tmp").exists())

    def test_keeps_existing_snapshot_when_hf_is_unavailable(self):
        target = self.root / "db/m1_live/2026-09-26.parquet"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"cached")
        with (
            patch.object(hf_live_reader, "_ROOT", self.root),
            patch.dict(os.environ, {"HF_REPO_ID": "owner/data", "HF_TOKEN": "token"}),
            patch("huggingface_hub.hf_hub_download", side_effect=RuntimeError("offline")),
        ):
            self.assertTrue(hf_live_reader.sync_live_m1("2026-09-26"))

        self.assertEqual(target.read_bytes(), b"cached")


if __name__ == "__main__":
    unittest.main()
