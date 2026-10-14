"""M1 chart with volume for the engulfing-bar events of scripts.m1_engulf_scan: price panel (candles, VWAP ±2σ,
prev close) with each event bar highlighted and its N written above, volume panel below.

    python -m scripts.chart_engulf --paths engulf_2026_paths.parquet --events engulf_2026.parquet --out charts/
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-n", type=int, default=5)
    args = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths = pd.read_parquet(args.paths)
    ev = pd.read_parquet(args.events)
    for d in (paths, ev):
        d["stock_id"] = d["stock_id"].astype(str)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for n, ((d, sid), b) in enumerate(paths.groupby(["date", "stock_id"], sort=True), 1):
        b = b.sort_values("time").reset_index(drop=True)
        x = np.arange(len(b))
        o, h, l, c, v = (b[k].to_numpy(float) for k in "ohlcv")
        fig, (ax, av) = plt.subplots(2, 1, figsize=(13, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        up = c >= o
        ax.vlines(x, l, h, color="black", linewidth=0.5)
        ax.bar(x, np.abs(c - o).clip(min=1e-6), bottom=np.minimum(o, c), color=np.where(up, "tab:red", "tab:green"), width=0.8)
        ax.plot(x, b["vwap"], color="tab:blue", linewidth=1.2, label="VWAP")
        ax.plot(x, b["up"], color="tab:blue", linewidth=0.8, linestyle="--", label="+/-2sd")
        ax.plot(x, b["dn"], color="tab:blue", linewidth=0.8, linestyle="--")
        ax.axhline(float(b["prev_close"].iloc[0]), color="gray", linewidth=0.8, linestyle=":", label="prev close")
        times = b["time"].tolist()
        e = ev[(ev["stock_id"] == sid) & (ev["date"] == d) & (ev["n"] >= args.min_n)]
        for _, r in e.iterrows():
            if r["time"] not in times:
                continue
            i = times.index(r["time"])
            ax.axvspan(i - 0.5, i + 0.5, color="gold", alpha=0.35)
            ax.annotate(f"N={int(r['n'])}", (i, h[i]), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=8, color="purple")
            av.bar([i], [v[i]], color="gold", width=0.8, zorder=3)
        av.bar(x, v, color=np.where(up, "tab:red", "tab:green"), width=0.8, alpha=0.7)
        av.set_ylabel("volume")
        step = max(1, len(b) // 12)
        av.set_xticks(x[::step], b["time"].to_numpy()[::step])
        ax.legend(loc="best", fontsize=8)
        ax.set_title(f"{sid} {d}  one M1 bar covering previous N bars (N>={args.min_n}), {len(e)} events")
        fig.tight_layout()
        fig.savefig(out / f"{n:02d}_{sid}_{d}.png", dpi=80)
        plt.close(fig)
        print("saved", sid, d, len(e))


if __name__ == "__main__":
    main()
