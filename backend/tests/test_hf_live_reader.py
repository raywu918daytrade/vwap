from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from main import hf_live_reader


class HfLiveReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        hf_live_reader._LAST_CHECK.clear()
        hf_live_reader._LAST_MANIFEST_KEY.clear()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_downloads_snapshot_atomically(self):
        manifest = self.root / "live.json"
        manifest.write_text(json.dumps({
            "trading_date": "2026-09-26",
            "latest_minute": "2026-09-26 09:01:00",
            "files": {"m1_live": "db/m1_live/2026-09-26.parquet"},
        }))
        source = self.root / "source.parquet"
        source.write_bytes(b"parquet-data")
        with (
            patch.object(hf_live_reader, "_ROOT", self.root),
            patch.dict(os.environ, {"HF_REPO_ID": "owner/data", "HF_TOKEN": "token"}),
            patch("huggingface_hub.hf_hub_download", side_effect=[str(manifest), str(source)]),
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

    def test_unchanged_manifest_does_not_download_snapshot_again(self):
        manifest = self.root / "live.json"
        manifest.write_text(json.dumps({
            "trading_date": "2026-09-26",
            "latest_minute": "2026-09-26 09:01:00",
            "files": {"m1_live": "db/m1_live/2026-09-26.parquet"},
        }))
        source = self.root / "source.parquet"
        source.write_bytes(b"first")
        with (
            patch.object(hf_live_reader, "_ROOT", self.root),
            patch.dict(os.environ, {
                "HF_REPO_ID": "owner/data",
                "HF_TOKEN": "token",
                "HF_LIVE_REFRESH_SECONDS": "1",
            }),
            patch("huggingface_hub.hf_hub_download", side_effect=[str(manifest), str(source), str(manifest)]) as download,
            patch("main.hf_live_reader.time.monotonic", side_effect=[100, 100, 102, 102]),
        ):
            self.assertEqual(hf_live_reader.refresh_live_m1("2026-09-26"), (True, True))
            self.assertEqual(hf_live_reader.refresh_live_m1("2026-09-26"), (True, False))

        self.assertEqual(download.call_count, 3)
        self.assertEqual((self.root / "db/m1_live/2026-09-26.parquet").read_bytes(), b"first")

    def test_merges_next_minute_delta(self):
        first_manifest = self.root / "first.json"
        first_manifest.write_text(json.dumps({
            "trading_date": "2026-09-26",
            "latest_minute": "2026-09-26 09:01:00",
            "files": {
                "m1_live": "db/m1_live/2026-09-26.parquet",
                "m1_delta": "db/m1_delta/2026-09-26/0901.parquet",
            },
        }))
        second_manifest = self.root / "second.json"
        second_manifest.write_text(json.dumps({
            "trading_date": "2026-09-26",
            "latest_minute": "2026-09-26 09:02:00",
            "files": {
                "m1_live": "db/m1_live/2026-09-26.parquet",
                "m1_delta": "db/m1_delta/2026-09-26/0902.parquet",
            },
        }))
        columns = ["stock_id", "date", "open", "high", "low", "close", "volume"]
        full = self.root / "full.parquet"
        delta = self.root / "delta.parquet"
        pd.DataFrame([["2330", "2026-09-26 09:01:00", 1, 2, 1, 2, 3]], columns=columns).to_parquet(full)
        pd.DataFrame([["2330", "2026-09-26 09:02:00", 2, 3, 2, 3, 4]], columns=columns).to_parquet(delta)
        with (
            patch.object(hf_live_reader, "_ROOT", self.root),
            patch.dict(os.environ, {
                "HF_REPO_ID": "owner/data",
                "HF_TOKEN": "token",
                "HF_LIVE_REFRESH_SECONDS": "1",
            }),
            patch("huggingface_hub.hf_hub_download", side_effect=[
                str(first_manifest), str(full), str(second_manifest), str(delta),
            ]) as download,
            patch("main.hf_live_reader.time.monotonic", side_effect=[100, 100, 102, 102]),
        ):
            self.assertEqual(hf_live_reader.refresh_live_m1("2026-09-26"), (True, True))
            self.assertEqual(hf_live_reader.refresh_live_m1("2026-09-26"), (True, True))

        result = pd.read_parquet(self.root / "db/m1_live/2026-09-26.parquet")
        self.assertEqual(result["date"].tolist(), ["2026-09-26 09:01:00", "2026-09-26 09:02:00"])
        self.assertEqual(download.call_count, 4)

    def test_downloads_matching_signal_snapshot(self):
        manifest = self.root / "live.json"
        manifest.write_text(json.dumps({
            "trading_date": "2026-09-26",
            "latest_minute": "2026-09-26 09:01:00",
            "files": {
                "m1_live": "db/m1_live/2026-09-26.parquet",
                "signal_live": "db/signal_live/2026-09-26.json",
            },
        }))
        m1 = self.root / "source.parquet"
        m1.write_bytes(b"parquet-data")
        signal = self.root / "signal.json"
        signal.write_text(json.dumps({
            "trading_date": "2026-09-26",
            "latest_minute": "2026-09-26 09:01:00",
            "vwap": [],
        }))
        with (
            patch.object(hf_live_reader, "_ROOT", self.root),
            patch.dict(os.environ, {"HF_REPO_ID": "owner/data", "HF_TOKEN": "token"}),
            patch("huggingface_hub.hf_hub_download", side_effect=[str(manifest), str(m1), str(signal)]),
        ):
            self.assertEqual(hf_live_reader.refresh_live_m1("2026-09-26"), (True, True))
            payload = hf_live_reader.read_live_signals("2026-09-26")
        self.assertEqual(payload["latest_minute"], "2026-09-26 09:01:00")


if __name__ == "__main__":
    unittest.main()
