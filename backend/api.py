"""
FastAPI backend for the slim pattern/VWAP monitor.

Kept surfaces:
- /api/pattern/* technical pattern scanning and chart detail
- /vwap_* VWAP, support/resistance, MACD/OBV divergence panels
- /chart/* D1/M1 chart data
- /quote/* watchlist quotes
- /stream server-sent events for realtime UI updates
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import threading
import time as _time_mod
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

_TW = timezone(timedelta(hours=8))


def tw_naive_to_epoch(dt) -> int:
    """Convert a Taiwan-local naive datetime/Timestamp to UTC epoch seconds."""
    if hasattr(dt, "tz_localize"):
        return int(dt.tz_localize(_TW).timestamp())
    return int(pd.Timestamp(dt).to_pydatetime().replace(tzinfo=_TW).timestamp())


app = FastAPI(
    title="just1stock 型態與 VWAP 監控",
    version="2.0.0",
    description="精簡版：型態掃描、VWAP框、K線、歷史資料與即時連線。",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_SKIP_LOG_PATHS = {"/stream", "/health"}


@app.middleware("http")
async def _log_http(request: Request, call_next):
    if request.url.path in _SKIP_LOG_PATHS:
        return await call_next(request)
    t0 = _time_mod.time()
    response = await call_next(request)
    elapsed_ms = int((_time_mod.time() - t0) * 1000)
    qs = f"?{request.url.query}" if request.url.query else ""
    append_system_log(
        f"{request.method} {request.url.path}{qs} -> {response.status_code} ({elapsed_ms}ms)",
        level="info" if response.status_code < 400 else "error",
    )
    return response


# Settings are kept as a tiny helper for operational defaults. The dashboard
# only reads the active monitor defaults.
_SETTINGS_PATH = Path(__file__).parent / "settings.json"
_settings_cache: dict | None = None


def _load_settings() -> dict:
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    try:
        _settings_cache = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        _settings_cache = {}
    return _settings_cache


def get_setting(key: str, default=None):
    return _load_settings().get(key, default)


# In-memory realtime state.
_lock = threading.Lock()
_today_date: date | None = None
_collector_status = "stopped"
_collector_coverage: dict = {"arrived": 0, "total": 0}
_quotes: dict[str, dict] = {}
_vwap_breakout_signals: list[dict] = []
_sr_vwap_cross_signals: list[dict] = []
_vwap_macd_live: dict | None = None
_vwap_obv_live: dict | None = None
_vwap_chg: dict[str, float] = {}

_system_logs: deque = deque(maxlen=500)
_LOG_DIR = Path(__file__).parent / "logs"
_VWAP_BUNDLE_CACHE: dict[tuple, dict] = {}
_VWAP_BUNDLE_CACHE_ORDER: deque = deque()
_VWAP_BUNDLE_CACHE_LIMIT = max(3, int(os.environ.get("VWAP_BUNDLE_CACHE_DATES", "80")))
_VWAP_BUNDLE_DISK_CACHE_DIR = Path(os.environ.get(
    "VWAP_BUNDLE_CACHE_DIR",
    Path(__file__).parent / ".cache/vwap_bundle",
))
_VWAP_BUNDLE_DISK_CACHE_ENABLED = os.environ.get("VWAP_BUNDLE_DISK_CACHE", "1").lower() not in {"0", "false", "no"}
_vwap_bundle_cache_lock = threading.Lock()


def _mtime_token(path: Path) -> str:
    try:
        return str(int(path.stat().st_mtime_ns))
    except OSError:
        return "missing"


def _vwap_bundle_source_version(date_str: str) -> str:
    month = date_str[:7].replace("-", "_")
    paths = [
        Path(__file__).parent / f"db/vwap_signals/{month}.parquet",
        Path(__file__).parent / f"db/vwap_activity/{month}.parquet",
        Path(__file__).parent / "db/tickers/tick_universe.parquet",
    ]
    return "|".join(f"{p.name}:{_mtime_token(p)}" for p in paths)


def _vwap_bundle_cache_key(date_str: str, universe: str, repeat: bool) -> tuple:
    return (date_str, universe or "daytrade", bool(repeat), _vwap_bundle_source_version(date_str))


def _vwap_bundle_disk_cache_path(cache_key: tuple) -> Path:
    raw = json.dumps(cache_key, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    date_part = str(cache_key[0]).replace("/", "-")
    return _VWAP_BUNDLE_DISK_CACHE_DIR / date_part / digest[:2] / f"{digest}.json.gz"


def _read_vwap_bundle_cache(cache_key: tuple) -> dict | None:
    with _vwap_bundle_cache_lock:
        cached = _VWAP_BUNDLE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if not _VWAP_BUNDLE_DISK_CACHE_ENABLED:
        return None
    path = _vwap_bundle_disk_cache_path(cache_key)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return None
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    _remember_vwap_bundle_cache(cache_key, payload)
    return payload


def _remember_vwap_bundle_cache(cache_key: tuple, payload: dict) -> None:
    with _vwap_bundle_cache_lock:
        _VWAP_BUNDLE_CACHE[cache_key] = payload
        if cache_key in _VWAP_BUNDLE_CACHE_ORDER:
            _VWAP_BUNDLE_CACHE_ORDER.remove(cache_key)
        _VWAP_BUNDLE_CACHE_ORDER.append(cache_key)
        while len(_VWAP_BUNDLE_CACHE_ORDER) > _VWAP_BUNDLE_CACHE_LIMIT:
            oldest = _VWAP_BUNDLE_CACHE_ORDER.popleft()
            _VWAP_BUNDLE_CACHE.pop(oldest, None)


def _write_vwap_bundle_disk_cache(cache_key: tuple, payload: dict) -> None:
    if not _VWAP_BUNDLE_DISK_CACHE_ENABLED:
        return
    path = _vwap_bundle_disk_cache_path(cache_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=4) as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        pass


def clear_vwap_bundle_cache() -> None:
    """Clear in-memory historical bundle cache after HF refresh."""
    with _vwap_bundle_cache_lock:
        _VWAP_BUNDLE_CACHE.clear()
        _VWAP_BUNDLE_CACHE_ORDER.clear()


def _reset_if_new_day() -> None:
    global _today_date, _vwap_macd_live, _vwap_obv_live, _vwap_chg
    today = datetime.now(_TW).date()
    if _today_date == today:
        return
    _today_date = today
    _quotes.clear()
    _vwap_breakout_signals.clear()
    _sr_vwap_cross_signals.clear()
    _vwap_macd_live = None
    _vwap_obv_live = None
    _vwap_chg = {}


def _log_ts() -> str:
    return datetime.now(_TW).strftime("%m/%d %H:%M:%S")


def _write_log_file(entry: dict) -> None:
    try:
        _LOG_DIR.mkdir(exist_ok=True)
        path = _LOG_DIR / f"{datetime.now(_TW).strftime('%Y-%m-%d')}.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"cat": "system", **entry}, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[LOG FILE ERROR] {exc}", flush=True)


def _read_log_file() -> list[dict]:
    path = _LOG_DIR / f"{datetime.now(_TW).strftime('%Y-%m-%d')}.jsonl"
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return rows


def append_system_log(message: str, level: str = "info") -> None:
    entry = {"time": _log_ts(), "level": level, "msg": message}
    _system_logs.append(entry)
    _write_log_file(entry)
    _broadcast({"type": "log", "cat": "system", **entry})


def push_alert(message: str, level: str = "warning") -> None:
    entry = {"time": _log_ts(), "level": level, "msg": message}
    _system_logs.append(entry)
    _write_log_file(entry)
    _broadcast({"type": "alert", "level": level, "message": message, "time": entry["time"]})
    _broadcast({"type": "log", "cat": "system", **entry})


# SSE broadcast.
_sse_clients: set[asyncio.Queue] = set()
_event_loop: asyncio.AbstractEventLoop | None = None


@app.on_event("startup")
async def _capture_loop() -> None:
    global _event_loop
    _event_loop = asyncio.get_running_loop()


def _broadcast(data: dict) -> None:
    if not _event_loop or not _sse_clients:
        return

    async def _enqueue_all() -> None:
        for q in list(_sse_clients):
            await q.put(data)

    asyncio.run_coroutine_threadsafe(_enqueue_all(), _event_loop)


from pattern.pattern_api import router as pattern_router

app.include_router(pattern_router)


# Public push functions used by realtime collectors.
def set_collector_status(status: str) -> None:
    global _collector_status
    _collector_status = status


def set_collector_coverage(arrived: int, total: int) -> None:
    global _collector_coverage
    _collector_coverage = {"arrived": int(arrived), "total": int(total)}


def _vwap_event_key(e: dict) -> tuple[str, str, str]:
    return (str(e.get("stock_id", "")), str(e.get("time", "")), str(e.get("direction", "")))


def _sr_vwap_event_key(e: dict) -> tuple[str, str, str, str]:
    return (
        str(e.get("stock_id", "")),
        str(e.get("time", "")),
        str(e.get("sr_kind", "")),
        str(e.get("vwap_dir", "")),
    )


def push_candles(stock_id: str, candles: list[dict]) -> None:
    """Notify clients that a stock's persisted live candles changed."""
    with _lock:
        _reset_if_new_day()
    _broadcast({"type": "candles", "stock_id": str(stock_id)})


