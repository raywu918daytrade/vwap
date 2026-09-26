from __future__ import annotations

import unittest
from unittest.mock import patch

import api


class LiveSignalReplaceTest(unittest.TestCase):
    def test_replaces_all_live_signal_maps_together(self):
        payload = {
            "trading_date": "2026-09-26",
            "latest_minute": "2026-09-26 09:01:00",
            "vwap": [{"stock_id": "2330", "time": "09:01"}],
            "sr": [{"stock_id": "2317", "time": "09:01"}],
            "chg": {"2330": 1.25},
            "macd": {"2330": {"events": []}},
            "obv": {"2317": {"events": []}},
            "activity": {"2330": {"day_atr": 0.02, "open5_rng": 0.01, "vol5_pr": 0.8}},
        }
        with patch.object(api, "_today_str", return_value="2026-09-26"):
            self.assertTrue(api.replace_live_signals(payload))

        self.assertEqual(api._vwap_breakout_signals, payload["vwap"])
        self.assertEqual(api._sr_vwap_cross_signals, payload["sr"])
        self.assertEqual(api._vwap_chg, payload["chg"])
        self.assertEqual(api._vwap_macd_live, payload["macd"])
        self.assertEqual(api._vwap_obv_live, payload["obv"])
        self.assertEqual(api._vwap_activity_live, payload["activity"])

    def test_rejects_wrong_trading_date(self):
        with patch.object(api, "_today_str", return_value="2026-09-26"):
            self.assertFalse(api.replace_live_signals({"trading_date": "2026-09-25"}))

    def test_render_catchup_never_runs_local_scan(self):
        api._vwap_breakout_signals[:] = [{"stock_id": "2330"}]
        api._sr_vwap_cross_signals[:] = [{"stock_id": "2317"}]
        with (
            patch.object(api, "is_render_reader", return_value=True),
            patch("main.hf_live_reader.read_live_signals", return_value=None),
        ):
            vwap, sr = api._catchup_today_into_memory()
        self.assertEqual(vwap, [{"stock_id": "2330"}])
        self.assertEqual(sr, [{"stock_id": "2317"}])


if __name__ == "__main__":
    unittest.main()
