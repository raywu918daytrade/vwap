"""M1 chart with volume for scripts.m1_dryup_scan events: high-volume run orange, dry box blue, break bar gold.

    python -m scripts.chart_dryup --paths dryup_2026_paths.parquet --events dryup_2026.parquet --out charts/
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
        ax.axhline(float(b["prev_close"].iloc[0]), color="gray", linewidth=0.8, linestyle=":", label="prev close")
        av.bar(x, v, color=np.where(up, "tab:red", "tab:green"), width=0.8, alpha=0.7)
        times = b["time"].tolist()
        e = ev[(ev["stock_id"] == sid) & (ev["date"] == d)]
        for _, r in e.iterrows():
            if r["run_start"] not in times or r["time"] not in times:
                continue
            i0, i1, t = times.index(r["run_start"]), times.index(r["run_end"]), times.index(r["time"])
            b1 = times.index(r["box_end"])
            for a in (ax, av):
                a.axvspan(i0 - 0.5, i1 + 0.5, color="orange", alpha=0.25)
                a.axvspan(i1 + 0.5, b1 + 0.5, color="tab:blue", alpha=0.15)
                a.axvspan(t - 0.5, t + 0.5, color="gold", alpha=0.6)
            ax.annotate(f"{r['side']} (run {r['move']:+.1f}%, box {int(r['box_n'])} bars)", (t, h[t]), xytext=(0, 8),
                        textcoords="offset points", ha="center", fontsize=8, color="purple")
        av.set_ylabel("volume")
        step = max(1, len(b) // 12)
        av.set_xticks(x[::step], b["time"].to_numpy()[::step])
        ax.legend(loc="best", fontsize=8)
        ax.set_title(f"{sid} {d}  high-volume run (orange), dry box (blue), break bar (gold), {len(e)} events")
        fig.tight_layout()
        fig.savefig(out / f"{n:02d}_{sid}_{d}.png", dpi=80)
        plt.close(fig)
        print("saved", sid, d, len(e))


if __name__ == "__main__":
    main()