def push_quote(stock_id: str, price: float, prev_close: float | None, minute_str: str = "") -> None:
    change_pct = (price - prev_close) / prev_close * 100 if prev_close else None
    row = {
        "stock_id": str(stock_id),
        "price": float(price),
        "prev_close": prev_close,
        "change_pct": change_pct,
        "minute": minute_str,
    }
    with _lock:
        _reset_if_new_day()
        _quotes[str(stock_id)] = row
    _broadcast({"type": "quote", "stock_id": str(stock_id), "data": row})


def push_vwap_breakout(minute_str: str, breakouts: list[dict]) -> None:
    if not breakouts:
        return
    fresh: list[dict] = []
    with _lock:
        _reset_if_new_day()
        existing = {_vwap_event_key(x) for x in _vwap_breakout_signals}
        for item in breakouts:
            row = {**item, "time": minute_str[11:16]}
            key = _vwap_event_key(row)
            if key in existing:
                continue
            existing.add(key)
            _vwap_breakout_signals.append(row)
            fresh.append(row)
    if fresh:
        _broadcast({"type": "vwap_breakout", "minute": minute_str[11:16], "data": fresh})


def push_sr_vwap_cross(minute_str: str, hits: list[dict]) -> None:
    if not hits:
        return
    fresh: list[dict] = []
    with _lock:
        _reset_if_new_day()
        existing = {_sr_vwap_event_key(x) for x in _sr_vwap_cross_signals}
        for item in hits:
            row = {**item, "time": minute_str[11:16]}
            key = _sr_vwap_event_key(row)
            if key in existing:
                continue
            existing.add(key)
            _sr_vwap_cross_signals.append(row)
            fresh.append(row)
    if fresh:
        _broadcast({"type": "sr_vwap_cross", "minute": minute_str[11:16], "data": fresh})


