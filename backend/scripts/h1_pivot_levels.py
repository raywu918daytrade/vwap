"""H1 關鍵價 from turning points only, plus charts to check them.

A swing high is an H1 bar whose high is the highest of the K bars on each side (swing low: lowest
low). Ties count (a flat double bottom is two swing lows); bars within K of each other are one touch.
Bars at the window edges count when the bars they have agree. Swing highs and lows within ±band
of each other form a level when >= MIN_TOUCH of them (from different bars) agree; the level is their mean.
This avoids the earlier version where bars merely passing through a price counted as touches.

    python -m scripts.h1_pivot_levels --h1 'h1_20*.parquet' --days stockdays.parquet --out h1_pivot_levels.parquet
    python -m scripts.h1_pivot_levels --h1 'h1_20*.parquet' --days stockdays.parquet --chart-dir charts/ --sample 10
"""

import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd

LOOKBACK = 5
K = 2
MIN_TOUCH = 2
BAND = 1.0


def pivots(h: np.ndarray, l: np.ndarray, k: int = K) -> list[tuple[int, float, str]]:
    out = []
    n = len(h)
    for i in range(n):
        a, b = max(0, i - k), min(n, i + k + 1)
        if b - a < k + 1:
            continue
        if h[i] >= h[a:b].max():
            out.append((i, float(h[i]), "H"))
        if l[i] <= l[a:b].min():
            out.append((i, float(l[i]), "L"))
    return out


def touches(members: list) -> int:
    """Separate touches: bars within K of each other (e.g. a flat double top) count once."""
    idx = sorted({m[0] for m in members})
    return sum(1 for j, i in enumerate(idx) if j == 0 or i - idx[j - 1] > K)


def cluster(piv: list[tuple[int, float, str]], band: float, min_touch: int) -> list[tuple[float, list]]:
    alive = list(piv)
    out = []
    while alive:
        best = max(alive, key=lambda p: touches([q for q in alive if abs(q[1] / p[1] - 1) * 100 <= band]))
        members = [q for q in alive if abs(q[1] / best[1] - 1) * 100 <= band]
        if touches(members) < min_touch:
            break
        out.append((float(np.mean([q[1] for q in members])), members))
        alive = [q for q in alive if q not in members]
    return out


def windows(h1: pd.DataFrame, want: dict, lookback: int):
    for sid, g in h1.groupby("stock_id", sort=False):
        if sid not in want:
            continue
        g = g.reset_index(drop=True)
        dates = g["date"].to_numpy()
        days, first = np.unique(dates, return_index=True)
        last = np.r_[first[1:], len(dates)]
        for j in np.flatnonzero(np.isin(days, list(want[sid]))):
            if j >= lookback:
                yield sid, days[j], g, first[j - lookback], last[j - 1], last[j]


def chart(path: Path, sid: str, day: str, g: pd.DataFrame, a: int, b: int, end: int, lv, band: float) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = g.iloc[a:end].reset_index(drop=True)
    o, h, l, c = (d[k].to_numpy(float) for k in "ohlc")
    x = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.vlines(x, l, h, color="black", linewidth=0.6)
    ax.bar(x, np.abs(c - o).clip(min=1e-6), bottom=np.minimum(o, c), color=np.where(c >= o, "tab:red", "tab:green"), width=0.7)
    ax.axvspan(b - a - 0.5, len(d) - 0.5, color="gold", alpha=0.2)
    for k in np.flatnonzero(d["hour"].to_numpy() == 0):
        ax.axvline(k - 0.5, color="gray", linewidth=0.4)
    for p, members in lv:
        ax.axhline(p, color="tab:blue", linestyle="--", linewidth=0.9)
        ax.axhspan(p * (1 - band / 100), p * (1 + band / 100), color="tab:blue", alpha=0.06)
        ax.text(0, p, f" {p:.2f} ({touches(members)} touches)", va="bottom", fontsize=8, color="tab:blue")
        for i, v, kind in members:
            ax.scatter([i], [v], marker="v" if kind == "H" else "^", color="tab:blue", s=50, zorder=5)
    ticks = np.flatnonzero(d["hour"].to_numpy() == 0)
    ax.set_xticks(ticks, d["date"].to_numpy()[ticks])
    ax.set_title(f"{sid} {day}  H1 key levels (swing points, ±{band:g}%, ≥{MIN_TOUCH} touches; gold = trade day, markers = touches)")
    fig.tight_layout()
    fig.savefig(path, dpi=85)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--h1", required=True)
    ap.add_argument("--days", required=True)
    ap.add_argument("--band", type=float, default=BAND)
    ap.add_argument("--lookback", type=int, default=LOOKBACK)
    ap.add_argument("--out", default=None)
    ap.add_argument("--chart-dir", default=None)
    ap.add_argument("--sample", type=int, default=10)
    args = ap.parse_args()
    h1 = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.h1))], ignore_index=True)
    h1["stock_id"] = h1["stock_id"].astype(str)
    # Flat bars (no range, e.g. no trades that hour) are not turning points.
    h1 = h1[(h1["h"] > 0) & (h1["l"] > 0) & (h1["h"] > h1["l"])].sort_values(["stock_id", "date", "hour"])
    want = pd.read_parquet(args.days)
    want["stock_id"] = want["stock_id"].astype(str)
    if args.chart_dir:
        want = want[want["date"] >= "2023-01-01"].sample(args.sample * 3, random_state=7)
        h1 = h1[h1["stock_id"].isin(set(want["stock_id"]))]
    want = want.groupby("stock_id")["date"].apply(set).to_dict()
    rows, n_chart = [], 0
    for sid, day, g, a, b, end in windows(h1, want, args.lookback):
        h, l, c = (g[k].to_numpy(float)[a:b] for k in "hlc")
        lv = cluster(pivots(h, l), args.band, MIN_TOUCH)
        if args.chart_dir:
            if lv and n_chart < args.sample:
                n_chart += 1
                Path(args.chart_dir).mkdir(parents=True, exist_ok=True)
                chart(Path(args.chart_dir) / f"{n_chart:02d}_{sid}_{day}.png", sid, day, g, a, b, end, lv, args.band)
            continue
        for p, members in lv:
            nh = sum(m[2] == "H" for m in members)
            rows.append((day, sid, args.band, (p / c[-1] - 1) * 100, touches(members), nh, len(members) - nh))
    if args.out:
        res = pd.DataFrame(rows, columns=["date", "stock_id", "band", "level_pct", "n_days", "n_highs", "n_lows"])
        res.to_parquet(args.out, index=False)
        print(len(res), "levels", res[["date", "stock_id"]].drop_duplicates().shape[0], "stock-days")


if __name__ == "__main__":
    main()
