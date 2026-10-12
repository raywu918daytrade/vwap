"""D1 關鍵價: price levels touched repeatedly in the 20 trading days before each date.

Points = each day's high and low (adjusted D1) of the previous 20 days. For a band w, greedily take
the point whose ±w% window holds the most distinct days; if >= MIN_DAYS days, the mean of those points
is a key level, its points are removed, repeat. Levels are stored as % vs the previous close (same
basis as the feature table), with the number of days and highs/lows that formed them.

    python -m scripts.build_key_levels --d1 d1_raw.parquet --days stockdays.parquet --out key_levels.parquet
"""

import argparse

import numpy as np
import pandas as pd

LOOKBACK = 20
MIN_DAYS = 3
BANDS = (0.5, 1.0, 1.5, 2.0)


def levels(hi: np.ndarray, lo: np.ndarray, w: float) -> list[tuple[float, int, int, int]]:
    px = np.r_[hi, lo]
    day = np.r_[np.arange(len(hi)), np.arange(len(lo))]
    is_hi = np.r_[np.ones(len(hi), bool), np.zeros(len(lo), bool)]
    alive = np.ones(len(px), bool)
    out = []
    while alive.any():
        best, best_n, best_m = -1, 0, None
        for i in np.flatnonzero(alive):
            m = alive & (np.abs(px / px[i] - 1) * 100 <= w)
            n = len(np.unique(day[m]))
            if n > best_n:
                best, best_n, best_m = i, n, m
        if best_n < MIN_DAYS:
            break
        out.append((float(px[best_m].mean()), best_n, int(is_hi[best_m].sum()), int((~is_hi[best_m]).sum())))
        alive &= ~best_m
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--d1", required=True)
    ap.add_argument("--days", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    d1 = pd.read_parquet(args.d1)
    d1["stock_id"] = d1["stock_id"].astype(str)
    d1["date"] = pd.to_datetime(d1["date"]).dt.strftime("%Y-%m-%d")
    d1 = d1[d1["close"] > 0].sort_values(["stock_id", "date"])
    want = pd.read_parquet(args.days)
    want["stock_id"] = want["stock_id"].astype(str)
    want = want.groupby("stock_id")["date"].apply(set).to_dict()
    rows = []
    for sid, g in d1.groupby("stock_id", sort=False):
        if sid not in want:
            continue
        dates = g["date"].to_numpy()
        h, l, c = (g[k].to_numpy(float) for k in ("high", "low", "close"))
        for i in np.flatnonzero(np.isin(dates, list(want[sid]))):
            if i < LOOKBACK:
                continue
            prev = c[i - 1]
            for w in BANDS:
                for lv, n, nh, nl in levels(h[i - LOOKBACK:i], l[i - LOOKBACK:i], w):
                    rows.append((dates[i], sid, w, (lv / prev - 1) * 100, n, nh, nl))
    res = pd.DataFrame(rows, columns=["date", "stock_id", "band", "level_pct", "n_days", "n_highs", "n_lows"])
    res["level_pct"] = res["level_pct"].astype("float32")
    res.to_parquet(args.out, index=False)
    print(len(res), "levels;", res.groupby("band").size().to_dict())
    print(res.groupby("band")[["date", "stock_id"]].apply(lambda x: len(x.drop_duplicates())).to_dict(), "stock-days with >=1 level")


if __name__ == "__main__":
    main()
