"""Plain M1 charts (candles + VWAP ±2σ, no signals) of chosen stock-days, from the _paths dump of
scripts.m1_vwap_band, for Ray to eyeball the volatility pool.

    python -m scripts.chart_pool_days --paths pick10_paths.parquet --info pool_pick10.parquet --out charts/
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", required=True)
    ap.add_argument("--info", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths = pd.read_parquet(args.paths)
    paths["stock_id"] = paths["stock_id"].astype(str)
    info = pd.read_parquet(args.info)
    info["stock_id"] = info["stock_id"].astype(str)
    info = info.set_index(["date", "stock_id"])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for n, ((d, sid), b) in enumerate(paths.groupby(["date", "stock_id"], sort=True), 1):
        b = b.sort_values("time").reset_index(drop=True)
        x = np.arange(len(b))
        o, h, l, c = (b[k].to_numpy(float) for k in "ohlc")
        fig, ax = plt.subplots(figsize=(13, 5))
        ax.vlines(x, l, h, color="black", linewidth=0.5)
        ax.bar(x, np.abs(c - o).clip(min=1e-6), bottom=np.minimum(o, c), color=np.where(c >= o, "tab:red", "tab:green"), width=0.8)
        ax.plot(x, b["vwap"], color="tab:blue", linewidth=1.2, label="VWAP")
        ax.plot(x, b["up"], color="tab:blue", linewidth=0.8, linestyle="--", label="+/-2sd")
        ax.plot(x, b["dn"], color="tab:blue", linewidth=0.8, linestyle="--")
        if "prev_close" in b:
            ax.axhline(float(b["prev_close"].iloc[0]), color="gray", linewidth=0.8, linestyle=":", label="prev close")
        for t in ("10:00", "12:00"):
            if t in b["time"].tolist():
                ax.axvline(b["time"].tolist().index(t), color="gray", linewidth=0.6, alpha=0.5)
        step = max(1, len(b) // 12)
        ax.set_xticks(x[::step], b["time"].to_numpy()[::step])
        ax.legend(loc="best", fontsize=8)
        r = info.loc[(d, sid)] if (d, sid) in info.index else None
        extra = f"  ATR {r['day_atr']:.1f}%  PR {r['vol5_pr']:.0f}  open5 range {r['open5_rng']:.1f}%" if r is not None else ""
        ax.set_title(f"{sid} {d}{extra}")
        fig.tight_layout()
        fig.savefig(out / f"{n:02d}_{sid}_{d}.png", dpi=80)
        plt.close(fig)
        print("saved", sid, d)


if __name__ == "__main__":
    main()
