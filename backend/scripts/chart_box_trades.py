"""Charts for box-study signals: left = H1 lookback with the box, right = the trade day's M5 with the
box edges and the signal bar.

    python -m scripts.chart_box_trades --picks picks_box.csv --h1 'h1_20*.parquet' --m5 'm5_hl_*.parquet' --boxes h1_boxes.parquet --out charts/
"""

import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.h1_box_zones import LOOKBACK, setups


def candles(ax, o, h, l, c, x) -> None:
    ax.vlines(x, l, h, color="black", linewidth=0.6)
    ax.bar(x, np.abs(c - o).clip(min=1e-6), bottom=np.minimum(o, c), color=np.where(c >= o, "tab:red", "tab:green"), width=0.7)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--picks", required=True)
    ap.add_argument("--h1", required=True)
    ap.add_argument("--m5", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    picks = pd.read_csv(args.picks, dtype={"stock_id": str})
    ids = set(picks["stock_id"])
    h1 = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.h1))], ignore_index=True)
    h1["stock_id"] = h1["stock_id"].astype(str)
    h1 = h1[h1["stock_id"].isin(ids) & (h1["h"] > 0)].sort_values(["stock_id", "date", "hour"])
    m5 = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.m5))], ignore_index=True)
    m5["stock_id"] = m5["stock_id"].astype(str)
    m5 = m5[m5["stock_id"].isin(ids)]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for n, t in enumerate(picks.itertuples(index=False), 1):
        g = h1[h1["stock_id"] == t.stock_id].reset_index(drop=True)
        days = sorted(g["date"].unique())
        k = days.index(t.date)
        hist = g[g["date"].isin(days[k - LOOKBACK:k])].reset_index(drop=True)
        o, c = hist["o"].to_numpy(float), hist["c"].to_numpy(float)
        day = np.unique(hist["date"].to_numpy(), return_inverse=True)[1]
        box = setups(o, c, day)[0 if t.side == "short" else 1]
        d = g[g["date"].isin(days[k - LOOKBACK:k + 1])].reset_index(drop=True)
        prev = c[-1]
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(16, 5.2), gridspec_kw={"width_ratios": [1.3, 1]})
        x = np.arange(len(d))
        candles(a1, *(d[q].to_numpy(float) for q in "ohlc"), x)
        a1.axvspan(len(hist) - 0.5, len(d) - 0.5, color="gold", alpha=0.2)
        i, j, lo, hi = box
        a1.add_patch(plt.Rectangle((i - 0.5, lo), j - i + 1, hi - lo, color="tab:blue", alpha=0.25))
        for a in (a1, a2):
            a.axhline(lo, color="tab:blue", linestyle="--", linewidth=0.8)
            a.axhline(hi, color="tab:blue", linestyle="--", linewidth=0.8)
            a.axhspan(lo, hi, color="tab:blue", alpha=0.07)
        ticks = np.flatnonzero(d["hour"].to_numpy() == 0)
        a1.set_xticks(ticks, [s[5:] for s in d["date"].to_numpy()[ticks]])
        a1.set_title(f"{t.stock_id} H1, box {lo:.2f}-{hi:.2f} ({'resistance' if t.side == 'short' else 'support'})")
        b = m5[(m5["stock_id"] == t.stock_id) & (m5["date"] == t.date)].sort_values("time").reset_index(drop=True)
        px = {q: prev * (1 + b[f"m5_{q}_pct"].to_numpy(float) / 100) for q in "ohlc"}
        x5 = np.arange(len(b))
        candles(a2, px["o"], px["h"], px["l"], px["c"], x5)
        si = int(np.searchsorted(b["time"].to_numpy(), t.time))
        a2.axvspan(si - 0.45, si + 0.45, color="tab:purple", alpha=0.25, label=f"signal M5 (ends {t.time}), enter next M1")
        step = max(1, len(b) // 10)
        a2.set_xticks(x5[::step], b["time"].to_numpy()[::step])
        a2.legend(loc="best", fontsize=8)
        net, hold = (t.short_net, t.short_hold) if t.side == "short" else (t.long_net, t.long_hold)
        a2.set_title(f"{t.date} M5 {t.side}: TP3.5/SL3 {net:+.2f}%, hold {hold:+.2f}%")
        fig.tight_layout()
        fig.savefig(out / f"{t.side}_{n:02d}_{t.stock_id}_{t.date}.png", dpi=80)
        plt.close(fig)
        print("saved", t.side, t.stock_id, t.date)


if __name__ == "__main__":
    main()
