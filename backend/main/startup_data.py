"""Startup data preparation for realtime market-data collection.

This module owns the data that must be ready before the live M1 collector is
useful: a fresh enough local market DB and the Fubon subscription universe.
"""

from __future__ import annotations

from fubon.subscribe_list import build_and_save_subscribe_list


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


def sync_local_market_db_from_hf_if_stale() -> str:
    """Pull the latest market DB snapshot from HF when local daily data is stale.

    `live_trader` calls this during startup. The freshness check uses the D1
    completion flag for stock 0050 on the expected latest trading day. When that
    flag is missing, the local DB is probably behind the external HF dataset,
    so we mirror that dataset before realtime M1 collection begins.

    Returns:
        "synced" when HF download finished successfully, "fresh" when local
        data already passed the freshness check, or "failed" when the download
        failed and the caller should continue with existing local files.
    """
    from datetime import datetime, timedelta, timezone

    from data.day_data_loader import _get_done_stocks

    tw = timezone(timedelta(hours=8))
    now = datetime.now(tw)
    check_date = _latest_market_db_check_date(now)

    done = _get_done_stocks(check_date)
    if "0050" in done:
        print(f"[HF同步檢查] 0050 {check_date} 的 d1 flag 已存在，本機資料新鮮，跳過下載", flush=True)
        return "fresh"

    print(f"[HF同步檢查] 0050 {check_date} 沒有 d1 flag，本機資料可能落後，從 HF Hub 同步保留資料夾...", flush=True)
    try:
        from scripts.sync_market_db_from_hf import sync_market_db_from_hf

        sync_market_db_from_hf()
        print("[HF同步檢查] 下載完成", flush=True)
        return "synced"
    except Exception as exc:
        print(f"[HF同步檢查] 下載失敗，改用現有本機資料繼續開機: {exc}", flush=True)
        return "failed"


def clear_market_query_caches() -> None:
    """Clear in-memory query caches after HF overwrites historical DB files."""
    from pattern.pattern_api import clear_pattern_cache, reload_universe_cache
    from pattern.vwap_activity import clear_cache as clear_activity_cache
    from pattern.vwap_macd_div import clear_cache as clear_macd_cache
    from pattern.vwap_obv_div import clear_cache as clear_obv_cache
    from pattern.vwap_sr_scan import clear_caches as clear_vwap_sr_caches

    clear_pattern_cache()
    reload_universe_cache()
    clear_vwap_sr_caches()
    clear_activity_cache()
    clear_macd_cache()
    clear_obv_cache()
    print("[HF同步檢查] 歷史查詢快取已清空", flush=True)


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
