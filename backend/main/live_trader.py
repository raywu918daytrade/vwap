"""Realtime entry point for the slim pattern/VWAP monitor."""

from __future__ import annotations

import builtins as _builtins
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import uvicorn

from api import (
    append_system_log as _log_sys,
    clear_vwap_bundle_cache as _clear_vwap_bundle_cache,
    get_uvicorn_config,
    push_candles,
    push_quote,
    push_sr_vwap_cross,
    push_vwap_breakout,
    push_vwap_chg,
    push_vwap_macd_div,
    push_vwap_obv_div,
    register_vwap_sr_catchup_hook,
    vwap_sr_catchup as _vwap_sr_catchup,
)
from data.query import load_m1_live
from main import collector as _collector
from main import startup_data as _startup_data
from main.config import (
    CACHE_PREWARM_CHART_ROWS,
    CACHE_PREWARM_CHART_DATES,
    CACHE_PREWARM_CHART_MONTHS,
    CACHE_PREWARM_CHART_PAUSE_SEC,
    CACHE_PREWARM_DAY_ATR,
    CACHE_PREWARM_MONTHS,
    CACHE_PREWARM_STOCKS,
    CACHE_PREWARM_STARTUP_CHART_DATES,
    CACHE_PREWARM_STARTUP_LIST_DATES,
    CACHE_PREWARM_STARTUP_MONTHS,
    CACHE_PREWARM_VOL5_PR,
    HF_DAILY_SYNC_HOUR,
    HF_DAILY_SYNC_MIN,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MIN,
    WATCHLIST_QUOTES,
)
from main.state import AppState

_TW = timezone(timedelta(hours=8))
_orig_print = _builtins.print


def _ts_print(*args, **kwargs):
    ts = datetime.now(_TW).strftime("%H:%M:%S")
    _orig_print(f"[{ts}]", *args, **kwargs)


_builtins.print = _ts_print

state = AppState()
_startup_done = threading.Event()
_last_vwap_catchup_date = ""
_prev_close_cache: dict[str, tuple[str, float]] = {}
_prewarm_lock = threading.Lock()
_prewarm_active = False


def _on_vwap_sr_catchup(vwap: list, sr: list) -> None:
    for item in vwap:
        state.vwap_crossed_today.add(str(item.get("stock_id", "")))
    for item in sr:
        state.vwap_crossed_today.add(str(item.get("stock_id", "")))


register_vwap_sr_catchup_hook(_on_vwap_sr_catchup)


def _startup() -> None:
    """Sync local historical data from HF, then prepare realtime subscriptions."""
    global _last_hf_sync_date, _next_hf_sync_retry_at
    try:
        sync_status = _startup_data.sync_local_market_db_from_hf_if_stale()
        now = datetime.now(_TW)
        if (now.hour, now.minute) >= (HF_DAILY_SYNC_HOUR, HF_DAILY_SYNC_MIN):
            expected_signal_date = _startup_data.latest_market_db_check_date(now)
            if sync_status in ("synced", "signals_synced", "fresh") and _startup_data.offline_signal_date_available(expected_signal_date):
                _last_hf_sync_date = now.date()
            else:
                _next_hf_sync_retry_at = now + timedelta(minutes=30)
        if sync_status in ("synced", "signals_synced"):
            _clear_after_hf_sync()
        _start_cache_prewarm("startup")
        print("載入富邦即時訂閱清單...", flush=True)
        _startup_data.load_fubon_subscription_universe(state)
        print(f"  即時訂閱標的：{len(state.tickers)} 支", flush=True)
        _log_sys(f"即時訂閱清單就緒：{len(state.tickers)} 支")
        if _collector.within_collection_window(now):
            _ensure_sr_vwap_day(now.strftime("%Y-%m-%d"))
        else:
            print("盤後啟動：略過只供即時盤勢使用的 SR 水位載入", flush=True)
        print("就緒：等待 M1 即時資料與 VWAP/型態事件", flush=True)
    except Exception as exc:
        print(f"啟動資料準備失敗，仍啟動 API/collector: {exc}", flush=True)
        _log_sys(f"啟動資料準備失敗: {exc}", "error")
    finally:
        _startup_done.set()


