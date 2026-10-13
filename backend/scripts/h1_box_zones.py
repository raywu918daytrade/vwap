"""H1 盤整區 (consolidation box) as resistance / support, plus charts to check them.

Box: >= MIN_BARS consecutive H1 bars over >= 2 trading days whose bodies (open/close) all fit in a
range of <= BOX % (of the box's mid price); runs are grown greedily and do not overlap.
Short setup: after the box, some H1 close fell >= BREAK % below the box bottom and yesterday still
closed below the box -> the box is overhead resistance today.
Long setup: mirror (close >= BREAK % above the box top, yesterday closed above it) -> support.
The box edge must be within NEAR % of yesterday's close. Only the most recent qualifying box per side
is kept. Prices are raw M1, levels stored as % vs the
previous day's last H1 close.

    python -m scripts.h1_box_zones --h1 'h1_20*.parquet' --days stockdays.parquet --out h1_boxes.parquet
    python -m scripts.h1_box_zones --h1 'h1_20*.parquet' --days stockdays.parquet --chart-dir charts/ --sample 5
"""

import argparse
from pathlib import Path

import glob
import numpy as np
import pandas as pd

LOOKBACK = 10
MIN_BARS = 8
BOX = 3.0
BREAK = 3.0
NEAR = 6.0  # the box edge must be within this % of yesterday's close, or today can't reach it


def boxes(o: np.ndarray, c: np.ndarray, day: np.ndarray) -> list[tuple[int, int, float, float]]:
    top, bot = np.maximum(o, c), np.minimum(o, c)
    out, i, n = [], 0, len(o)
    while i < n:
        hi, lo, j = top[i], bot[i], i
        while j + 1 < n:
            h2, l2 = max(hi, top[j + 1]), min(lo, bot[j + 1])
            if (h2 / l2 - 1) * 100 > BOX:
                break
            hi, lo, j = h2, l2, j + 1
        if j - i + 1 >= MIN_BARS and day[j] != day[i]:
            out.append((i, j, float(lo), float(hi)))
            i = j + 1
        else:
            i += 1
    return out


def setups(o, c, day, brk: float = BREAK):
    """Most recent short (resistance) and long (support) box, or None."""
    short = long = None
    last = c[-1]
    for i, j, lo, hi in boxes(o, c, day):
        after = c[j + 1:]
        if len(after) == 0:
            continue
        if after.min() <= lo * (1 - brk / 100) and last < lo and lo <= last * (1 + NEAR / 100):
            short = (i, j, lo, hi)
        if after.max() >= hi * (1 + brk / 100) and last > hi and hi >= last * (1 - NEAR / 100):
            long = (i, j, lo, hi)
    return short, long


def chart(path: Path, title: str, d: pd.DataFrame, n_hist: int, box) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    o, h, l, c = (d[k].to_numpy(float) for k in "ohlc")
    x = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(12, 4.8))
    ax.vlines(x, l, h, color="black", linewidth=0.6)
    ax.bar(x, np.abs(c - o).clip(min=1e-6), bottom=np.minimum(o, c), color=np.where(c >= o, "tab:red", "tab:green"), width=0.7)
    ax.axvspan(n_hist - 0.5, len(d) - 0.5, color="gold", alpha=0.2)
    for k in np.flatnonzero(d["hour"].to_numpy() == 0):
        ax.axvline(k - 0.5, color="gray", linewidth=0.4)
    i, j, lo, hi = box
    ax.add_patch(plt.Rectangle((i - 0.5, lo), j - i + 1, hi - lo, color="tab:blue", alpha=0.25))
    ax.axhspan(lo, hi, xmin=0, xmax=1, color="tab:blue", alpha=0.07)
    ax.axhline(lo, color="tab:blue", linestyle="--", linewidth=0.8)
    ax.axhline(hi, color="tab:blue", linestyle="--", linewidth=0.8)
    ticks = np.flatnonzero(d["hour"].to_numpy() == 0)
    ax.set_xticks(ticks, [s[5:] for s in d["date"].to_numpy()[ticks]])
    ax.set_ylim(np.nanmin(l) * 0.99, np.nanmax(h) * 1.01)
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=85)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--h1", required=True)
    ap.add_argument("--days", required=True)
    ap.add_argument("--lookback", type=int, default=LOOKBACK)
    ap.add_argument("--out", default=None)
    ap.add_argument("--chart-dir", default=None)
    ap.add_argument("--sample", type=int, default=5)
    args = ap.parse_args()
    h1 = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.h1))], ignore_index=True)
    h1["stock_id"] = h1["stock_id"].astype(str)
    h1 = h1[(h1["h"] > 0) & (h1["l"] > 0)].sort_values(["stock_id", "date", "hour"])
    want = pd.read_parquet(args.days)
    want["stock_id"] = want["stock_id"].astype(str)
    if args.chart_dir:
        want = want[want["date"] >= "2023-01-01"].sample(400, random_state=11)
        h1 = h1[h1["stock_id"].isin(set(want["stock_id"]))]
    want = want.groupby("stock_id")["date"].apply(set).to_dict()
    rows, n_chart = [], {"short": 0, "long": 0}
    groups = dict(list(h1.groupby("stock_id", sort=False)))
    order = list(groups)
    if args.chart_dir:
        np.random.default_rng(3).shuffle(order)
    for sid in order:
        if sid not in want:
            continue
        g = groups[sid].reset_index(drop=True)
        dates = g["date"].to_numpy()
        days, first = np.unique(dates, return_index=True)
        last = np.r_[first[1:], len(dates)]
        for jd in np.flatnonzero(np.isin(days, list(want[sid]))):
            if jd < args.lookback:
                continue
            a, b = first[jd - args.lookback], last[jd - 1]
            o, c = g["o"].to_numpy(float)[a:b], g["c"].to_numpy(float)[a:b]
            day = np.unique(dates[a:b], return_inverse=True)[1]
            for side, box in zip(("short", "long"), setups(o, c, day)):
                if box is None:
                    continue
                i, j, lo, hi = box
                prev = c[-1]
                rows.append((days[jd], sid, side, (lo / prev - 1) * 100, (hi / prev - 1) * 100, j - i + 1,
                             int(day[j] - day[i] + 1), int(len(o) - 1 - j)))
                if args.chart_dir and n_chart[side] < args.sample:
                    n_chart[side] += 1
                    Path(args.chart_dir).mkdir(parents=True, exist_ok=True)
                    word = "resistance (short)" if side == "short" else "support (long)"
                    chart(Path(args.chart_dir) / f"{side}_{n_chart[side]:02d}_{sid}_{days[jd]}.png",
                          f"{sid} {days[jd]}  H1 box {word}: {j - i + 1} bars, {lo:.2f}-{hi:.2f} (gold = trade day)",
                          g.iloc[a:last[jd]].reset_index(drop=True), b - a, box)
    if args.out:
        res = pd.DataFrame(rows, columns=["date", "stock_id", "side", "lo_pct", "hi_pct", "n_bars", "n_days", "bars_since"])
        res.to_parquet(args.out, index=False)
        print(len(res), res.groupby("side").size().to_dict())


if __name__ == "__main__":
    main()
