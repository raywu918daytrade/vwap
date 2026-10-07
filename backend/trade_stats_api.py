"""Paper-trade logs for dashboard signal setups.

``sr_short`` trades the VWAP + support/resistance light all day: above VWAP
and touching resistance -> long, below VWAP and touching support -> short.
``sr_short_slots`` is the backtest's best setup: short only (below VWAP and
touching support) in 10:00-10:15 and 10:45-11:15.
``vwap_cross`` trades every VWAP cross all day: cross up -> long, cross down
-> short.
``macd_vwap`` trades a VWAP cross that follows a MACD histogram divergence:
bullish divergence then cross up -> long, bearish divergence then cross down
-> short (the latest divergence confirmed within 30 minutes before the cross).

All enter at the next minute's open, +2% take profit / -4% stop loss (stop
wins when one bar hits both), otherwise exit at the 13:24 close. Only stocks
that pass the dashboard's default filters (ATR >= 5%, open-5-minute volume
PR >= 50) and only the first qualifying signal per stock per day.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter

router = APIRouter()
_TW = timezone(timedelta(hours=8))

_COMMON = {
    "take_profit_pct": 2.0,
    "stop_loss_pct": 4.0,
    "last_bar": "13:24",
    "cost_pct": 0.435,
    "min_day_atr": 0.05,
    "min_vol5_pr": 0.5,
}
STRATEGIES = {
    "sr_short": {
        **_COMMON,
        "label": "VWAP＋壓力支撐",
        "side": "both",
        "signal": "站上 VWAP 碰壓力做多、跌破 VWAP 碰支撐做空",
        "window": ["09:00", "13:24"],
    },
    "sr_short_slots": {
        **_COMMON,
        "label": "SR 做空・最佳時段",
        "side": "short",
        "signal": "跌破 VWAP 碰支撐做空",
        "window": ["10:00", "11:15"],
        "windows": [["10:00", "10:15"], ["10:45", "11:15"]],
    },
    "vwap_cross": {
        **_COMMON,
        "label": "VWAP 穿越",
        "side": "both",
        "signal": "VWAP 上穿做多、下穿做空",
        "window": ["09:00", "13:24"],
    },
    "macd_vwap": {
        **_COMMON,
        "label": "MACD 背離＋VWAP",
        "side": "both",
        "signal": "MACD 底背離後上穿 VWAP 做多、頂背離後下穿 VWAP 做空（背離在穿越前 30 分內）",
        "window": ["09:00", "13:24"],
        "macd_max_gap_min": 30,
    },
}
DEFAULT_STRATEGY = "sr_short"
RULES = STRATEGIES[DEFAULT_STRATEGY]

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_LIVE_TTL_SECONDS = 20.0
_MAX_CACHED_DATES = 80


def _today() -> str:
    return datetime.now(_TW).strftime("%Y-%m-%d")


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _attach_macd(rows: list[dict], macd: dict, max_gap_min: int) -> list[dict]:
    """Tag each VWAP cross with the latest MACD divergence confirmed at or before it."""
    out = []
    for row in rows or []:
        hhmm = str(row.get("time") or "")[:5]
        events = ((macd or {}).get(str(row.get("stock_id") or "")) or {}).get("events") or []
        hits = [e for e in events if len(hhmm) == 5 and len(str(e.get("time") or "")) >= 5
                and str(e["time"])[:5] <= hhmm]
        latest = max(hits, key=lambda e: str(e["time"])[:5], default=None)
        if latest and _minutes(hhmm) - _minutes(str(latest["time"])[:5]) <= max_gap_min:
            row = {**row, "macd_kind": latest.get("kind"), "macd_time": str(latest["time"])[:5]}
        out.append(row)
    return out


def _signal_side(row: dict, strategy: str) -> str | None:
    if strategy == "macd_vwap":
        direction, kind = row.get("direction"), row.get("macd_kind")
        if direction == "up" and kind == "bull":
            return "long"
        if direction == "down" and kind == "bear":
            return "short"
        return None
    if strategy == "vwap_cross":
        direction = row.get("direction")
        return {"up": "long", "down": "short"}.get(direction)
    kind, vwap_dir = row.get("sr_kind"), row.get("vwap_dir")
    if vwap_dir == "up" and kind in ("resistance", "both"):
        return "long"
    if vwap_dir == "down" and kind in ("support", "both"):
        return "short"
    return None


def _candidates(rows: list[dict], activity: dict, strategy: str = DEFAULT_STRATEGY) -> list[dict]:
    """First qualifying signal per stock, in time order."""
    rules = STRATEGIES[strategy]
    windows = rules.get("windows") or [rules["window"]]
    only_side = rules["side"] if rules["side"] != "both" else None
    picked: dict[str, dict] = {}
    for row in rows or []:
        sid = str(row.get("stock_id") or "")
        hhmm = str(row.get("time") or "")[:5]
        if not sid or len(hhmm) != 5:
            continue
        side = _signal_side(row, strategy)
        if side is None or (only_side and side != only_side):
            continue
        if not any(start <= hhmm < end for start, end in windows):
            continue
        act = activity.get(sid) or {}
        atr, vol_pr = _num(act.get("day_atr")), _num(act.get("vol5_pr"))
        if atr is None or vol_pr is None or atr < rules["min_day_atr"] or vol_pr < rules["min_vol5_pr"]:
            continue
        prev = picked.get(sid)
        if prev is None or hhmm < prev["signal_time"]:
            picked[sid] = {
                "stock_id": sid,
                "name": row.get("name") or "",
                "side": side,
                "signal_time": hhmm,
                "signal_price": _num(row.get("price")),
                "support": _num(row.get("support")),
                "resistance": _num(row.get("resistance")),
                "day_atr": atr,
                "vol5_pr": vol_pr,
                "macd_time": row.get("macd_time"),
            }
    return sorted(picked.values(), key=lambda item: (item["signal_time"], item["stock_id"]))


def _day_bars(stock_id: str, date_str: str) -> pd.DataFrame:
    from pattern.data_loader import get_stock_candles

    try:
        df = get_stock_candles(stock_id, timeframe="1m", date=date_str, limit=100000, full_day=True)
    except Exception as exc:
        print(f"[交易統計] {stock_id} {date_str} 分K讀取失敗: {exc}", flush=True)
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    if getattr(df["date"].dt, "tz", None) is not None:
        df["date"] = df["date"].dt.tz_localize(None)
    df = df[df["date"].dt.strftime("%Y-%m-%d") == date_str]
    df["hhmm"] = df["date"].dt.strftime("%H:%M")
    df = df[df["hhmm"] <= RULES["last_bar"]]
    return df.drop_duplicates("hhmm", keep="last").sort_values("hhmm").reset_index(drop=True)


def _simulate(cand: dict, bars: pd.DataFrame, session_closed: bool) -> dict:
    trade = {**cand, "status": "等待進場", "entry_time": None, "entry_price": None,
             "exit_time": None, "exit_price": None, "last_price": None, "gross_pct": None, "net_pct": None}
    if bars.empty:
        trade["status"] = "缺分K" if session_closed else "等待進場"
        return trade
    after = bars[bars["hhmm"] > cand["signal_time"]]
    if after.empty:
        trade["status"] = "無法進場" if session_closed else "等待進場"
        return trade

    entry = float(after["open"].iloc[0])
    if not entry > 0:
        trade["status"] = "無法進場"
        return trade
    trade["entry_time"] = after["hhmm"].iloc[0]
    trade["entry_price"] = round(entry, 2)
    sign = 1 if cand.get("side") == "long" else -1
    tp_price = entry * (1 + sign * RULES["take_profit_pct"] / 100)
    sl_price = entry * (1 - sign * RULES["stop_loss_pct"] / 100)

    exit_price = exit_time = None
    for bar in after.itertuples(index=False):
        high, low = float(bar.high), float(bar.low)
        if (low <= sl_price) if sign > 0 else (high >= sl_price):
            exit_price, exit_time, trade["status"] = sl_price, bar.hhmm, "停損"
            break
        if (high >= tp_price) if sign > 0 else (low <= tp_price):
            exit_price, exit_time, trade["status"] = tp_price, bar.hhmm, "停利"
            break

    last_close = float(after["close"].iloc[-1])
    trade["last_price"] = round(last_close, 2)
    if exit_price is None:
        if session_closed or after["hhmm"].iloc[-1] >= RULES["last_bar"]:
            exit_price, exit_time, trade["status"] = last_close, after["hhmm"].iloc[-1], "收盤平倉"
        else:
            trade["status"] = "持有中"
            gross = sign * (last_close - entry) / entry * 100
            trade["gross_pct"] = round(gross, 3)
            trade["net_pct"] = round(gross - RULES["cost_pct"], 3)
            return trade

    gross = sign * (exit_price - entry) / entry * 100
    trade["exit_time"] = exit_time
    trade["exit_price"] = round(exit_price, 2)
    trade["gross_pct"] = round(gross, 3)
    trade["net_pct"] = round(gross - RULES["cost_pct"], 3)
    return trade


def _summary(trades: list[dict]) -> dict:
    closed = [t["net_pct"] for t in trades if t["status"] in {"停利", "停損", "收盤平倉"}]
    open_ = [t["net_pct"] for t in trades if t["status"] == "持有中"]
    wins = sum(1 for x in closed if x > 0)
    return {
        "signals": len(trades),
        "closed": len(closed),
        "open": len(open_),
        "wins": wins,
        "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
        "total_net_pct": round(sum(closed), 3) if closed else 0.0,
        "avg_net_pct": round(sum(closed) / len(closed), 3) if closed else None,
        "open_net_pct": round(sum(open_), 3) if open_ else 0.0,
    }


def _compute(date_str: str, strategy: str) -> dict:
    import api

    today = _today()
    now = datetime.now(_TW)
    session_closed = date_str < today or (date_str == today and now.strftime("%H:%M") >= "13:31")
    if strategy == "vwap_cross":
        rows = api.vwap_breakout_today(date=date_str)
    elif strategy == "macd_vwap":
        macd = (api.vwap_macd_div(date=date_str) or {}).get("stocks") or {}
        rows = _attach_macd(api.vwap_breakout_today(date=date_str), macd,
                            STRATEGIES[strategy]["macd_max_gap_min"])
    else:
        rows = api.sr_vwap_cross_today(date=date_str)
    activity = (api.vwap_activity(date=date_str) or {}).get("stocks") or {}
    trades = [_simulate(cand, _day_bars(cand["stock_id"], date_str), session_closed)
              for cand in _candidates(rows, activity, strategy)]
    return {
        "date": date_str,
        "strategy": strategy,
        "is_today": date_str == today,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "activity_ready": bool(activity),
        "rules": STRATEGIES[strategy],
        "summary": _summary(trades),
        "trades": trades,
    }


@router.get("/api/trade_stats", tags=["VWAP"], summary="VWAP + 支撐做空模擬交易紀錄")
def trade_stats(date: str | None = None, strategy: str = DEFAULT_STRATEGY) -> dict:
    date_str = str(date or _today())[:10]
    strategy = strategy if strategy in STRATEGIES else DEFAULT_STRATEGY
    today = _today()
    if date_str > today:
        return {"date": date_str, "strategy": strategy, "is_today": False, "activity_ready": False,
                "rules": STRATEGIES[strategy], "summary": _summary([]), "trades": []}
    key = f"{date_str}|{strategy}"
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        stamp, result = cached
        if date_str < today or time.monotonic() - stamp < _LIVE_TTL_SECONDS:
            return result

    result = _compute(date_str, strategy)
    # Past days are fixed; keep them, unless nothing was found (data may still be syncing).
    if date_str == today or result["trades"]:
        with _cache_lock:
            _cache[key] = (time.monotonic(), result)
            while len(_cache) > _MAX_CACHED_DATES:
                _cache.pop(min(_cache), None)
    return result
