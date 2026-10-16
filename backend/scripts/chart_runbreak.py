"""M1 chart with volume for scripts.m1_runbreak_scan events: high-volume run orange, trigger bar (max-volume / longest) red hatch, break bar gold, level dashed.

    python -m scripts.chart_runbreak --paths runbreak_2026_paths.parquet --events runbreak_2026.parquet --out charts/
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
    ap.add_argument("--pick", help="parquet of selected event rows (with a 'tag' column): one chart per row")
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
    if args.pick:
        pk = pd.read_parquet(args.pick)
        pk["stock_id"] = pk["stock_id"].astype(str)
        jobs = [(f"{r['tag']}_{n:02d}", r["date"], r["stock_id"], pk.iloc[[k]]) for n, (k, r) in enumerate(pk.iterrows(), 1)]
    else:
        jobs = [(f"{n:02d}", d, sid, None) for n, (d, sid) in enumerate(sorted(set(zip(paths["date"], paths["stock_id"]))), 1)]
    for tag, d, sid, only in jobs:
        b = paths[(paths["date"] == d) & (paths["stock_id"] == sid)].sort_values("time").reset_index(drop=True)
        if b.empty:
            print("no path", sid, d)
            continue
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
        e = only if only is not None else ev[(ev["stock_id"] == sid) & (ev["date"] == d)]
        for _, r in e.iterrows():
            if r["run_start"] not in times or r["time"] not in times:
                continue
            i0, i1, t, bi = times.index(r["run_start"]), times.index(r["run_end"]), times.index(r["time"]), times.index(r["bar"])
            for a in (ax, av):
                a.axvspan(i0 - 0.5, i1 + 0.5, color="orange", alpha=0.25)
                a.axvspan(bi - 0.5, bi + 0.5, facecolor="none", edgecolor="red", hatch="//", linewidth=0.8)
                a.axvspan(t - 0.5, t + 0.5, color="gold", alpha=0.6)
            ax.hlines(float(r["level"]), i0, t + 10, color="purple", linestyle="--", linewidth=0.8)
            ax.annotate(f"{r['side']} {r['trigger']} run {r['move']:+.1f}%", (t, l[t]), xytext=(0, -14), textcoords="offset points",
                        ha="center", fontsize=8, color="purple")
        av.set_ylabel("volume")
        step = max(1, len(b) // 12)
        av.set_xticks(x[::step], b["time"].to_numpy()[::step])
        ax.legend(loc="best", fontsize=8)
        extra = f"  net {float(e['net'].iloc[0]):+.2f}% ({ {1: 'TP', -1: 'SL', 0: 'close'}[int(e['exit'].iloc[0])] })" if only is not None else f", {len(e)} events"
        ax.set_title(f"{sid} {d}  run (orange), trigger bar (red hatch), break (gold){extra}")
        fig.tight_layout()
        fig.savefig(out / f"{tag}_{sid}_{d}.png", dpi=80)
        plt.close(fig)
        print("saved", sid, d, len(e))


if __name__ == "__main__":
    main()
