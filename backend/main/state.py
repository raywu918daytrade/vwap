"""Shared runtime state for the slim realtime monitor."""

from __future__ import annotations

import threading


class AppState:
    def __init__(self):
        # Ticker list used by the Fubon realtime collector and display names.
        self.tickers: dict[str, str] = {}
        self.day_trade_stocks: set[str] | None = None

        # Daily support/resistance levels used by the VWAP panel.
        self.sr_levels: dict[str, tuple[float | None, float | None]] = {}
        self.sr_levels_date: str = ""
        self.sr_prev_close: dict[str, float] = {}
        self.vwap_crossed_today: set[str] = set()

        # Set by fubon.marketdata_ws.FubonM1Collector after it finishes filling
        # the M1 gap between market open and the current WebSocket connection.
        self.backfill_done = threading.Event()
