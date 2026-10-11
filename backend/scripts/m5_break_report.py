"""Short when an M5 red bar is followed by an M5 that breaks its low.

1. Stock-days that pass the dashboard filters: ATR14 >= 5% and the first-5-minute
   volume PR >= 50 (both from vwap_activity, the same numbers the site uses).
2. On M1 bars grouped into M5 (09:00-09:04, 09:05-09:09, ...): the previous M5
   closed red (close > open) and an M1 inside the current M5 closes below that
   red bar's low. The first such M1 of the day is the signal; short at the next
   M1 open. One trade per stock per day.
3. Exit at +TP% / -SL% (stop wins when one bar hits both), else the 13:24 close.
4. Split by the 5 M1 bars up to the signal: accelerating or not (see
   d1_div_vwap_report.speed_features), and by Jul-Aug / Sep-Oct.

A random-minute short on the same stock-days is printed as a baseline.

    python -m scripts.m5_break_report --from-date 2026-07-01 --to-date 2026-10-08
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
import pandas as pd

import trade_stats_api as ts
from data.adjustment_query import load_pattern_day
from pattern.vwap_activity import metrics_for_date as activity_for_date
from pattern.vwap_sr_scan import stock_ids_for_universe
from scripts import d1_div_vwap_report as d1r
from scripts.trade_stats_report import one_at_a_time, streaks

FIRST_SIGNAL = "09:05"


def m5_bars(bars: pd.DataFrame) -> pd.DataFrame:
    b = bars.copy()
    mins = b["hhmm"].str[:2].astype(int) * 60 + b["hhmm"].str[3:].astype(int)
    b["bucket"] = mins // 5
    g = b.groupby("bucket", sort=True)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "start": g["hhmm"].first()})
    return out.reset_index()


def find_signal(bars: pd.DataFrame) -> dict | None:
    """First M1 that closes below the low of a red previous M5."""
    if bars.empty:
        return None
    m5 = m5_bars(bars).set_index("bucket")
    mins = bars["hhmm"].str[:2].astype(int) * 60 + bars["hhmm"].str[3:].astype(int)
    for hhmm, close, minute in zip(bars["hhmm"], bars["close"].astype(float), mins):
        if hhmm < FIRST_SIGNAL or hhmm >= ts.RULES["last_bar"]:
            continue
        prev = m5.loc[minute // 5 - 1] if (minute // 5 - 1) in m5.index else None
        if prev is None or not (prev["close"] > prev["open"]):
            continue
        if close < prev["low"]:
            return {"signal_time": hhmm, "red_start": prev["start"], "red_low": float(prev["low"])}
    return None


def chart_m5(t: dict, bars: pd.DataFrame, title: str, path: Path) -> None:
    import matplotlib.pyplot as plt

    m5 = m5_bars(bars)
    x = np.arange(len(m5))
    fig, ax = plt.subplots(figsize=(11, 5))
    up = m5["close"] >= m5["open"]
    ax.vlines(x, m5["low"], m5["high"], color="black", linewidth=0.6)
    ax.bar(x, (m5["close"] - m5["open"]).abs().clip(lower=1e-6), bottom=m5[["open", "close"]].min(axis=1),
           color=np.where(up, "tab:red", "tab:green"), width=0.7)
    starts = m5["start"].tolist()
    if t["red_start"] in starts:
        i = starts.index(t["red_start"])
        ax.axvspan(i - 0.45, i + 0.45, color="gold", alpha=0.3, label="red M5")
        ax.hlines(t["red_low"], i - 0.5, i + 1.5, color="blue", linestyle="--", label="red M5 low")
        ax.axvspan(i + 0.55, i + 1.45, color="tab:blue", alpha=0.12, label=f"break M5 (M1 close {t['signal_time']})")
    step = max(1, len(m5) // 12)
    ax.set_xticks(x[::step], starts[::step])
    ax.legend(loc="best")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


def table(trades: list[dict], label: str) -> None:
    net = [t["net_pct"] for t in trades]
    if not net:
        print(f"| {label} | 0 | - | - | - | - |")
        return
    exits = "/".join(str(sum(t["status"] == k for t in trades)) for k in ("停利", "停損", "收盤平倉"))
    print(f"| {label} | {len(net)} | {sum(x > 0 for x in net) / len(net) * 100:.1f}% | {sum(net) / len(net):+.3f}% "
          f"| {sum(net):+.1f}% | {exits} |")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--tp", type=float, default=3.5)
    parser.add_argument("--sl", type=float, default=3.0)
    parser.add_argument("--chart-dir", default="db/m5_break_charts")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    d1r.TP, d1r.SL = args.tp, args.sl
    rules = {**ts.RULES, "take_profit_pct": args.tp, "stop_loss_pct": args.sl}
    rng = random.Random(args.seed)

    stock_ids = stock_ids_for_universe("daytrade")
    day = load_pattern_day(start_date=args.from_date, end_date=args.to_date)
    trade_days = sorted(d for d in day["date"].dt.strftime("%Y-%m-%d").unique() if args.from_date <= d <= args.to_date)

    picked, signals, trades, randoms, bars_cache = [], [], [], [], {}
    for d in trade_days:
        act = activity_for_date(d) or {}
        for sid, v in act.items():
            sid = str(sid)
            if sid not in stock_ids:
                continue
            atr, vol = v.get("day_atr"), v.get("vol5_pr")
            if atr is None or vol is None or atr < rules["min_day_atr"] or vol < rules["min_vol5_pr"]:
                continue
            picked.append((d, sid))
            bars = ts._day_bars(sid, d)
            if bars.empty:
                continue
            bars_cache[(sid, d)] = bars
            sig = find_signal(bars)
            if sig:
                cand = {"stock_id": sid, "date": d, "side": "short", **sig}
                signals.append(cand)
                tr = ts._simulate(cand, bars, True, rules)
                if tr["net_pct"] is not None:
                    sp = d1r.speed_features(bars, sig["signal_time"], "short") or {"m5_move": None, "accel": None}
                    trades.append({**tr, "date": d, **sp})
            mins = [h for h in bars["hhmm"] if FIRST_SIGNAL <= h < "13:00"]
            if mins:
                rt = ts._simulate({"stock_id": sid, "date": d, "side": "short", "signal_time": rng.choice(mins)},
                                  bars, True, rules)
                if rt["net_pct"] is not None:
                    randoms.append({**rt, "date": d})
        print(f"{d}  過濾後 {sum(1 for p in picked if p[0] == d)} 檔，累計訊號 {len(signals)}", flush=True)

    print(f"\nStep 1  ATR≥5% + 開盤5分量PR≥50 的股票日：{len(picked)}")
    print(f"Step 2  上一根 M5 收紅、這根 M5 跌破其低點（M1 收盤跌破）：{len(signals)}")
    print(f"Step 3  完成交易：{len(trades)}")

    periods = (("全期", lambda d: True), ("7-8月", lambda d: d < "2026-09-01"), ("9-10月", lambda d: d >= "2026-09-01"))
    groups = (("全部", lambda t: True), ("加速", lambda t: t.get("accel") is True),
              ("沒加速", lambda t: t.get("accel") is False))
    print(f"\n===== M5 紅K後跌破前低 做空（停利 {args.tp:g}% / 停損 {args.sl:g}%，成本 {rules['cost_pct']}%）=====")
    print("| 分組 | 筆數 | 勝率 | 平均 | 合計 | 停利/停損/收盤 |")
    print("|---|---|---|---|---|---|")
    for gname, gf in groups:
        for pname, pf in periods:
            table([t for t in trades if gf(t) and pf(t["date"])], f"{gname} {pname}")
    for pname, pf in periods:
        table([t for t in randoms if pf(t["date"])], f"對照：同股票日隨機時間做空 {pname}")
    print("\n----- 依訊號時間 -----")
    print("| 分組 | 筆數 | 勝率 | 平均 | 合計 | 停利/停損/收盤 |")
    print("|---|---|---|---|---|---|")
    for lo, hi in (("09:05", "10:00"), ("10:00", "11:00"), ("11:00", "12:00"), ("12:00", "13:25")):
        for pname, pf in periods[1:]:
            table([t for t in trades if lo <= t["signal_time"] < hi and pf(t["date"])], f"{lo}-{hi} {pname}")

    print("\n----- 連勝連輸與加倍（一次只拿一筆）-----")
    print("| 分組 | 筆數 | 最多連勝 | 最多連輸 | 連輸4次 | 每筆1單位 | 輸加倍 | 贏加倍 | 連勝1/2/3/4/5+ | 連輸1/2/3/4/5+ |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for gname, gf in groups:
        for pname, pf in periods:
            sel = [t for t in trades if gf(t) and pf(t["date"])]
            if not sel:
                continue
            r = streaks(one_at_a_time(sel))
            print(f"| {gname} {pname} | {r['n']} | {r['max_win']} | {r['max_streak']} | {r['busts']} | {r['flat']:+.1f}% "
                  f"| {r['mart']:+.1f}% | {r['anti']:+.1f}% | {'/'.join(map(str, r['dist']['win']))} "
                  f"| {'/'.join(map(str, r['dist']['loss']))} |")

    out = Path(args.chart_dir)
    out.mkdir(parents=True, exist_ok=True)
    print("\n抽樣圖：")
    for i, s in enumerate(rng.sample(signals, min(3, len(signals))), 1):
        p = out / f"step1_{i}_{s['stock_id']}_{s['date']}.png"
        chart_m5(s, bars_cache[(s["stock_id"], s["date"])],
                 f"Step 1  M5 red then break  {s['stock_id']}  {s['date']}  red M5 {s['red_start']}  "
                 f"break {s['signal_time']}", p)
        print(f"  {p.name}  紅K {s['red_start']} 低 {s['red_low']}  跌破 {s['signal_time']}")
    for tag, want in (("accel", True), ("noaccel", False)):
        pool = [t for t in trades if t.get("accel") is want]
        for i, t in enumerate(rng.sample(pool, min(3, len(pool))), 1):
            p = out / f"step2_{tag}_{i}_{t['stock_id']}_{t['date']}.png"
            d1r.chart_intraday(t, bars_cache[(t["stock_id"], t["date"])], t,
                               f"Step 2  {'Accelerating' if want else 'Not accelerating'}  short  {t['stock_id']}  "
                               f"{t['date']}  5-bar {t['m5_move']:+.2f}%  in {t['entry_time']}  "
                               f"out {t['exit_time']}  net {t['net_pct']:+.2f}%", p, span=True)
            print(f"  {p.name}  5根 {t['m5_move']:+.2f}%  {t['status']} {t['net_pct']:+.2f}%")


if __name__ == "__main__":
    main()
