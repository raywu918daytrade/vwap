from __future__ import annotations

import unittest

import pandas as pd

from trade_stats_api import _attach_macd, _candidates, _simulate, _summary


def _bars(rows):
    return pd.DataFrame(rows, columns=["hhmm", "open", "high", "low", "close"])


GOOD = {"day_atr": 0.06, "vol5_pr": 0.7}


class CandidatesTest(unittest.TestCase):
    def test_keeps_first_sr_signal_per_stock_on_both_sides(self):
        rows = [
            {"stock_id": "1111", "time": "10:30", "sr_kind": "support", "vwap_dir": "down"},
            {"stock_id": "1111", "time": "10:05", "sr_kind": "support", "vwap_dir": "down"},
            {"stock_id": "2222", "time": "09:50", "sr_kind": "resistance", "vwap_dir": "down"},
            {"stock_id": "3333", "time": "13:00", "sr_kind": "resistance", "vwap_dir": "up"},
            {"stock_id": "4444", "time": "13:24", "sr_kind": "support", "vwap_dir": "down"},
            {"stock_id": "5555", "time": "11:00", "sr_kind": "support", "vwap_dir": "down"},
            {"stock_id": "6666", "time": "09:01", "sr_kind": "both", "vwap_dir": "up"},
        ]
        activity = {sid: GOOD for sid in ("1111", "2222", "3333", "4444", "6666")}
        activity["5555"] = {"day_atr": 0.04, "vol5_pr": 0.9}

        picked = _candidates(rows, activity)

        self.assertEqual(
            [(c["stock_id"], c["signal_time"], c["side"]) for c in picked],
            [("6666", "09:01", "long"), ("1111", "10:05", "short"), ("3333", "13:00", "long")],
        )

    def test_macd_vwap_needs_matching_recent_divergence(self):
        rows = [
            {"stock_id": "1111", "time": "09:40", "direction": "up"},    # bull div 09:20 -> long
            {"stock_id": "2222", "time": "10:00", "direction": "up"},    # latest div is bear -> skip
            {"stock_id": "2222", "time": "10:15", "direction": "down"},  # bear div 09:50 -> short
            {"stock_id": "3333", "time": "11:00", "direction": "up"},    # div 40 min old -> skip
            {"stock_id": "4444", "time": "09:30", "direction": "down"},  # no div
        ]
        macd = {
            "1111": {"events": [{"kind": "bull", "time": "09:20"}]},
            "2222": {"events": [{"kind": "bull", "time": "09:30"}, {"kind": "bear", "time": "09:50"}]},
            "3333": {"events": [{"kind": "bull", "time": "10:20"}]},
        }
        activity = {sid: GOOD for sid in ("1111", "2222", "3333", "4444")}

        picked = _candidates(_attach_macd(rows, macd, 30), activity, "macd_vwap")

        self.assertEqual(
            [(c["stock_id"], c["signal_time"], c["side"], c["macd_time"]) for c in picked],
            [("1111", "09:40", "long", "09:20"), ("2222", "10:15", "short", "09:50")],
        )

    def test_vwap_cross_uses_direction_all_day(self):
        rows = [
            {"stock_id": "1111", "time": "09:03", "direction": "up"},
            {"stock_id": "1111", "time": "09:01", "direction": "down"},
            {"stock_id": "2222", "time": "13:10", "direction": "up"},
            {"stock_id": "3333", "time": "13:24", "direction": "up"},
        ]
        activity = {sid: GOOD for sid in ("1111", "2222", "3333")}

        picked = _candidates(rows, activity, "vwap_cross")

        self.assertEqual(
            [(c["stock_id"], c["signal_time"], c["side"]) for c in picked],
            [("1111", "09:01", "short"), ("2222", "13:10", "long")],
        )


    def test_sr_short_slots_keeps_short_signals_in_best_slots(self):
        rows = [
            {"stock_id": "1111", "time": "10:05", "sr_kind": "support", "vwap_dir": "down"},
            {"stock_id": "2222", "time": "10:20", "sr_kind": "support", "vwap_dir": "down"},
            {"stock_id": "3333", "time": "11:10", "sr_kind": "support", "vwap_dir": "down"},
            {"stock_id": "4444", "time": "10:50", "sr_kind": "resistance", "vwap_dir": "up"},
            {"stock_id": "5555", "time": "11:15", "sr_kind": "support", "vwap_dir": "down"},
        ]
        activity = {sid: GOOD for sid in ("1111", "2222", "3333", "4444", "5555")}

        picked = _candidates(rows, activity, "sr_short_slots")

        self.assertEqual([(c["stock_id"], c["side"]) for c in picked], [("1111", "short"), ("3333", "short")])


