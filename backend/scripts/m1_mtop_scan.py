"""Ray 2026-10-16: 「M 頭 + 跌破 VWAP」 — intraday double top on M1 then the first close below VWAP -> short.
M top: two swing highs (local max of high over ±3 bars) at least --gap bars apart, the second within --tol % of the
first, with the trough between them at least --depth % below the lower high; the close at the second peak is above
VWAP. Signal = first M1 close below VWAP after the second peak is confirmed (3 bars later), 09:10–12:00, before price
takes out the M high. Entry next open. Outcomes: TP 3.5 / SL 3, hold; stop at the M high with TP 1x / 3.5 / hold.
Also records the red-green-red MACD histogram divergence label (scripts.macd_rgr) at the signal bar.

    python -m scripts.m1_mtop_scan --days pr50_days.parquet --year 2025 --out mtop_2025.parquet [--dump 1500]
"""

import argparse

import numpy as np
import pandas as pd

from data.adjustment_query import load_pattern_day
from scripts.build_feature_table import outcome
from scripts.build_feature_table_long import atr_table, load_m1_month, months
from scripts.m1_vwap_band import band
from scripts.macd_rgr import macd_hist, rgr_divergence

COST = 0.435
W = 3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", required=True)
    ap.add_argument("--year", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tol", type=float, default=1.0, help="second high within this % of the first")
    ap.add_argument("--depth", type=float, default=0.8, help="trough at least this % below the lower high")
    ap.add_argument("--gap", type=int, default=6, help="min bars between the two highs")
    ap.add_argument("--dump", type=int, default=0)
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
            hist = macd_hist(c)
            prev_close = o[0] / gaps[(sid, d)]
            hi_so_far = np.maximum.accumulate(h)
            nb = len(bars)
            # swing highs confirmed W bars later
            sw = [p for p in range(W, nb - W) if h[p] >= h[p - W:p + W + 1].max()]
            used_p2 = set()
            found = False
            for a in range(len(sw) - 1):
                p1 = sw[a]
                for bidx in range(a + 1, len(sw)):
                    p2 = sw[bidx]
                    if p2 - p1 < args.gap or p2 in used_p2:
                        continue
                    if h[p2] > h[p1] * (1 + args.tol / 100):
                        break  # a higher high ends this M attempt from p1
                    diff = (h[p2] / h[p1] - 1) * 100
                    if diff < -args.tol:
                        continue
                    trough = l[p1:p2 + 1].min()
                    depth = (1 - trough / min(h[p1], h[p2])) * 100
                    if depth < args.depth or c[p2] < vwap[p2]:
                        continue
                    m_hi = max(h[p1], h[p2])
                    t = None
                    for u in range(p2 + W, nb - 1):
                        if hh[u] > "12:00" or h[u] > m_hi:
                            break
                        if c[u] < vwap[u]:
                            t = u
                            break
                    if t is None or hh[t] < "09:10":
                        continue
                    used_p2.add(p2)
                    found = True
                    e = t + 1
                    lab, i1, i2, q1, q2 = rgr_divergence(h, hist, t)
                    sl_m = (m_hi / o[e] - 1) * 100
                    rec = {"date": d, "stock_id": sid, "side": "short", "time": hh[t], "p1_time": hh[p1], "p1_hi": h[p1],
                           "p2_time": hh[p2], "p2_hi": h[p2], "diff_pct": diff, "depth_pct": depth, "gap_bars": p2 - p1,
                           "bars_after_p2": t - p2, "m_hi": m_hi, "rgr": lab,
                           "q1_time": hh[q1] if q1 >= 0 else "", "q2_time": hh[q2] if q2 >= 0 else "",
                           "r1_time": hh[i1] if i1 >= 0 else "", "r2_time": hh[i2] if i2 >= 0 else "",
                           "hist_t": hist[t], "entry_pct": (o[e] / prev_close - 1) * 100, "entry_price": o[e],
                           "hi_pct": (hi_so_far[t] / prev_close - 1) * 100, "vs_open": (o[e] / o[0] - 1) * 100,
                           "m_vs_open": (m_hi / o[0] - 1) * 100, "sl_near": sl_m, "prev_close": prev_close}
                    net, ex = outcome(o, h, l, c, e, -1, 3.5, 3.0, COST)
                    rec.update({"net": net, "exit": ex, "hold": outcome(o, h, l, c, e, -1, 1e6, 3.0, COST)[0]})
                    sn = max(sl_m, 0.3)
                    rec["rr1_nearsl"], rec["exit_rr1"] = outcome(o, h, l, c, e, -1, sn, sn, COST)
                    rec["tp35_nearsl"] = outcome(o, h, l, c, e, -1, 3.5, sn, COST)[0]
                    rec["hold_nearsl"] = outcome(o, h, l, c, e, -1, 1e6, sn, COST)[0]
                    rows.append(rec)
                    break  # one M per first peak
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
