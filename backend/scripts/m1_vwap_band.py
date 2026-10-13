"""Intraday M1 VWAP ± 2σ signals for the standard stock-days.

Per minute: VWAP = cumulative Σ(close·vol)/Σvol, σ = volume-weighted std of close around VWAP, both
from 09:00. Short signal: an M1 whose high reaches VWAP+2σ and that closes below VWAP+2σ (touched
the upper band and came back), entry at the next M1 open; long = low reaches VWAP−2σ, closes above.
Signals from 09:10 to 12:00, with the standard caps (entry ≤ +5% / ≥ −5%, no ±9.5% touched so far).
Outcomes: TP 3.5 / SL 3 and no-TP / SL 3, close 13:24, cost 0.435% (slippage added in the study).
Every signal of the day is written; the study picks the first one. --sample N also dumps the full
M1 band path of N stock-days for charts.

    python -m scripts.m1_vwap_band --days stockdays.parquet --year 2024 --out m1vb_2024.parquet --sample 3
"""

import argparse

import numpy as np
import pandas as pd

from data.adjustment_query import load_pattern_day
from scripts.build_feature_table import outcome
from scripts.build_feature_table_long import atr_table, load_m1_month, months

COST = 0.435
K = 2.0


def band(c: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cv = np.maximum(np.cumsum(v), 1e-9)
    vwap = np.cumsum(c * v) / cv
    var = np.cumsum(c * c * v) / cv - vwap ** 2
    return vwap, np.sqrt(np.clip(var, 0, None))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", required=True)
    ap.add_argument("--year", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()
    want = pd.read_parquet(args.days)
    want["stock_id"] = want["stock_id"].astype(str)
    want = want[want["date"].str[:4] == args.year]
    keys = set(zip(want["stock_id"], want["date"]))
    ids = {s for s, _ in keys}
    day = load_pattern_day(start_date=f"{int(args.year) - 1}-11-01", end_date=f"{args.year}-12-31")
    day["stock_id"] = day["stock_id"].astype(str)
    day = day[day["stock_id"].isin(ids) & (day["open"] > 0)]
    gaps = {k: g for k, (_, g) in atr_table(day).items()}
    rng = np.random.default_rng(5)
    sample_keys = set(map(tuple, rng.choice(sorted(keys), size=min(args.sample, len(keys)), replace=False))) if args.sample else set()
    rows, paths = [], []
    for stem in months(f"{args.year}-01", f"{args.year}-12"):
        m1 = load_m1_month(stem, ids)
        if m1.empty:
            continue
        n0 = len(rows)
        for (sid, d), bars in m1.groupby(["stock_id", "day"], sort=False):
            if (sid, d) not in keys or (sid, d) not in gaps:
                continue
            bars = bars.reset_index(drop=True)
            o, h, l, c = (bars[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
            v = bars["volume"].astype(float).to_numpy()
            hh = bars["hhmm"].to_numpy()
            vwap, sd = band(c, v)
            up, dn = vwap + K * sd, vwap - K * sd
            prev_close = o[0] / gaps[(sid, d)]
            hi_so_far, lo_so_far = np.maximum.accumulate(h), np.minimum.accumulate(l)
            if (sid, d) in sample_keys:
                paths.append(pd.DataFrame({"stock_id": sid, "date": d, "time": hh, "o": o, "h": h, "l": l, "c": c,
                                           "vwap": vwap, "up": up, "dn": dn}))
            for i in range(10, len(bars) - 1):
                if hh[i] > "12:00":
                    break
                if sd[i] <= 0:
                    continue
                short = h[i] >= up[i] and c[i] < up[i]
                long_ = l[i] <= dn[i] and c[i] > dn[i]
                for side, ok, sign in (("short", short, -1), ("long", long_, 1)):
                    if not ok:
                        continue
                    net, ex = outcome(o, h, l, c, i + 1, sign, 3.5, 3.0, COST)
                    hold, _ = outcome(o, h, l, c, i + 1, sign, 1e6, 3.0, COST)
                    rows.append({"date": d, "stock_id": sid, "side": side, "time": hh[i], "entry_time": hh[i + 1],
                                 "entry_pct": (o[i + 1] / prev_close - 1) * 100,
                                 "hi_pct": (hi_so_far[i] / prev_close - 1) * 100, "lo_pct": (lo_so_far[i] / prev_close - 1) * 100,
                                 "band_pct": (K * sd[i] / vwap[i]) * 100, "vs_vwap": (c[i] / vwap[i] - 1) * 100,
                                 "net": net, "exit": ex, "hold": hold})
        print(stem, len(rows) - n0, flush=True)
    pd.DataFrame(rows).to_parquet(args.out, index=False)
    if paths:
        pd.concat(paths).to_parquet(args.out.replace(".parquet", "_paths.parquet"), index=False)
    print("rows", len(rows))


if __name__ == "__main__":
    main()