def push_vwap_macd_div(minute_str: str, stocks: dict) -> None:
    global _vwap_macd_live
    with _lock:
        _reset_if_new_day()
        _vwap_macd_live = stocks or {}
    _broadcast({"type": "vwap_macd_div", "minute": minute_str[11:16]})


def push_vwap_obv_div(minute_str: str, stocks: dict) -> None:
    global _vwap_obv_live
    with _lock:
        _reset_if_new_day()
        _vwap_obv_live = stocks or {}
    _broadcast({"type": "vwap_obv_div", "minute": minute_str[11:16]})


def push_vwap_chg(minute_str: str, stocks: dict) -> None:
    global _vwap_chg
    with _lock:
        _reset_if_new_day()
        _vwap_chg = {str(k): float(v) for k, v in (stocks or {}).items()}
    _broadcast({"type": "vwap_chg", "minute": minute_str[11:16], "stocks": dict(_vwap_chg)})


_vwap_sr_catchup_hook = None


def register_vwap_sr_catchup_hook(fn) -> None:
    global _vwap_sr_catchup_hook
    _vwap_sr_catchup_hook = fn


def _sync_live_vwap_sr_sets(vwap: list, sr: list) -> None:
    if _vwap_sr_catchup_hook is not None:
        _vwap_sr_catchup_hook(vwap, sr)


def _scan_vwap_sr(date_str: str, universe: str = "daytrade") -> tuple[list, list]:
    from pattern.vwap_sr_scan import scan_date

    vwap, sr = scan_date(date_str, universe=universe)
    return list(reversed(vwap)), list(reversed(sr))


def _today_str() -> str:
    return datetime.now(_TW).strftime("%Y-%m-%d")


def _historical_vwap_bundle(date_str: str, universe: str = "daytrade") -> dict:
    """Read HF-synced historical intraday signals for API responses."""
    from pattern.vwap_signal_store import read_vwap_signals
    from pattern.vwap_sr_scan import stock_ids_for_universe

    bundle = read_vwap_signals(date_str, stock_ids=stock_ids_for_universe(universe))
    return bundle or {
        "vwap": [],
        "sr": [],
        "macd": {},
        "obv": {},
        "chg": {},
        "sr_levels": {},
        "m1_bars": 0,
    }