def _clear_after_hf_sync() -> None:
    """Refresh in-process caches after HF updates local historical data."""
    global _last_vwap_catchup_date
    _startup_data.clear_market_query_caches()
    _clear_vwap_bundle_cache()
    _prev_close_cache.clear()
    _last_vwap_catchup_date = ""


def _start_cache_prewarm(reason: str) -> None:
    """Warm historical caches in the background without blocking API startup."""
    month_limit = CACHE_PREWARM_STARTUP_MONTHS if reason == "startup" else CACHE_PREWARM_MONTHS
    list_date_limit = CACHE_PREWARM_STARTUP_LIST_DATES if reason == "startup" else 0
    chart_date_limit = CACHE_PREWARM_STARTUP_CHART_DATES if reason == "startup" else CACHE_PREWARM_CHART_DATES
    chart_month_limit = min(CACHE_PREWARM_CHART_MONTHS, month_limit)
    if reason == "startup" and chart_date_limit <= 0:
        chart_month_limit = 0
    if month_limit <= 0:
        return

    def _run() -> None:
        global _prewarm_active
        with _prewarm_lock:
            if _prewarm_active:
                print(f"[快取預熱] 已在執行中，略過 {reason}", flush=True)
                return
            _prewarm_active = True
        try:
            from main.cache_warmup import prewarm_historical_caches

            print(f"[快取預熱] 觸發來源：{reason}", flush=True)
            prewarm_historical_caches(
                month_limit=month_limit,
                list_date_limit=list_date_limit,
                chart_month_limit=chart_month_limit,
                chart_date_limit=chart_date_limit,
                chart_rows=CACHE_PREWARM_CHART_ROWS,
                chart_stocks=CACHE_PREWARM_STOCKS,
                activity_filters={
                    "day_atr": CACHE_PREWARM_DAY_ATR,
                    "vol5_pr": CACHE_PREWARM_VOL5_PR,
                },
                chart_pause_sec=CACHE_PREWARM_CHART_PAUSE_SEC,
            )
        except Exception as exc:
            print(f"[快取預熱] 失敗: {exc}", flush=True)
            _log_sys(f"快取預熱失敗: {exc}", "warning")
        finally:
            with _prewarm_lock:
                _prewarm_active = False

    threading.Thread(target=_run, daemon=True).start()


_last_hf_sync_date = None
_next_hf_sync_retry_at = None


def _daily_hf_sync() -> None:
    """Check HF for a fresh market DB once a day while the service stays online."""
    global _last_hf_sync_date, _next_hf_sync_retry_at
    while True:
        now = datetime.now(_TW)
        today = now.date()
        scheduled = (HF_DAILY_SYNC_HOUR, HF_DAILY_SYNC_MIN)
        retry_ready = _next_hf_sync_retry_at is None or now >= _next_hf_sync_retry_at
        should_sync = (
            _startup_done.is_set()
            and _last_hf_sync_date != today
            and (now.hour, now.minute) >= scheduled
            and retry_ready
        )
        if should_sync:
            hhmm = now.strftime("%H:%M")
            print(f"[{hhmm}] 每日 HF 同步檢查：下載外部維護的歷史 market DB", flush=True)
            sync_status = _startup_data.sync_local_market_db_from_hf_if_stale()
            expected_signal_date = _startup_data.latest_market_db_check_date(now)
            if sync_status in ("synced", "signals_synced", "fresh") and not _startup_data.offline_signal_date_available(expected_signal_date):
                _next_hf_sync_retry_at = now + timedelta(minutes=30)
                print(
                    f"  尚未看到 {expected_signal_date} 的離線盤勢訊號，將於 {_next_hf_sync_retry_at.strftime('%H:%M')} 後重試",
                    flush=True,
                )
                _log_sys(f"每日 HF 同步尚未取得 {expected_signal_date} 離線盤勢訊號，30 分鐘後重試", "warning")
            elif sync_status in ("synced", "signals_synced"):
                _clear_after_hf_sync()
                _start_cache_prewarm("daily-hf-sync")
                _last_hf_sync_date = today
                _next_hf_sync_retry_at = None
                _log_sys("每日 HF 同步完成：已下載歷史/離線訊號資料並清空查詢快取")
            elif sync_status == "fresh":
                _last_hf_sync_date = today
                _next_hf_sync_retry_at = None
                _log_sys("每日 HF 同步檢查完成：本機歷史資料已新鮮")
            else:
                _next_hf_sync_retry_at = now + timedelta(minutes=30)
                print(f"  每日 HF 同步失敗，將於 {_next_hf_sync_retry_at.strftime('%H:%M')} 後重試", flush=True)
                _log_sys("每日 HF 同步失敗，30 分鐘後重試", "error")
        time.sleep(60)


