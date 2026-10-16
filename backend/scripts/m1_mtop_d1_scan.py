"""Ray 2026-10-16: M 頭 on M1 using the project's own D1 detector (pattern.m_top.MTopDetector) bar by bar, with the
tolerances tightened for M1 (two tops within 1%, neckline depth 1.5-5%, four-segment symmetry within 2.5x, score >= 60).
Signal = first bar whose detect() reports breakout_down (close <= neckline * 0.997), 09:10-12:00; one per stock-day
(the first). Entry next open. Outcomes recorded for later (TP 3.5 / SL 3, hold, stop at the M high); also the first
close below VWAP after the signal (entry there) for the "跌破 VWAP 再進" variant.

    python -m scripts.m1_mtop_d1_scan --days pr50_days.parquet --year 2025 --out mtopd1_2025.parquet [--dump 1500]
"""

import argparse
from multiprocessing import Pool

import numpy as np
import pandas as pd

from data.adjustment_query import load_pattern_day
from pattern.m_top.detector import MTopDetector
from scripts.build_feature_table import outcome
from scripts.build_feature_table_long import atr_table, load_m1_month, months
from scripts.m1_vwap_band import band
from scripts.macd_rgr import macd_hist, rgr_divergence

COST = 0.435
DET = MTopDetector(window=3, max_top_diff_pct=0.01, min_depth_pct=0.015, max_depth_pct=0.05,
                   min_top_dist=5, max_top_dist=60, min_candles=20, max_candles=120)
DET.max_sym_ratio = 2.5
MIN_SCORE = 60.0
K_BARS = 1.0  # two-top tolerance = K_BARS x mean M1 bar range over the detection window (Ray: 誤差用 K 根數, 1-3 根)