def _latest_signal_by_stock(rows: list[dict]) -> list[dict]:
    """Keep only the latest intraday event per stock for default list views."""
    latest: dict[str, dict] = {}
    for row in rows or []:
        sid = str(row.get("stock_id") or "")
        if not sid:
            continue
        prev = latest.get(sid)
        if prev is None or str(row.get("time") or "") > str(prev.get("time") or ""):
            latest[sid] = row
    return sorted(
        latest.values(),
        key=lambda item: (str(item.get("time") or ""), str(item.get("stock_id") or "")),
        reverse=True,
    )


def _activity_metrics_for_date(date_str: str, universe: str) -> dict:
    from pattern.vwap_activity import metrics_for_date

    return metrics_for_date(date_str, universe=universe)


def _stored_activity_for_date(date_str: str, universe: str) -> dict:
    """Read GHA-produced activity data without runtime calculation."""
    from pattern.activity_store import read_vwap_activity
    from pattern.vwap_sr_scan import stock_ids_for_universe

    return read_vwap_activity(date_str, stock_ids=stock_ids_for_universe(universe)) or {}


def _catchup_today_into_memory() -> tuple[list, list]:
    global _vwap_macd_live, _vwap_obv_live, _vwap_chg
    today = datetime.now(_TW).strftime("%Y-%m-%d")
    from pattern.vwap_macd_div import metrics_for_date as macd_metrics
    from pattern.vwap_obv_div import metrics_for_date as obv_metrics
    from pattern.vwap_sr_scan import last_chg_map, scan_date

    vwap, sr = scan_date(today)
    macd = macd_metrics(today)
    obv = obv_metrics(today)
    chg = last_chg_map(today)
    with _lock:
        _reset_if_new_day()
        _vwap_breakout_signals.clear()
        _vwap_breakout_signals.extend(vwap)
        _sr_vwap_cross_signals.clear()
        _sr_vwap_cross_signals.extend(sr)
        _vwap_macd_live = macd
        _vwap_obv_live = obv
        _vwap_chg = chg
    _sync_live_vwap_sr_sets(vwap, sr)
    append_system_log(
        f"VWAP catchup: {today} VWAP {len(vwap)} / SR {len(sr)} / MACD {len(macd)} / OBV {len(obv)} / chg {len(chg)}"
    )
    return list(reversed(vwap)), list(reversed(sr))


def _session_vwap_from_candles(candles: list[dict]) -> list[dict]:
    out: list[dict] = []
    cum_pv = 0.0
    cum_vol = 0.0
    for c in candles:
        vol = float(c.get("volume") or 0)
        close = float(c["close"])
        cum_pv += close * vol
        cum_vol += vol
        if cum_vol > 0:
            out.append({"time": c["time"], "value": round(cum_pv / cum_vol, 2)})
    return out


def _legacy_candle_shape(candles: list[dict]) -> list[dict]:
    """Keep the old response_model shape after slimming Pydantic models."""
    shaped = []
    for c in candles:
        row = dict(c)
        row.setdefault("vwap", None)
        shaped.append(row)
    return shaped


_COLLECTOR_MSG = {
    "running": "資料流正常",
    "stopped": "盤後或尚未啟動",
    "error": "資料流中斷",
}


@app.get("/health", tags=["系統"], summary="健康檢查")
def health():
    return {
        "status": "ok",
        "collector": _collector_status,
        "message": _COLLECTOR_MSG.get(_collector_status, _collector_status),
        "sse_clients": len(_sse_clients),
        "ws_clients": len(_sse_clients),
        "last_signal_at": None,
        "coverage": _collector_coverage,
    }


@app.get("/settings", tags=["系統"], summary="讀取本機設定")
def settings_get():
    return _load_settings()


@app.get("/vwap_chg", tags=["VWAP"], summary="今日每檔股票漲跌幅%")
def vwap_chg_today():
    with _lock:
        return dict(_vwap_chg)


@app.get("/vwap_breakout/today", tags=["VWAP"], summary="VWAP 突破/跌破清單")
def vwap_breakout_today(date: Optional[str] = None, universe: str = "daytrade"):
    if date:
        if str(date)[:10] != _today_str():
            return _historical_vwap_bundle(str(date)[:10], universe=universe)["vwap"]
        vwap, _ = _scan_vwap_sr(date, universe=universe)
        return vwap
    with _lock:
        return list(reversed(_vwap_breakout_signals))


