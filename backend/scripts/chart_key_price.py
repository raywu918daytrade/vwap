"""Charts to verify D1 key levels and the M5 reversal signal: left = last 40 D1 bars with the key levels
(the 20-day window shaded), right = the day's M5 (from 09:05) with the same levels and the signal bar.

    python -m scripts.chart_key_price --picks picks.csv --d1 d1_raw.parquet --m5 'm5_hl_*.parquet' --levels key_levels.parquet --band 1 --out charts/
"""

import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd



def candles(ax, o, h, l, c, x) -> None:
    up = c >= o
    ax.vlines(x, l, h, color="black", linewidth=0.6)
    ax.bar(x, np.abs(c - o).clip(min=1e-6), bottom=np.minimum(o, c), color=np.where(up, "tab:red", "tab:green"), width=0.7)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--picks", required=True)
    ap.add_argument("--d1", required=True)
    ap.add_argument("--m5", required=True)
    ap.add_argument("--levels", required=True)
    ap.add_argument("--band", type=float, default=1.0)
    ap.add_argument("--h1", default=None, help="draw the previous --lookback days of H1 on the left instead of D1")
    ap.add_argument("--lookback", type=int, default=5)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    picks = pd.read_csv(args.picks, dtype={"stock_id": str})
    keys = set(zip(picks["date"], picks["stock_id"]))
    d1 = pd.read_parquet(args.d1)
    d1["stock_id"] = d1["stock_id"].astype(str)
    d1["date"] = pd.to_datetime(d1["date"]).dt.strftime("%Y-%m-%d")
    m5 = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.m5))], ignore_index=True)
    m5["stock_id"] = m5["stock_id"].astype(str)
    m5 = m5[[k in keys for k in zip(m5["date"], m5["stock_id"])]]
    lv = pd.read_parquet(args.levels)
    lv["stock_id"] = lv["stock_id"].astype(str)
    lv = lv[lv["band"] == args.band]
    h1 = None
    if args.h1:
        h1 = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.h1))], ignore_index=True)
        h1["stock_id"] = h1["stock_id"].astype(str)
        h1 = h1[h1["stock_id"].isin(set(picks["stock_id"]))]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for n, t in enumerate(picks.itertuples(index=False), 1):
        d = d1[(d1["stock_id"] == t.stock_id) & (d1["date"] <= t.date)].tail(41).reset_index(drop=True)
        prev_close = float(d["close"].iloc[-2])
        levels = lv[(lv["stock_id"] == t.stock_id) & (lv["date"] == t.date)]
        prices = [(prev_close * (1 + r.level_pct / 100), r.n_days, r.level_pct) for r in levels.itertuples(index=False)]
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 5.5), gridspec_kw={"width_ratios": [1, 1.4]})
        if h1 is not None:
            hs = h1[(h1["stock_id"] == t.stock_id) & (h1["date"] <= t.date)].sort_values(["date", "hour"])
            days = sorted(hs["date"].unique())[-(args.lookback + 1):]
            hs = hs[hs["date"].isin(days)].reset_index(drop=True)
            # Raw M1 prices: put the levels on the H1 basis (previous day's last H1 close).
            prev_close = float(hs[hs["date"] < t.date]["c"].iloc[-1])
            prices = [(prev_close * (1 + r.level_pct / 100), r.n_days, r.level_pct) for r in levels.itertuples(index=False)]
            d = hs.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})
            n_today = int((d["date"] == t.date).sum())
            x = np.arange(len(d))
            candles(a1, *(d[k].to_numpy(float) for k in ("open", "high", "low", "close")), x)
            a1.axvspan(-0.5, len(d) - n_today - 0.5, color="gray", alpha=0.08)
            a1.axvspan(len(d) - n_today - 0.5, len(d) - 0.5, color="gold", alpha=0.25)
            for k in np.flatnonzero(d["hour"].to_numpy() == 0):
                a1.axvline(k - 0.5, color="gray", linewidth=0.4)
        else:
            x = np.arange(len(d))
            candles(a1, *(d[k].to_numpy(float) for k in ("open", "high", "low", "close")), x)
            a1.axvspan(len(d) - 21.5, len(d) - 1.5, color="gray", alpha=0.08)
            a1.axvspan(len(d) - 1.5, len(d) - 0.5, color="gold", alpha=0.25)
        for p, nd, pct in prices:
            a1.axhline(p, color="tab:blue", linewidth=0.9, linestyle="--")
            a1.text(0, p, f" {p:.2f} ({nd}d, {pct:+.1f}%)", va="bottom", fontsize=8, color="tab:blue")
        step1 = 5 if h1 is not None else 8
        a1.set_xticks(x[::step1], pd.to_datetime(d["date"]).dt.strftime("%m-%d").to_numpy()[::step1])
        a1.set_title(f"{'H1' if h1 is not None else 'D1'} {t.stock_id}  key levels ±{args.band:g}% (gray = lookback, gold = trade day)")
        b = m5[(m5["stock_id"] == t.stock_id) & (m5["date"] == t.date)].sort_values("time").reset_index(drop=True)
        px = {k: prev_close * (1 + b[f"m5_{k}_pct"].to_numpy(float) / 100) for k in "ohlc"}
        x5 = np.arange(len(b))
        candles(a2, px["o"], px["h"], px["l"], px["c"], x5)
        for p, nd, _ in prices:
            a2.axhline(p, color="tab:blue", linewidth=0.9, linestyle="--")
            a2.axhspan(p * (1 - args.band / 100), p * (1 + args.band / 100), color="tab:blue", alpha=0.06)
        i = int(np.searchsorted(b["time"].to_numpy(), t.time))
        a2.axvspan(i - 0.45, i + 0.45, color="tab:red", alpha=0.2, label=f"reversal M5 (ends {t.time})")
        a2.axhline(prev_close, color="gray", linewidth=0.6)
        step = max(1, len(b) // 12)
        a2.set_xticks(x5[::step], b["time"].to_numpy()[::step])
        a2.legend(loc="best", fontsize=8)
        a2.set_title(f"{t.stock_id} {t.date} M5  net(TP3.5/SL3) {t.short_net:+.2f}%  hold {t.short_hold:+.2f}%")
        fig.tight_layout()
        fig.savefig(out / f"{n:02d}_{t.stock_id}_{t.date}.png", dpi=85)
        plt.close(fig)
        print("saved", n, t.stock_id, t.date, t.time)


if __name__ == "__main__":
    main()
