"""M1 chart with VWAP ± 2σ band and the day's reversal signals, from the _paths dump of
scripts.m1_vwap_band.

    python -m scripts.chart_m1vb --paths 'm1vb_20??_paths.parquet' --signals 'm1vb_20??.parquet' --out charts/
"""

import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", required=True)
    ap.add_argument("--signals", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.paths))], ignore_index=True)
    sig = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.signals))], ignore_index=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for n, ((sid, d), b) in enumerate(paths.groupby(["stock_id", "date"], sort=True), 1):
        b = b.sort_values("time").reset_index(drop=True)
        x = np.arange(len(b))
        o, h, l, c = (b[k].to_numpy(float) for k in "ohlc")
        fig, ax = plt.subplots(figsize=(13, 5))
        ax.vlines(x, l, h, color="black", linewidth=0.5)
        ax.bar(x, np.abs(c - o).clip(min=1e-6), bottom=np.minimum(o, c), color=np.where(c >= o, "tab:red", "tab:green"), width=0.8)
        ax.plot(x, b["vwap"], color="tab:blue", linewidth=1.2, label="VWAP")
        ax.plot(x, b["up"], color="tab:blue", linewidth=0.8, linestyle="--", label="±2σ")
        ax.plot(x, b["dn"], color="tab:blue", linewidth=0.8, linestyle="--")
        ax.fill_between(x, b["dn"], b["up"], color="tab:blue", alpha=0.05)
        s = sig[(sig["stock_id"] == sid) & (sig["date"] == d)]
        times = b["time"].tolist()
        for side, mk, col in (("short", "v", "tab:purple"), ("long", "^", "tab:orange")):
            t = s[s["side"] == side]["time"]
            idx = [times.index(v) for v in t if v in times]
            if idx:
                ax.scatter(idx, [h[i] * 1.004 if side == "short" else l[i] * 0.996 for i in idx], marker=mk, color=col, s=45, zorder=5,
                           label=f"{side} signal ({len(idx)})")
        step = max(1, len(b) // 12)
        ax.set_xticks(x[::step], b["time"].to_numpy()[::step])
        ax.set_ylim(l[10:].min() * 0.995, h[10:].max() * 1.005)
        ax.legend(loc="best", fontsize=8)
        ax.set_title(f"{sid} {d}  M1 with VWAP ±2σ (signals 09:10-12:00)")
        fig.tight_layout()
        fig.savefig(out / f"{n:02d}_{sid}_{d}.png", dpi=80)
        plt.close(fig)
        print("saved", sid, d)


if __name__ == "__main__":
    main()
