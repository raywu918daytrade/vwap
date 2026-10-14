"""Ray 2026-10-14: 1 根 M1 穿越 N 根 K 線 — one M1 bar whose BODY (open..close) spans the whole high-low range of
the previous N bars (it opens on one side of that box and closes beyond the other side), body >= --min-body %.
Scans the given stock-days (ATR>=5, PR>=90 pool), records every such bar with N >= --min-n, the standard outcomes
(next-M1-open entry, TP3.5/SL3 and hold-to-close, cost 0.435 baked), and dumps the M1 path (with volume and VWAP)
of the --dump most recent stock-days that have an event, for charts.

    python -m scripts.m1_engulf_scan --days pr90_days.parquet --year 2026 --out engulf_2026.parquet --dump 10
"""

import argparse

import numpy as np
import pandas as pd

from scripts.build_feature_table import outcome
from scripts.build_feature_table_long import atr_table, load_m1_month, months
from scripts.m1_vwap_band import band
from data.adjustment_query import load_pattern_day

COST = 0.435
MAX_N = 60


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", required=True)
    ap.add_argument("--year", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-n", type=int, default=5)
    ap.add_argument("--min-body", type=float, default=0.5, help="bar body in %% of price, to skip 1-tick boxes")
    ap.add_argument("--dump", type=int, default=0, help="dump M1 paths of the N most recent stock-days with an event")
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
    rows, paths = [], {}
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
            prev_close = o[0] / gaps[(sid, d)]
            hi_so_far, lo_so_far = np.maximum.accumulate(h), np.minimum.accumulate(l)
            found = False
            for i in range(args.min_n + 1, len(bars) - 1):
                if hh[i] < "09:05" or hh[i] > "13:00":
                    continue
                body_lo, body_hi = min(o[i], c[i]), max(o[i], c[i])
                if (body_hi / body_lo - 1) * 100 < args.min_body:
                    continue
                # largest N such that this bar's body spans the previous N bars' high-low range
                n = 0
                while n < min(MAX_N, i - 1) and body_hi >= h[i - n - 1] and body_lo <= l[i - n - 1]:
                    n += 1
                if n < args.min_n:
                    continue
                found = True
                side = "up" if c[i] > o[i] else "down"
                sign = 1 if side == "up" else -1
                net, ex = outcome(o, h, l, c, i + 1, sign, 3.5, 3.0, COST)
                hold, _ = outcome(o, h, l, c, i + 1, sign, 1e6, 3.0, COST)
                net_r, _ = outcome(o, h, l, c, i + 1, -sign, 3.5, 3.0, COST)
                hold_r, _ = outcome(o, h, l, c, i + 1, -sign, 1e6, 3.0, COST)
                pv = v[i - n:i].mean()
                rows.append({"date": d, "stock_id": sid, "time": hh[i], "side": side, "n": n,
                             "bar_rng": (h[i] / l[i] - 1) * 100, "body": (c[i] / o[i] - 1) * 100,
                             "close_pos": (c[i] - l[i]) / max(h[i] - l[i], 1e-9), "vol_ratio": v[i] / max(pv, 1e-9),
                             "entry_pct": (o[i + 1] / prev_close - 1) * 100, "entry_price": o[i + 1],
                             "hi_pct": (hi_so_far[i] / prev_close - 1) * 100, "lo_pct": (lo_so_far[i] / prev_close - 1) * 100,
                             "vs_vwap": (c[i] / vwap[i] - 1) * 100,
                             "net": net, "exit": ex, "hold": hold, "net_rev": net_r, "hold_rev": hold_r})
            if found and args.dump:
                paths[(d, sid)] = pd.DataFrame({"stock_id": sid, "date": d, "time": hh, "o": o, "h": h, "l": l, "c": c, "v": v,
                                                "vwap": vwap, "up": vwap + 2 * sd, "dn": vwap - 2 * sd, "prev_close": prev_close})
        print(stem, len(rows) - n0, flush=True)
    pd.DataFrame(rows).to_parquet(args.out, index=False)
    if paths:
        keep = sorted(paths, reverse=True)[:args.dump]
        pd.concat([paths[k] for k in keep]).to_parquet(args.out.replace(".parquet", "_paths.parquet"), index=False)
    print("rows", len(rows), "dumped", min(len(paths), args.dump))


if __name__ == "__main__":
    main()