@app.get("/sr_vwap_cross/today", tags=["VWAP"], summary="VWAP + 壓力/支撐穿越清單")
def sr_vwap_cross_today(date: Optional[str] = None, universe: str = "daytrade"):
    if date:
        if str(date)[:10] != _today_str():
            return _historical_vwap_bundle(str(date)[:10], universe=universe)["sr"]
        _, sr = _scan_vwap_sr(date, universe=universe)
        return sr
    with _lock:
        return list(reversed(_sr_vwap_cross_signals))


@app.get("/vwap_sr_catchup", tags=["VWAP"], summary="補齊今日 VWAP/SR 記憶體狀態")
def vwap_sr_catchup():
    vwap, sr = _catchup_today_into_memory()
    return {"vwap": vwap, "sr": sr}


@app.get("/vwap_sr_replay", tags=["VWAP"], summary="掃描指定日期的 VWAP/SR")
def vwap_sr_replay(date: str, universe: str = "daytrade"):
    from pattern.vwap_sr_scan import last_chg_map, last_m1_bars

    date_str = str(date)[:10]
    if date_str != _today_str():
        bundle = _historical_vwap_bundle(date_str, universe=universe)
        return {
            "vwap": bundle["vwap"],
            "sr": bundle["sr"],
            "m1_bars": bundle["m1_bars"],
            "chg": bundle["chg"],
        }

    vwap, sr = _scan_vwap_sr(date, universe=universe)
    return {
        "vwap": vwap,
        "sr": sr,
        "m1_bars": last_m1_bars(date_str, universe),
        "chg": last_chg_map(date_str, universe=universe),
    }


@app.get("/vwap_activity", tags=["VWAP"], summary="VWAP 篩選用活動度資料")
def vwap_activity(date: Optional[str] = None, universe: str = "daytrade"):
    date_str = date or datetime.now(_TW).strftime("%Y-%m-%d")
    if date_str == _today_str():
        return {"date": date_str, "stocks": _stored_activity_for_date(date_str, universe)}
    from pattern.vwap_activity import metrics_for_date

    return {"date": date_str, "stocks": metrics_for_date(date_str, universe=universe)}


@app.get("/vwap_macd_div", tags=["VWAP"], summary="MACD 柱體背離")
def vwap_macd_div(date: Optional[str] = None, universe: str = "daytrade"):
    date_str = date or datetime.now(_TW).strftime("%Y-%m-%d")
    today = datetime.now(_TW).strftime("%Y-%m-%d")
    if date_str != today:
        return {"date": date_str, "stocks": _historical_vwap_bundle(date_str, universe=universe)["macd"]}
    if date_str == today:
        with _lock:
            return {"date": date_str, "stocks": dict(_vwap_macd_live or {})}

    from pattern.vwap_macd_div import metrics_for_date
    return {"date": date_str, "stocks": metrics_for_date(date_str, universe=universe)}


@app.get("/vwap_obv_div", tags=["VWAP"], summary="OBV 背離")
def vwap_obv_div(date: Optional[str] = None, universe: str = "daytrade"):
    date_str = date or datetime.now(_TW).strftime("%Y-%m-%d")
    today = datetime.now(_TW).strftime("%Y-%m-%d")
    if date_str != today:
        return {"date": date_str, "stocks": _historical_vwap_bundle(date_str, universe=universe)["obv"]}
    if date_str == today:
        with _lock:
            return {"date": date_str, "stocks": dict(_vwap_obv_live or {})}

    from pattern.vwap_obv_div import metrics_for_date
    return {"date": date_str, "stocks": metrics_for_date(date_str, universe=universe)}


