"""Draw verification charts for picked short trades: D1 (last 40 days + MA5/10/20) and the day's M5 with VWAP.

Input CSV columns: date, stock_id, setup_time, signal_m5_close, exit, short_net.
Marks the red+black setup pair, the M5 that broke VWAP, the entry (next M1 open), the 3% stop and the exit.

    python -m scripts.chart_trades --trades picks.csv --out charts/
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from data.adjustment_query import load_pattern_day
from scripts.build_feature_table_long import load_m1_month

TP, SL, LAST = 3.5, 3.0, "13:24"


def m5(bars: pd.DataFrame) -> pd.DataFrame:
    b = bars.copy()
    mins = b["hhmm"].str[:2].astype(int) * 60 + b["hhmm"].str[3:].astype(int)
    b["bucket"] = mins // 5
    g = b.groupby("bucket", sort=True)
    return pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                         "close": g["close"].last(), "start": g["hhmm"].first(), "end": g["hhmm"].last(),
                         "vwap": g["vwap"].last()}).reset_index()


def candles(ax, o, h, l, c, x) -> None:
    up = c >= o
    ax.vlines(x, l, h, color="black", linewidth=0.6)
    ax.bar(x, np.abs(c - o).clip(min=1e-6), bottom=np.minimum(o, c), color=np.where(up, "tab:red", "tab:green"), width=0.7)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    picks = pd.read_csv(args.trades, dtype={"stock_id": str})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ids = set(picks["stock_id"])
    day = load_pattern_day(start_date="2022-09-01")
    day["stock_id"] = day["stock_id"].astype(str)
    day = day[day["stock_id"].isin(ids)]
    m1_cache: dict[str, pd.DataFrame] = {}
    for n, t in enumerate(picks.itertuples(index=False), 1):
        stem = t.date[:7].replace("-", "_")
        if stem not in m1_cache:
            m1_cache[stem] = load_m1_month(stem, ids)
        bars = m1_cache[stem]
        bars = bars[(bars["stock_id"] == t.stock_id) & (bars["day"] == t.date)].reset_index(drop=True)
        if bars.empty:
            print("no M1", t.date, t.stock_id)
            continue
        v = bars["volume"].astype(float)
        bars["vwap"] = (bars["close"] * v).cumsum() / v.cumsum().clip(lower=1e-9)
        after = bars[bars["hhmm"] > t.signal_m5_close]
        entry = float(after["open"].iloc[0])
        entry_t = after["hhmm"].iloc[0]
        sl, tp = entry * (1 + SL / 100), entry * (1 - TP / 100)
        exit_p, exit_t = float(after["close"].iloc[-1]), after["hhmm"].iloc[-1]
        for b in after.itertuples(index=False):
            if b.high >= sl:
                exit_p, exit_t = sl, b.hhmm
                break
            if b.low <= tp:
                exit_p, exit_t = tp, b.hhmm
                break
        d = day[(day["stock_id"] == t.stock_id) & (day["date"] <= t.date)].sort_values("date").tail(60).reset_index(drop=True)
        c = d["close"].astype(float)
        mas = {k: c.rolling(k).mean() for k in (5, 10, 20)}
        d, mas = d.tail(40).reset_index(drop=True), {k: s.tail(40).reset_index(drop=True) for k, s in mas.items()}

        fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 5.5), gridspec_kw={"width_ratios": [1, 1.6]})
        x = np.arange(len(d))
        candles(a1, d["open"].to_numpy(float), d["high"].to_numpy(float), d["low"].to_numpy(float), d["close"].to_numpy(float), x)
        for k, col in ((5, "orange"), (10, "purple"), (20, "blue")):
            a1.plot(x, mas[k], color=col, linewidth=1, label=f"MA{k}")
        a1.axvspan(len(d) - 1.5, len(d) - 0.5, color="gray", alpha=0.15)
        a1.set_xticks(x[::8], d["date"].dt.strftime("%m-%d").to_numpy()[::8])
        a1.legend(loc="best", fontsize=8)
        a1.set_title(f"D1 {t.stock_id} (last bar = trade day)")

        b5 = m5(bars)
        x5 = np.arange(len(b5))
        candles(a2, b5["open"].to_numpy(float), b5["high"].to_numpy(float), b5["low"].to_numpy(float), b5["close"].to_numpy(float), x5)
        a2.plot(x5, b5["vwap"], color="blue", linewidth=1, label="VWAP")
        ends = b5["end"].tolist()

        def idx(hhmm: str) -> int:
            return int(np.searchsorted(np.array(ends), hhmm))

        si, ti = idx(t.setup_time), idx(t.signal_m5_close)
        a2.axvspan(si - 1.45, si + 0.45, color="gold", alpha=0.3, label=f"red+black setup ({t.setup_time})")
        a2.axvspan(ti - 0.45, ti + 0.45, color="tab:blue", alpha=0.15, label=f"VWAP break M5 ({t.signal_m5_close})")
        ei, xi = idx(entry_t), idx(exit_t)
        a2.scatter([ei], [entry], marker="v", color="black", s=60, zorder=5, label=f"short {entry_t} @ {entry:.2f}")
        a2.hlines(sl, ei, len(b5) - 1, color="red", linestyle="--", linewidth=0.8, label=f"stop +3% {sl:.2f}")
        a2.scatter([xi], [exit_p], marker="^", color="green", s=60, zorder=5, label=f"exit {exit_t} @ {exit_p:.2f} ({ {"停利": "TP", "停損": "SL", "收盤": "close"}.get(t.exit, t.exit)})")
        step = max(1, len(b5) // 12)
        a2.set_xticks(x5[::step], b5["start"].to_numpy()[::step])
        a2.legend(loc="best", fontsize=8)
        a2.set_title(f"{t.stock_id} {t.date} M5  net {t.short_net:+.2f}%")
        fig.tight_layout()
        name = f"{n:02d}_{t.stock_id}_{t.date}_{t.exit}.png"
        fig.savefig(out / name, dpi=85)
        plt.close(fig)
        print("saved", name, "entry", entry_t, round(entry, 2), "exit", exit_t, round(exit_p, 2))


if __name__ == "__main__":
    main()
