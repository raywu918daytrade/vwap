"""Ray 2026-10-16: 「先看做空：VWAP 穿越時，要回頭看是否在 MACD 柱狀頂背離，再做空」 — every M1 close-below-VWAP cross
(previous close at/above VWAP) between 09:10 and 12:00; at the cross look back at the day's last two swing highs
(local max of high over ±3 bars). 頂背離 = later swing high >= earlier one AND the MACD(12,26,9) histogram peak
around it is lower (earlier peak > 0). Labels: div / nodiv (two swing highs, no divergence) / one (fewer than two
swing highs). Entry next open. Outcomes: TP 3.5 / SL 3, hold, stop at the last swing high with TP 1x / 3.5 / hold.

    python -m scripts.m1_vwapdiv_scan --days pr50_days.parquet --year 2025 --out vwapdiv_2025.parquet [--dump 1500]
"""

import argparse

import numpy as np
import pandas as pd

from data.adjustment_query import load_pattern_day
from scripts.build_feature_table import outcome
from scripts.build_feature_table_long import atr_table, load_m1_month, months
from scripts.m1_vwap_band import band

COST = 0.435
W = 3


def macd_hist(c, fast=12, slow=26, sig=9):
    s = pd.Series(c)
    macd = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    return (macd - macd.ewm(span=sig, adjust=False).mean()).to_numpy()


def swing_highs(h, t):
    """Indices p <= t-W with h[p] the max of h[p-W:p+W+1]."""
    out = []
    for p in range(W, t - W + 1):
        if h[p] >= h[p - W:p + W + 1].max():
            if out and p - out[-1] <= W:
                if h[p] >= h[out[-1]]:
                    out[-1] = p
                continue
            out.append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", required=True)
    ap.add_argument("--year", required=True)
    ap.add_argument("--out", required=True)
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
            found = False
            for t in range(6, nb - 1):
                if hh[t] > "12:00":
                    break
                if hh[t] < "09:10" or not (c[t - 1] >= vwap[t - 1] and c[t] < vwap[t]):
                    continue
                sw = swing_highs(h, t)
                if len(sw) >= 2:
                    p1, p2 = sw[-2], sw[-1]
                    hp1 = hist[max(0, p1 - W):p1 + W + 1].max()
                    hp2 = hist[max(0, p2 - W):p2 + W + 1].max()
                    if h[p2] >= h[p1] and hp1 > 0 and hp2 < hp1:
                        lab = "div"
                    elif h[p2] < h[p1]:
                        lab = "lowerhigh"
                    else:
                        lab = "nodiv"
                elif len(sw) == 1:
                    p1, p2, hp1, hp2, lab = -1, sw[0], np.nan, hist[max(0, sw[0] - W):sw[0] + W + 1].max(), "one"
                else:
                    continue
                e = t + 1
                near = h[p2]
                sl_near = (near / o[e] - 1) * 100
                rec = {"date": d, "stock_id": sid, "side": "short", "time": hh[t], "div": lab,
                       "p1_time": hh[p1] if p1 >= 0 else "", "p1_hi": h[p1] if p1 >= 0 else np.nan, "p1_hist": hp1,
                       "p2_time": hh[p2], "p2_hi": near, "p2_hist": hp2, "bars_since_p2": t - p2,
                       "hist_t": hist[t], "entry_pct": (o[e] / prev_close - 1) * 100, "entry_price": o[e],
                       "hi_pct": (hi_so_far[t] / prev_close - 1) * 100, "vs_open": (o[e] / o[0] - 1) * 100,
                       "sl_near": sl_near, "prev_close": prev_close}
                net, ex = outcome(o, h, l, c, e, -1, 3.5, 3.0, COST)
                rec.update({"net": net, "exit": ex, "hold": outcome(o, h, l, c, e, -1, 1e6, 3.0, COST)[0]})
                sn = max(sl_near, 0.3)
                rec["rr1_nearsl"], rec["exit_rr1"] = outcome(o, h, l, c, e, -1, sn, sn, COST)
                rec["tp35_nearsl"] = outcome(o, h, l, c, e, -1, 3.5, sn, COST)[0]
                rec["hold_nearsl"] = outcome(o, h, l, c, e, -1, 1e6, sn, COST)[0]
                rows.append(rec)
                found = found or lab == "div"
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