def one(args):
    sid, d, bars, prev_close = args
    o, h, l, c = (bars[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
    v = bars["volume"].astype(float).to_numpy()
    hh = bars["hhmm"].to_numpy()
    vwap, sd = band(c, v)
    df = pd.DataFrame({"date": hh, "open": o, "high": h, "low": l, "close": c, "volume": v})
    nb = len(bars)
    for t in range(20, nb - 1):
        if hh[t] < "09:10":
            continue
        if hh[t] > "12:00":
            break
        bar_pct = 0.0
        if K_BARS > 0:
            w = df.iloc[max(0, t - 119):t + 1]
            bar_pct = float((w["high"] - w["low"]).mean()) / float(w["high"].max()) * 100  # one bar's range in %
            DET.max_top_diff_pct = max(1e-4, K_BARS * bar_pct / 100)
        r = DET.detect(df.iloc[:t + 1], sid, "1m")
        if r is None or r.score < MIN_SCORE or r.details["breakout_status"] != "breakout_down":
            continue
        l0, h1, lo, h2 = r.pivots
        e = t + 1
        m_hi = max(h1.price, h2.price)
        hist = macd_hist(c)
        lab, i1, i2, q1, q2 = rgr_divergence(h, hist, t)
        sl_m = (m_hi / o[e] - 1) * 100
        rec = {"date": d, "stock_id": sid, "side": "short", "time": hh[t], "l0_time": l0.date, "p1_time": h1.date,
               "tr_time": lo.date, "p2_time": h2.date, "d_time": r.lines[3].end_date, "d_price": r.lines[3].end_price,
               "p1_hi": h1.price, "p2_hi": h2.price, "neck": lo.price, "m_hi": m_hi, "score": r.score,
               "depth_pct": r.details["neckline_depth_pct"], "diff_pct": r.details["top_diff_pct"], "bar_pct": bar_pct, "k_bars": K_BARS,
               "rise_pct": (h1.price / l0.price - 1) * 100, "below_vwap": bool(c[t] < vwap[t]), "rgr": lab,
               "q1_time": hh[q1] if q1 >= 0 else "", "q2_time": hh[q2] if q2 >= 0 else "",
               "entry_price": o[e], "entry_pct": (o[e] / prev_close - 1) * 100, "vs_open": (o[e] / o[0] - 1) * 100,
               "hi_pct": (h[:t + 1].max() / prev_close - 1) * 100, "m_vs_open": (m_hi / o[0] - 1) * 100,
               "sl_near": sl_m, "prev_close": prev_close}
        net, ex = outcome(o, h, l, c, e, -1, 3.5, 3.0, COST)
        rec.update({"net": net, "exit": ex, "hold": outcome(o, h, l, c, e, -1, 1e6, 3.0, COST)[0]})
        sn = max(sl_m, 0.3)
        rec["rr1_nearsl"], rec["exit_rr1"] = outcome(o, h, l, c, e, -1, sn, sn, COST)
        rec["tp35_nearsl"] = outcome(o, h, l, c, e, -1, 3.5, sn, COST)[0]
        rec["hold_nearsl"] = outcome(o, h, l, c, e, -1, 1e6, sn, COST)[0]
        # variant: wait for the first close below VWAP after the signal (before the M high is taken out)
        tv = None
        for u in range(t, nb - 1):
            if hh[u] > "12:00" or h[u] > m_hi:
                break
            if c[u] < vwap[u]:
                tv = u
                break
        rec["vwap_time"] = hh[tv] if tv is not None else ""
        if tv is not None:
            ev = tv + 1
            slv = max((m_hi / o[ev] - 1) * 100, 0.3)
            rec["v_entry_pct"] = (o[ev] / prev_close - 1) * 100
            rec["v_hi_pct"] = (h[:tv + 1].max() / prev_close - 1) * 100
            rec["v_net"] = outcome(o, h, l, c, ev, -1, 3.5, 3.0, COST)[0]
            rec["v_hold"] = outcome(o, h, l, c, ev, -1, 1e6, 3.0, COST)[0]
            rec["v_rr1_nearsl"] = outcome(o, h, l, c, ev, -1, slv, slv, COST)[0]
            rec["v_hold_nearsl"] = outcome(o, h, l, c, ev, -1, 1e6, slv, COST)[0]
        path = pd.DataFrame({"stock_id": sid, "date": d, "time": hh, "o": o, "h": h, "l": l, "c": c, "v": v,
                             "vwap": vwap, "up": vwap + 2 * sd, "dn": vwap - 2 * sd, "prev_close": prev_close})
        return rec, path
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", required=True)
    ap.add_argument("--year", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dump", type=int, default=0)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--k", type=float, default=1.0, help="two-top tolerance in M1 bar ranges")
    ap.add_argument("--part", default="", help="i/n: process only the i-th of n slices of the year's stock-days")
    args = ap.parse_args()
    global K_BARS
    K_BARS = args.k
    want = pd.read_parquet(args.days)
    want["stock_id"] = want["stock_id"].astype(str)
    want = want[want["date"].str[:4] == args.year]
    if args.part:
        i, n = (int(x) for x in args.part.split("/"))
        want = want.sort_values(["date", "stock_id"]).iloc[i::n]
    keys = set(zip(want["stock_id"], want["date"]))
    extra = {}
    if "day_atr" in want.columns:
        extra = {(s, d): (a, p) for s, d, a, p in zip(want["stock_id"], want["date"], want["day_atr"], want["vol5_pr"])}
    ids = {s for s, _ in keys}
    day = load_pattern_day(start_date=f"{int(args.year) - 1}-11-01", end_date=f"{args.year}-12-31")
    day["stock_id"] = day["stock_id"].astype(str)
    day = day[day["stock_id"].isin(ids) & (day["open"] > 0)]
    gaps = {k: g for k, (_, g) in atr_table(day).items()}
    rows, paths, n_days = [], {}, 0
    with Pool(args.procs) as pool:
        for stem in months(f"{args.year}-01", f"{args.year}-12"):
            m1 = load_m1_month(stem, ids)
            if m1.empty:
                continue
            jobs = []
            for (sid, d), bars in m1.groupby(["stock_id", "day"], sort=False):
                if (sid, d) not in keys or (sid, d) not in gaps:
                    continue
                bars = bars.reset_index(drop=True)
                jobs.append((sid, d, bars, float(bars["open"].iloc[0]) / gaps[(sid, d)]))
            n_days += len(jobs)
            n0 = len(rows)
            for res in pool.imap_unordered(one, jobs, chunksize=8):
                if res is None:
                    continue
                rec, path = res
                if extra:
                    rec["day_atr"], rec["vol5_pr"] = extra.get((rec["stock_id"], rec["date"]), (np.nan, np.nan))
                rows.append(rec)
                paths[(rec["date"], rec["stock_id"])] = path
            print(stem, "days", len(jobs), "mtops", len(rows) - n0, flush=True)
    pd.DataFrame(rows).to_parquet(args.out, index=False)
    if paths and args.dump:
        keep = sorted(paths, reverse=True)[:args.dump]
        pd.concat([paths[k] for k in keep]).to_parquet(args.out.replace(".parquet", "_paths.parquet"), index=False)
    print("stock-days", n_days, "rows", len(rows), "dumped", min(len(paths), args.dump))


if __name__ == "__main__":
    main()
