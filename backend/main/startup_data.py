"""Startup data preparation for realtime market-data collection.

This module owns the data that must be ready before the live M1 collector is
useful: a fresh enough local market DB and the Fubon subscription universe.
"""

from __future__ import annotations

from fubon.subscribe_list import build_and_save_subscribe_list

_MARKET_DB_SYNC_FOLDERS = [
    "m1",
    "m5_std",
    "d1",
    "adjustment_day",
    "tick",
    "tickers",
    "volume_profile",
    "poc_day",
    "tick_adjust_factor",
    "adjustment_factor",
    "m1_flags",
    "d1_flags",
    "adjustment_day_flags",
    "tick_flags",
]


def _latest_market_db_check_date(now) -> str:
    """Return the trading date whose D1 flag should exist before live startup.

    During a trading day before the official daily data is expected, and during
    weekends, the latest usable historical date is the prior expected trading
    day. The helper only handles weekends; official exchange holidays still
    fail safely by causing an HF freshness check.
    """
    from data.day_data_loader import _expected_prior_trading_day

    if now.weekday() >= 5 or (now.hour, now.minute) < (13, 30):
        return _expected_prior_trading_day(now)
    return now.strftime("%Y-%m-%d")


def sync_local_market_db_from_hf_if_stale() -> None:
    """Pull the latest market DB snapshot from HF when local daily data is stale.

    `live_trader` calls this during startup. The freshness check uses the D1
    completion flag for stock 0050 on the expected latest trading day. When that
    flag is missing, the local DB is probably behind the external HF dataset,
    so we mirror that dataset before realtime M1 collection begins.
    """
    from datetime import datetime, timedelta, timezone

    from data.day_data_loader import _get_done_stocks

    tw = timezone(timedelta(hours=8))
    now = datetime.now(tw)
    check_date = _latest_market_db_check_date(now)

    done = _get_done_stocks(check_date)
    if "0050" in done:
        print(f"[HF同步檢查] 0050 {check_date} 的 d1 flag 已存在，本機資料新鮮，跳過下載", flush=True)
        return

    print(f"[HF同步檢查] 0050 {check_date} 沒有 d1 flag，本機資料可能落後，從 HF Hub 同步保留資料夾...", flush=True)
    try:
        from scripts.sync_market_db_from_hf import sync_market_db_from_hf

        sync_market_db_from_hf(only=_MARKET_DB_SYNC_FOLDERS)
        print("[HF同步檢查] 下載完成", flush=True)
    except Exception as exc:
        print(f"[HF同步檢查] 下載失敗，改用現有本機資料繼續開機: {exc}", flush=True)


def refresh_fubon_subscription_universe(state) -> None:
    """Rebuild the realtime Fubon subscription universe and store it on state."""
    df = build_and_save_subscribe_list()
    if df.empty:
        print("  警告：無法取得候選股清單（非盤中或富邦 API 失敗），不過濾股票", flush=True)
        state.tickers = {}
        state.day_trade_stocks = None
        return
    state.tickers = df.set_index("stock_id")["name"].to_dict()
    state.day_trade_stocks = set(state.tickers.keys()) or None
