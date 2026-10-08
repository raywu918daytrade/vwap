"""Try combinations of dashboard conditions on top of a VWAP cross.

Each VWAP cross (09:05-13:24, dashboard ATR / volume PR filters) is a possible
entry: cross up -> long, cross down -> short. Optional conditions, each on or
off, must have happened within --gap minutes before the cross, on the same side:

- sr:   a VWAP + support/resistance light (long: above VWAP at resistance,
        short: below VWAP at support)
- macd: a MACD histogram divergence (long: bull, short: bear)
- obv:  an OBV divergence (long: bull, short: bear)
- slot: the cross is inside 10:00-12:15
- chase: skip longs already up >= 5% and shorts already down <= -5%

First qualifying cross per stock per day, entered at the next minute's open.
Results are split into a train range and a test range, so a combo that only
looks good on the data it was picked from is easy to spot.

    python -m scripts.combo_report --train 2026-07-01 2026-08-31 --test 2026-09-01 2026-10-07
"""

from __future__ import annotations

import argparse
import itertools

import pandas as pd

import trade_stats_api as ts
from pattern.vwap_activity import metrics_for_date as activity_for_date
from pattern.vwap_signal_store import read_vwap_signals
from pattern.vwap_sr_scan import prev_close_for_date, stock_ids_for_universe

CONDITIONS = ["sr", "macd", "obv", "slot", "chase"]
EXITS = [(2.0, 4.0), (3.0, 3.0), (2.0, 2.0)]
SLOT = ("10:00", "12:15")
CHASE_PCT = 5.0
RULES = ts.STRATEGIES["vwap_cross"]


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _recent(times: list[str], hhmm: str, gap: int) -> bool:
    now = _minutes(hhmm)
    return any(0 <= now - _minutes(t) <= gap for t in times)


def _div_times(div_map: dict, sid: str, kind: str) -> list[str]:
    events = ((div_map or {}).get(sid) or {}).get("events") or []
    return [str(e["time"])[:5] for e in events if e.get("kind") == kind and len(str(e.get("time") or "")) >= 5]


def day_events(date_str: str, stock_ids: set[str], gap: int) -> list[dict]:
    """Every filtered VWAP cross of the day with its condition flags."""
    bundle = read_vwap_signals(date_str, stock_ids=stock_ids)
    activity = activity_for_date(date_str) or {}
    if not bundle or not activity:
        return []
    prev_close = prev_close_for_date(date_str)
    sr_times: dict[tuple[str, str], list[str]] = {}
    for row in bundle.get("sr") or []:
        side = ts._signal_side(row, "sr_short")
        if side:
            sr_times.setdefault((str(row.get("stock_id")), side), []).append(str(row.get("time") or "")[:5])
    out = []
    for row in bundle.get("vwap") or []:
        sid, hhmm = str(row.get("stock_id") or ""), str(row.get("time") or "")[:5]
        side = {"up": "long", "down": "short"}.get(row.get("direction"))
        if not sid or len(hhmm) != 5 or side is None:
            continue
        if not (RULES["window"][0] <= hhmm < RULES["window"][1]):
            continue
        act = activity.get(sid) or {}
        atr, vol_pr = ts._num(act.get("day_atr")), ts._num(act.get("vol5_pr"))
        if atr is None or vol_pr is None or atr < RULES["min_day_atr"] or vol_pr < RULES["min_vol5_pr"]:
            continue
        kind = "bull" if side == "long" else "bear"
        chg = ts._chg_pct(ts._num(row.get("price")), prev_close.get(sid))
        out.append({
            "date": date_str, "stock_id": sid, "side": side, "signal_time": hhmm,
            "sr": _recent(sr_times.get((sid, side), []), hhmm, gap),
            "macd": _recent(_div_times(bundle.get("macd"), sid, kind), hhmm, gap),
            "obv": _recent(_div_times(bundle.get("obv"), sid, kind), hhmm, gap),
            "slot": SLOT[0] <= hhmm < SLOT[1],
            "chase": chg is not None and (chg < CHASE_PCT if side == "long" else chg > -CHASE_PCT),
        })
    return sorted(out, key=lambda e: (e["signal_time"], e["stock_id"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", nargs=2, required=True)
    parser.add_argument("--test", nargs=2, required=True)
    parser.add_argument("--gap", type=int, default=30, help="minutes a condition stays valid before the cross")
    parser.add_argument("--min-trades", type=int, default=30)
    args = parser.parse_args()

    stock_ids = stock_ids_for_universe("daytrade")
    periods = {"train": args.train, "test": args.test}
    events: dict[str, list[dict]] = {}
    for period, (start, end) in periods.items():
        events[period] = []
        for d in pd.bdate_range(start, end):
            evs = day_events(d.strftime("%Y-%m-%d"), stock_ids, args.gap)
            events[period] += evs
            print(f"{d:%Y-%m-%d}: {len(evs)} crosses", flush=True)

    bars: dict[tuple[str, str], pd.DataFrame] = {}
    sims: dict[tuple, float] = {}

    def net(ev: dict, tp: float, sl: float) -> float | None:
        key = (ev["date"], ev["stock_id"], ev["signal_time"], ev["side"], tp, sl)
        if key not in sims:
            bkey = (ev["stock_id"], ev["date"])
            if bkey not in bars:
                bars[bkey] = ts._day_bars(ev["stock_id"], ev["date"])
            rules = {**RULES, "take_profit_pct": tp, "stop_loss_pct": sl}
            sims[key] = ts._simulate(ev, bars[bkey], True, rules)["net_pct"]
        return sims[key]

    def stats(evs: list[dict], combo: tuple[str, ...], side: str, tp: float, sl: float) -> dict:
        picked: dict[tuple[str, str], dict] = {}
        for ev in evs:
            if ev["side"] != side or not all(ev[c] for c in combo):
                continue
            picked.setdefault((ev["date"], ev["stock_id"]), ev)
        nets = [x for x in (net(ev, tp, sl) for ev in picked.values()) if x is not None]
        if not nets:
            return {"n": 0, "win": None, "avg": None}
        return {"n": len(nets), "win": sum(x > 0 for x in nets) / len(nets) * 100, "avg": sum(nets) / len(nets)}

    rows = []
    for k in range(len(CONDITIONS) + 1):
        for combo in itertools.combinations(CONDITIONS, k):
            for side in ("long", "short"):
                for tp, sl in EXITS:
                    tr = stats(events["train"], combo, side, tp, sl)
                    if tr["n"] < args.min_trades:
                        continue
                    te = stats(events["test"], combo, side, tp, sl)
                    rows.append({"combo": "+".join(combo) or "(只有 VWAP 穿越)", "side": side, "exit": f"{tp:g}/{sl:g}",
                                 "train_n": tr["n"], "train_win": tr["win"], "train_avg": tr["avg"],
                                 "test_n": te["n"], "test_win": te["win"], "test_avg": te["avg"]})
    df = pd.DataFrame(rows).sort_values("train_avg", ascending=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    print(f"\n===== 依訓練期平均損益排序（訓練 {args.train[0]}~{args.train[1]}，驗證 {args.test[0]}~{args.test[1]}）=====")
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()
