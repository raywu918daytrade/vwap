"""Prepare the HF/TiDB-backed datasets required by the dashboard reader."""

from __future__ import annotations

_OFFLINE_SIGNAL_FOLDERS = ["pattern_scan", "vwap_activity", "vwap_signals"]
_RUNTIME_QUERY_FOLDERS = [*_OFFLINE_SIGNAL_FOLDERS, "tickers"]


def _tidb_offline_reads_enabled() -> bool:
    try:
        from data.tidb_offline_store import tidb_reads_enabled

        return tidb_reads_enabled()
    except Exception:
        return False


def warm_tidb_query_connection() -> bool:
    """Warm one pooled TiDB connection so the first chart click stays fast."""
    try:
        from data.tidb_offline_store import warm_tidb_read_pool

        warmed = warm_tidb_read_pool()
        if warmed:
            print("[TiDB] 查詢連線已預熱", flush=True)
        return warmed
    except Exception as exc:
        print(f"[TiDB] 查詢連線預熱失敗，首次查詢時重試: {exc}", flush=True)
        return False


def _latest_market_db_check_date(now) -> str:
    """Return the trading date whose D1 flag should exist before live startup.

    During a trading day before the official daily data is expected, and during
    weekends, the latest usable historical date is the prior expected trading
    day. The helper only handles weekends; official exchange holidays still
    fail safely by causing an HF freshness check.
    """
    from datetime import timedelta

    if now.weekday() >= 5 or (now.hour, now.minute) < (13, 30):
        candidate = now.date() - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate.isoformat()
    return now.strftime("%Y-%m-%d")


def latest_market_db_check_date(now=None) -> str:
    """Public wrapper for the date that daily HF sync should be fresh through."""
    if now is None:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone(timedelta(hours=8)))
    return _latest_market_db_check_date(now)


def offline_signal_date_available(date_str: str) -> bool:
    """Return whether the GHA-produced signal bundle exists locally."""
    try:
        from pattern.vwap_signal_store import available_signal_dates

        return str(date_str)[:10] in set(available_signal_dates())
    except Exception:
        return False


def sync_offline_signal_shards_from_hf() -> bool:
    """Pull small GHA-produced signal shards even when raw market DB is fresh."""
    if _tidb_offline_reads_enabled():
        print("[HF同步檢查] TiDB 查詢已啟用，跳過 pattern/activity/signals parquet 同步", flush=True)
        return False
    try:
        from scripts.sync_market_db_from_hf import sync_market_db_from_hf

        sync_market_db_from_hf(only=_OFFLINE_SIGNAL_FOLDERS, prune=False)
        print("[HF同步檢查] 離線訊號檔已同步", flush=True)
        return True
    except Exception as exc:
        print(f"[HF同步檢查] 離線訊號檔同步失敗，沿用現有檔案: {exc}", flush=True)
        return False


def sync_runtime_query_data_from_hf() -> bool:
    """Pull only precomputed API inputs needed by on-demand deployments."""
    try:
        from scripts.sync_market_db_from_hf import sync_market_db_from_hf

        folders = ["tickers"] if _tidb_offline_reads_enabled() else _RUNTIME_QUERY_FOLDERS
        sync_market_db_from_hf(only=folders, prune=False)
        from main.hf_on_demand import prune_month_cache

        prune_month_cache()
        if folders == ["tickers"]:
            print("[HF同步檢查] TiDB 查詢已啟用，僅同步股票清單", flush=True)
        else:
            print("[HF同步檢查] 離線結果與股票清單已同步", flush=True)
        return True
    except Exception as exc:
        print(f"[HF同步檢查] 小型同步失敗，沿用現有檔案: {exc}", flush=True)
        return False


def clear_market_query_caches() -> None:
    """Clear in-memory query caches after HF overwrites historical DB files."""
    from pattern.activity_store import clear_cache as clear_activity_store_cache
    from pattern.pattern_api import clear_pattern_cache, reload_universe_cache
    from pattern.vwap_activity import clear_cache as clear_activity_cache
    from pattern.vwap_macd_div import clear_cache as clear_macd_cache
    from pattern.vwap_obv_div import clear_cache as clear_obv_cache
    from pattern.vwap_signal_store import clear_cache as clear_signal_store_cache
    from pattern.vwap_sr_scan import clear_caches as clear_vwap_sr_caches

    clear_pattern_cache()
    reload_universe_cache()
    clear_activity_store_cache()
    clear_signal_store_cache()
    clear_vwap_sr_caches()
    clear_activity_cache()
    clear_macd_cache()
    clear_obv_cache()
    print("[HF同步檢查] 歷史查詢快取已清空", flush=True)