class SimulateTest(unittest.TestCase):
    cand = {"stock_id": "1111", "signal_time": "10:05"}

    def test_take_profit(self):
        bars = _bars([("10:05", 100, 100, 99, 100), ("10:06", 100, 100.5, 99.5, 99.8), ("10:07", 99.8, 99.9, 97.9, 98)])
        trade = _simulate(self.cand, bars, session_closed=True)
        self.assertEqual((trade["status"], trade["entry_time"], trade["exit_time"]), ("停利", "10:06", "10:07"))
        self.assertAlmostEqual(trade["gross_pct"], 2.0)

    def test_stop_wins_when_one_bar_hits_both(self):
        bars = _bars([("10:05", 100, 100, 99, 100), ("10:06", 100, 104.5, 97, 101)])
        trade = _simulate(self.cand, bars, session_closed=True)
        self.assertEqual(trade["status"], "停損")
        self.assertAlmostEqual(trade["gross_pct"], -4.0)

    def test_long_take_profit_and_stop(self):
        cand = {"stock_id": "1111", "signal_time": "10:05", "side": "long"}
        bars = _bars([("10:05", 100, 100, 99, 100), ("10:06", 100, 100.5, 99.5, 99.8), ("10:07", 99.8, 102.1, 99.5, 102)])
        trade = _simulate(cand, bars, session_closed=True)
        self.assertEqual(trade["status"], "停利")
        self.assertAlmostEqual(trade["gross_pct"], 2.0)
        bars = _bars([("10:05", 100, 100, 99, 100), ("10:06", 100, 102.5, 95.9, 96)])
        trade = _simulate(cand, bars, session_closed=True)
        self.assertEqual(trade["status"], "停損")
        self.assertAlmostEqual(trade["gross_pct"], -4.0)

    def test_open_position_mid_session(self):
        bars = _bars([("10:05", 100, 100, 99, 100), ("10:06", 100, 100.5, 99.5, 99)])
        trade = _simulate(self.cand, bars, session_closed=False)
        self.assertEqual(trade["status"], "持有中")
        self.assertAlmostEqual(trade["gross_pct"], 1.0)

    def test_closes_at_last_bar(self):
        bars = _bars([("10:05", 100, 100, 99, 100), ("10:06", 100, 100.5, 99.5, 99), ("13:24", 99, 99.5, 98.5, 99.5)])
        trade = _simulate(self.cand, bars, session_closed=False)
        self.assertEqual((trade["status"], trade["exit_time"]), ("收盤平倉", "13:24"))
        self.assertAlmostEqual(trade["gross_pct"], 0.5)

    def test_waiting_for_entry_bar(self):
        bars = _bars([("10:05", 100, 100, 99, 100)])
        self.assertEqual(_simulate(self.cand, bars, session_closed=False)["status"], "等待進場")


class SummaryTest(unittest.TestCase):
    def test_counts_only_closed_trades_in_win_rate(self):
        trades = [
            {"status": "停利", "net_pct": 1.565},
            {"status": "停損", "net_pct": -4.435},
            {"status": "持有中", "net_pct": 0.2},
        ]
        summary = _summary(trades)
        self.assertEqual((summary["closed"], summary["open"], summary["wins"], summary["win_rate"]), (2, 1, 1, 50.0))
        self.assertAlmostEqual(summary["total_net_pct"], -2.87)


if __name__ == "__main__":
    unittest.main()