def _daily_refresh() -> None:
    """Refresh subscription and support/resistance levels once every trading day."""
    last_refresh = None
    while True:
        now = datetime.now(_TW)
        today = now.date()
        should_refresh = _startup_done.is_set() and last_refresh != today and now.hour == 6
        if should_refresh:
            print(f"[{now.strftime('%H:%M')}] 每日更新：富邦訂閱清單 + SR水位", flush=True)
            try:
                _startup_data.refresh_fubon_subscription_universe(state)
                _refresh_sr_levels(now.strftime("%Y-%m-%d"))
                last_refresh = today
                _log_sys(f"每日更新完成：{len(state.tickers)} 支標的")
            except Exception as exc:
                print(f"  每日更新失敗: {exc}", flush=True)
                _log_sys(f"每日更新失敗: {exc}", "error")
        time.sleep(60)


def _watchlist_prev_close(stock_id: str, date_str: str) -> float | None:
    cached = _prev_close_cache.get(stock_id)
    if cached and cached[0] == date_str:
        return cached[1]

    from data.query import load_day_by_stock

    df = load_day_by_stock(stock_id)
    if df.empty:
        return None
    df = df[df["date"] < pd.Timestamp(date_str)]
    if df.empty:
        return None
    val = float(df.iloc[-1]["close"])
    _prev_close_cache[stock_id] = (date_str, val)
    return val