@app.get("/vwap_signal/bundle", tags=["VWAP"], summary="盤中訊號整包讀取")
def vwap_signal_bundle(date: Optional[str] = None, universe: str = "daytrade", repeat: bool = False):
    """Return the dashboard's historical signal inputs with one parquet read.

    The refresh path used to fire VWAP/SR, activity, MACD, and OBV as separate
    requests. On a small Oracle VM those concurrent requests could all cold-read
    the same monthly shards before caches filled. This endpoint keeps the same
    response shapes while reducing duplicate IO.
    """
    date_str = (date or _today_str())[:10]
    today = _today_str()
    if date_str != today:
        cache_key = _vwap_bundle_cache_key(date_str, universe, repeat)
        cached = _read_vwap_bundle_cache(cache_key)
        if cached is not None:
            return cached
        bundle = _historical_vwap_bundle(date_str, universe=universe)
        vwap_rows = bundle["vwap"] if repeat else _latest_signal_by_stock(bundle["vwap"])
        result = {
            "date": date_str,
            "vwap": vwap_rows,
            "sr": bundle["sr"],
            "m1_bars": bundle["m1_bars"],
            "chg": bundle["chg"],
            "activity": _activity_metrics_for_date(date_str, universe),
            "macd": bundle["macd"],
            "obv": bundle["obv"],
        }
        _remember_vwap_bundle_cache(cache_key, result)
        _write_vwap_bundle_disk_cache(cache_key, result)
        return result

    with _lock:
        vwap_rows = list(reversed(_vwap_breakout_signals))
        sr_rows = list(reversed(_sr_vwap_cross_signals))
        macd_live = dict(_vwap_macd_live or {})
        obv_live = dict(_vwap_obv_live or {})
        chg_live = dict(_vwap_chg)

    if not repeat:
        vwap_rows = _latest_signal_by_stock(vwap_rows)
    return {
        "date": date_str,
        "vwap": vwap_rows,
        "sr": sr_rows,
        "m1_bars": 0,
        "chg": chg_live,
        "activity": _stored_activity_for_date(date_str, universe),
        "macd": macd_live,
        "obv": obv_live,
    }


@app.get("/vwap_signal/dates", tags=["VWAP"], summary="取得已有離線盤勢資料的日期")
def vwap_signal_dates():
    from pattern.activity_store import available_activity_dates
    from pattern.vwap_signal_store import available_signal_dates

    dates = sorted(set(available_activity_dates()) | set(available_signal_dates()))
    return {
        "dates": dates,
        "latest": dates[-1] if dates else None,
        "total": len(dates),
    }


@app.get("/chart/{stock_id}/candles", tags=["圖表"], summary="今日即時 M1 K 線")
def chart_candles(stock_id: str):
    return chart_candles_history(str(stock_id), _today_str())


@app.get("/chart/{stock_id}/candles/history", tags=["圖表"], summary="歷史 M1 K 線")
def chart_candles_history(
    stock_id: str,
    date: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    interval: str = "1m",
):
    if interval != "1m":
        raise HTTPException(status_code=400, detail="目前只支援 interval=1m")

    from pattern.data_loader import get_stock_candles

    try:
        df = get_stock_candles(
            str(stock_id),
            timeframe="1m",
            date=str(date)[:10],
            limit=100000,
            full_day=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"讀取本機/HF 歷史分K失敗: {exc}") from exc
    if df.empty:
        raise HTTPException(status_code=404, detail=f"{stock_id} 在 {date} 沒有分K資料")

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    df["_minute"] = df["date"].dt.strftime("%H:%M")
    if start_time:
        df = df[df["_minute"] >= start_time]
    if end_time:
        df = df[df["_minute"] <= end_time]
    df = df.sort_values("date")

    candles = [
        {
            "time": tw_naive_to_epoch(row["date"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
            "vwap": None,
        }
        for _, row in df.iterrows()
    ]
    return {"stock_id": str(stock_id), "candles": candles, "vwap": _session_vwap_from_candles(candles)}


@app.get("/quote/{stock_id}", tags=["圖表"], summary="固定追蹤股票即時報價")
def get_quote(stock_id: str):
    with _lock:
        row = _quotes.get(str(stock_id))
    if row is None:
        raise HTTPException(status_code=404, detail=f"尚無 {stock_id} 的報價資料（可能還沒開盤或非追蹤清單）")
    return row


@app.get("/api/logs", tags=["系統"], summary="今日程式日誌")
def get_logs(cat: str = "system"):
    rows = _read_log_file()
    if not rows:
        rows = [{"cat": "system", **row} for row in list(_system_logs)]
    if cat not in {"all", "system"}:
        return []
    return list(reversed(rows))[:500]


@app.get("/stream", tags=["即時推送"], summary="SSE 即時推送")
async def event_stream(request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    _sse_clients.add(queue)

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=5)
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            _sse_clients.discard(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST_DIR", Path(__file__).parent / "static"))
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")


def get_uvicorn_config(host: str = "0.0.0.0", port: int = 8000) -> uvicorn.Config:
    return uvicorn.Config(app=app, host=host, port=port, log_level="warning")
