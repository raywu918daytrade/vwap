from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from data.resample import compute_m5_std
from pattern.vwap_activity import _open5_from_m1


class OpenFiveFromM1Test(unittest.TestCase):
    def test_matches_first_standard_m5_window(self):
        minutes = pd.date_range("2026-09-22 09:00:00", periods=6, freq="min")
        m1 = pd.DataFrame(
            {
                "stock_id": ["1303"] * len(minutes),
                "date": minutes,
                "open": [10, 11, 12, 13, 14, 15],
                "high": [11, 12, 13, 14, 15, 16],
                "low": [9, 10, 11, 12, 13, 14],
                "close": [10.5, 11.5, 12.5, 13.5, 14.5, 15.5],
                "volume": [100, 10, 20, 30, 40, 900],
            }
        )

        expected = compute_m5_std(m1)
        expected = expected[expected["date"] == pd.Timestamp("2026-09-22 09:05:00")].iloc[0]

        with patch("data.query.load_m1_live", return_value=m1):
            actual = _open5_from_m1("2026-09-22", {"1303"}).set_index("stock_id").loc["1303"]

        self.assertEqual(float(actual["open"]), float(expected["open"]))
        self.assertEqual(float(actual["high"]), float(expected["high"]))
        self.assertEqual(float(actual["low"]), float(expected["low"]))
        self.assertEqual(int(actual["volume"]), int(expected["volume"]))
        self.assertEqual(int(actual["volume"]), 200)

    def test_excludes_0905_minute_from_first_bar(self):
        m1 = pd.DataFrame(
            {
                "stock_id": ["1303", "1303"],
                "date": pd.to_datetime(["2026-09-22 09:04:00", "2026-09-22 09:05:00"]),
                "open": [10, 20],
                "high": [11, 21],
                "low": [9, 19],
                "close": [10.5, 20.5],
                "volume": [100, 999],
            }
        )

        with patch("data.query.load_m1_live", return_value=m1):
            actual = _open5_from_m1("2026-09-22", {"1303"}).iloc[0]

        self.assertEqual(int(actual["volume"]), 100)
        self.assertEqual(float(actual["high"]), 11.0)


if __name__ == "__main__":
    unittest.main()