def _refresh_sr_levels(date_str: str) -> None:
    from data.adjustment_query import load_pattern_day
    from pattern.horizontal_sr import horizontal_sr_prices

    stocks = {str(sid) for sid in state.tickers.keys()}
    if not stocks:
        state.sr_levels = {}
        state.sr_prev_close = {}
        state.sr_levels_date = date_str
        return

    hist_start = (pd.Timestamp(date_str) - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
    print(f"[SR水位] 計算 {len(stocks)} 檔（日K < {date_str}）", flush=True)
    day = load_pattern_day(start_date=hist_start, end_date=date_str)
    if day.empty:
        state.sr_levels = {}
        state.sr_prev_close = {}
        state.sr_levels_date = date_str
        print("  [SR水位] 無日K", flush=True)
        return

    day = day.copy()
    day["stock_id"] = day["stock_id"].astype(str)
    cutoff = pd.Timestamp(date_str)
    levels: dict[str, tuple[float | None, float | None]] = {}
    prev_closes: dict[str, float] = {}
    for sid, g in day.groupby("stock_id", sort=False):
        sid = str(sid)
        if sid not in stocks:
            continue
        hist = g.loc[g["date"] < cutoff].sort_values("date")
        if not hist.empty:
            prev_closes[sid] = float(hist["close"].iloc[-1])
        res, sup = horizontal_sr_prices(hist)
        if res is not None or sup is not None:
            levels[sid] = (res, sup)

    state.sr_levels = levels
    state.sr_prev_close = prev_closes
    state.sr_levels_date = date_str
    print(f"  [SR水位] {len(levels)} 檔有壓力/支撐", flush=True)


def _ensure_sr_vwap_day(date_str: str) -> None:
    if state.sr_levels_date == date_str:
        return
    state.vwap_crossed_today.clear()
    _refresh_sr_levels(date_str)


def _ensure_vwap_catchup_after_collector_backfill(date_str: str) -> None:
    """Fill today's VWAP/SR memory once after collector finishes M1 gap backfill."""
    global _last_vwap_catchup_date
    if _last_vwap_catchup_date == date_str or not state.backfill_done.is_set():
        return
    try:
        _vwap_sr_catchup()
        _last_vwap_catchup_date = date_str
    except Exception as exc:
        print(f"[VWAP catchup] 補齊失敗: {exc}", flush=True)
        _log_sys(f"VWAP catchup 補齊失敗: {exc}", "error")


def on_minute(minute_str: str, df: pd.DataFrame) -> None:
    """Collector callback: push K lines, quotes, and VWAP-related events."""
    dt = pd.Timestamp(minute_str)
    h, m = dt.hour, dt.minute
    date_str = minute_str[:10]
    hhmm = minute_str[11:16]

    _ensure_sr_vwap_day(date_str)
    m1_live = load_m1_live(date_str)
    if m1_live.empty:
        print(f"[on_minute] {hhmm} 無 M1 資料", flush=True)
        return

    print(
        f"[on_minute] {hhmm} M1:{m1_live['stock_id'].nunique()} 支 {len(m1_live):,} 筆",
        flush=True,
    )

    from pattern.vwap_macd_div import all_divs_for_group as all_macd_divs_for_group
    from pattern.vwap_obv_div import all_divs_for_group as all_obv_divs_for_group
    from pattern.vwap_sr_scan import events_for_group

    vwap_breakouts: list[dict] = []
    sr_vwap_hits: list[dict] = []
    macd_by_sid: dict[str, dict] = {}
    obv_by_sid: dict[str, dict] = {}
    chg_map: dict[str, float] = {}
    pc_map = state.sr_prev_close or {}

    backfill_ready = state.backfill_done.is_set()
    if backfill_ready:
        _ensure_vwap_catchup_after_collector_backfill(date_str)
    else:
        print(f"[on_minute] {hhmm} M1缺口仍在補，先只推K線/報價", flush=True)

    for raw_sid, g in m1_live.groupby("stock_id", sort=False):
        sid = str(raw_sid)
        push_candles(sid, [])

        if not g.empty:
            prev_close = pc_map.get(sid)
            last_close = float(g.sort_values("date").iloc[-1]["close"])
            if prev_close and prev_close > 0:
                chg_map[sid] = round((last_close / prev_close - 1.0) * 100, 2)
            if sid in WATCHLIST_QUOTES and (h, m) <= (MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN):
                quote_prev_close = _watchlist_prev_close(sid, date_str)
                push_quote(sid, last_close, quote_prev_close, minute_str)

        if not backfill_ready:
            continue

        macd_evs = all_macd_divs_for_group(g)
        if macd_evs:
            macd_by_sid[sid] = {"events": macd_evs}

        obv_evs = all_obv_divs_for_group(g)
        if obv_evs:
            obv_by_sid[sid] = {"events": obv_evs}

        if len(g) >= 2:
            ve, se = events_for_group(
                sid,
                state.tickers.get(sid, sid),
                g,
                state.sr_levels.get(sid),
                pc_map.get(sid),
            )
            vwap_breakouts.extend([item for item in ve if item.get("time") == hhmm])
            sr_vwap_hits.extend([item for item in se if item.get("time") == hhmm])

    if chg_map:
        push_vwap_chg(minute_str, chg_map)

    if not backfill_ready:
        return

    push_vwap_macd_div(minute_str, macd_by_sid)
    push_vwap_obv_div(minute_str, obv_by_sid)

    if vwap_breakouts:
        push_vwap_breakout(minute_str, vwap_breakouts)
        print(
            f"  [VWAP突破] {len(vwap_breakouts)} 筆: "
            + " ".join(f"{b['stock_id']}({'突破' if b['direction'] == 'up' else '跌破'})" for b in vwap_breakouts),
            flush=True,
        )
    if sr_vwap_hits:
        push_sr_vwap_cross(minute_str, sr_vwap_hits)
        print(
            f"  [VWAP+壓力支撐] {len(sr_vwap_hits)} 筆: "
            + " ".join(f"{b['stock_id']}({b['sr_kind']})" for b in sr_vwap_hits),
            flush=True,
        )


def _run_collector_after_startup() -> None:
    _startup_done.wait()
    _collector.start_collector(on_minute, backfill_done=state.backfill_done)


def _server_port() -> int:
    raw = os.environ.get("PORT", "8000")
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"PORT must be an integer, got {raw!r}") from exc


if __name__ == "__main__":
    threading.Thread(target=_startup, daemon=True).start()
    threading.Thread(target=_daily_hf_sync, daemon=True).start()
    threading.Thread(target=_daily_refresh, daemon=True).start()
    threading.Thread(target=_run_collector_after_startup, daemon=True).start()

    port = _server_port()
    print(f"HTTP server listening on 0.0.0.0:{port}", flush=True)
    config = get_uvicorn_config(host="0.0.0.0", port=port)
    server = uvicorn.Server(config)
    server.run()
