"""H1 關鍵價: levels touched by >= 3 H1 bars (highs/lows) in the previous N trading days.

Same greedy clustering as scripts.build_key_levels (n_days there = number of distinct H1 bars here).
Levels are % vs the previous day's last H1 close (raw M1 prices; the feature table's prev close only
differs on ex-dividend days).

    python -m scripts.build_h1_levels --h1 'h1_*.parquet' --days stockdays.parquet --lookback 5 --out h1_levels.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.build_key_levels import BANDS, levels


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--h1", required=True)
    ap.add_argument("--days", required=True)
    ap.add_argument("--lookback", type=int, default=5)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    h1 = pd.concat([pd.read_parquet(p, columns=["stock_id", "date", "hour", "h", "l", "c"]) for p in sorted(glob.glob(args.h1))],
                   ignore_index=True)
    h1["stock_id"] = h1["stock_id"].astype(str)
    h1 = h1[(h1["h"] > 0) & (h1["l"] > 0)].sort_values(["stock_id", "date", "hour"])
    want = pd.read_parquet(args.days)
    want["stock_id"] = want["stock_id"].astype(str)
    want = want.groupby("stock_id")["date"].apply(set).to_dict()
    rows = []
    for sid, g in h1.groupby("stock_id", sort=False):
        if sid not in want:
            continue
        dates = g["date"].to_numpy()
        days, first = np.unique(dates, return_index=True)
        last = np.r_[first[1:], len(dates)]
        h, l, c = (g[k].to_numpy(float) for k in ("h", "l", "c"))
        for j in np.flatnonzero(np.isin(days, list(want[sid]))):
            if j < args.lookback:
                continue
            a, b = first[j - args.lookback], last[j - 1]
            prev = c[b - 1]
            for w in BANDS:
                for lv, n, nh, nl in levels(h[a:b], l[a:b], w):
                    rows.append((days[j], sid, w, (lv / prev - 1) * 100, n, nh, nl))
    res = pd.DataFrame(rows, columns=["date", "stock_id", "band", "level_pct", "n_days", "n_highs", "n_lows"])
    res["level_pct"] = res["level_pct"].astype("float32")
    res.to_parquet(args.out, index=False)
    print(len(res), "levels;", res.groupby("band").size().to_dict())


if __name__ == "__main__":
    main()
