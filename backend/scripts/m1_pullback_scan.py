"""Ray 2026-10-14: 「要減量 價格回落一半」 — after a burst of consecutive high-volume M1 bars (same run definition as
scripts.m1_climax_scan), wait for price to pull back on shrinking volume to the midpoint of the run's high-low range:
the pullback bars (from the bar after the run to the touch bar) average <= --dry x the run's average volume, the touch
happens within --within bars and before 12:00. Entry A: next open after the touch ("touch"). Entry B: the first bar
after the touch (<= --confirm bars) that closes back beyond the previous bar's high (up run) / low (down run)
("confirm"). Sides: with the run (long after an up run) and against it; outcomes TP 3.5 / SL 3, hold to close, and
SL at the run's far extreme (run low for an up-run long). Runs on GitHub Actions where HF M1 data is available.

    python -m scripts.m1_pullback_scan --days pr50_days.parquet --year 2024 --out pullback_2024.parquet [--dump 12]
"""

import argparse

import numpy as np
import pandas as pd

from data.adjustment_query import load_pattern_day
from scripts.build_feature_table import outcome
from scripts.build_feature_table_long import atr_table, load_m1_month, months
from scripts.m1_vwap_band import band

COST = 0.435


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", required=True)
    ap.add_argument("--year", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--vol", type=float, default=2.0)
    ap.add_argument("--move", type=float, default=1.5)
    ap.add_argument("--dry", type=float, default=0.5, help="pullback avg volume <= this x run avg volume")
    ap.add_argument("--within", type=int, default=60)
    ap.add_argument("--confirm", type=int, default=10)
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

    def outs(o, h, l, c, e, sign, sl_far, sl_near=None):
        # Ray 2026-10-14 「停損在高點對吧」: the against-run trade with its stop at the run's near extreme
        # (run high for a short after an up run), TP 3.5 and hold; plus TP 1.5 / 2 for the same stop.
        rev = {}
        if sl_near is not None:
            rev = {"rev_nearsl": outcome(o, h, l, c, e, -sign, 3.5, max(sl_near, 0.3), COST)[0],
                   "rev_hold_nearsl": outcome(o, h, l, c, e, -sign, 1e6, max(sl_near, 0.3), COST)[0],
                   "rev_tp2_nearsl": outcome(o, h, l, c, e, -sign, 2.0, max(sl_near, 0.3), COST)[0],
                   "rev_tp15_nearsl": outcome(o, h, l, c, e, -sign, 1.5, max(sl_near, 0.3), COST)[0],
                   "rev_tp3_nearsl": outcome(o, h, l, c, e, -sign, 3.0, max(sl_near, 0.3), COST)[0],
                   "rev_tp25_nearsl": outcome(o, h, l, c, e, -sign, 2.5, max(sl_near, 0.3), COST)[0]}
        return {**rev,"net": outcome(o, h, l, c, e, sign, 3.5, 3.0, COST)[0], "exit": outcome(o, h, l, c, e, sign, 3.5, 3.0, COST)[1],
                "hold": outcome(o, h, l, c, e, sign, 1e6, 3.0, COST)[0],
                "net_farsl": outcome(o, h, l, c, e, sign, 3.5, max(sl_far, 0.3), COST)[0],
                "hold_farsl": outcome(o, h, l, c, e, sign, 1e6, max(sl_far, 0.3), COST)[0],
                "net_rev": outcome(o, h, l, c, e, -sign, 3.5, 3.0, COST)[0], "hold_rev": outcome(o, h, l, c, e, -sign, 1e6, 3.0, COST)[0]}

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
            cum = np.cumsum(v)
            found = False
            i = 5
            nb = len(bars)
            while i < nb - 1:
                if hh[i] > "12:00":
                    break
                avg = cum[i - 1] / i
                j = i
                while j < nb and v[j] >= args.vol * avg and j - i < 30:
                    j += 1
                k = j - i
                if k < args.k:
                    i += 1
                    continue
                seg_c = c[i:j]
                n_dn = sum(seg_c[t] < seg_c[t - 1] for t in range(1, k))
                n_up = sum(seg_c[t] > seg_c[t - 1] for t in range(1, k))
                move = (c[j - 1] / o[i] - 1) * 100
                up = move >= args.move and n_dn <= 1
                dn = move <= -args.move and n_up <= 1
                if not (up or dn):
                    i += 1
                    continue
                run_avg = v[i:j].mean()
                run_hi, run_lo = h[i:j].max(), l[i:j].min()
                mid = (run_hi + run_lo) / 2
                u = None
                for t in range(j, min(j + args.within, nb - 1)):
                    if hh[t] > "12:00":
                        break
                    # the pullback must not make a new extreme beyond the run first
                    if (up and h[t] > run_hi) or (dn and l[t] < run_lo):
                        break
                    if (up and l[t] <= mid) or (dn and h[t] >= mid):
                        u = t
                        break
                if u is None or hh[u] < "09:10":
                    i = j
                    continue
                pb_vol_x = v[j:u + 1].mean() / max(run_avg, 1e-9)
                if pb_vol_x > args.dry:
                    i = j
                    continue
                found = True
                sign = 1 if up else -1  # "with the run"
                far = run_lo if up else run_hi
                near = run_hi if up else run_lo
                ext_i = i + int(np.argmax(h[i:j])) if up else i + int(np.argmin(l[i:j]))
                hi5_vol, open5_vol = v[max(ext_i - 4, 0):ext_i + 1].sum(), v[:5].sum()
                e_a = u + 1
                rec = {"date": d, "stock_id": sid, "run_dir": "up" if up else "dn", "run_start": hh[i], "run_end": hh[j - 1],
                       "touch": hh[u], "k": k, "move": move, "run_vol_x": run_avg / max(avg, 1e-9), "pb_bars": u - j + 1,
                       "pb_vol_x": pb_vol_x, "touch_vol_x": v[u] / max(run_avg, 1e-9),
                       "hi5_vol": hi5_vol, "open5_vol": open5_vol, "hi5_x_open5": hi5_vol / max(open5_vol, 1e-9),
                       "entry_pct": (o[e_a] / prev_close - 1) * 100, "entry_price": o[e_a],
                       "hi_pct": (hi_so_far[u] / prev_close - 1) * 100, "lo_pct": (lo_so_far[u] / prev_close - 1) * 100,
                       "vs_vwap": (c[u] / vwap[u] - 1) * 100, "time": hh[u],
                       "sl_far": abs(far / o[e_a] - 1) * 100, "sl_near": abs(near / o[e_a] - 1) * 100}
                rec.update(outs(o, h, l, c, e_a, sign, rec["sl_far"], rec["sl_near"]))
                # entry B: confirmation bar
                cf = None
                for t in range(u + 1, min(u + 1 + args.confirm, nb - 1)):
                    if hh[t] > "12:00":
                        break
                    if (up and c[t] > h[t - 1]) or (dn and c[t] < l[t - 1]):
                        cf = t
                        break
                rec.update({"cf_time": None, "cf_entry_pct": np.nan, "cf_entry_price": np.nan, "cf_hi_pct": np.nan, "cf_lo_pct": np.nan, "cf_sl_far": np.nan,
                            "cf_sl_near": np.nan,
                            **{f"cf_{x}": np.nan for x in ("net", "exit", "hold", "net_farsl", "hold_farsl", "net_rev", "hold_rev",
                                                           "rev_nearsl", "rev_hold_nearsl", "rev_tp2_nearsl", "rev_tp15_nearsl",
                                                           "rev_tp3_nearsl", "rev_tp25_nearsl")}})
                if cf is not None:
                    e_b = cf + 1
                    slf, sln = abs(far / o[e_b] - 1) * 100, abs(near / o[e_b] - 1) * 100
                    rec.update({"cf_time": hh[cf], "cf_entry_pct": (o[e_b] / prev_close - 1) * 100, "cf_entry_price": o[e_b],
                                "cf_hi_pct": (hi_so_far[cf] / prev_close - 1) * 100, "cf_lo_pct": (lo_so_far[cf] / prev_close - 1) * 100,
                                "cf_sl_far": slf, "cf_sl_near": sln, **{f"cf_{x}": y for x, y in outs(o, h, l, c, e_b, sign, slf, sln).items()}})
                rows.append(rec)
                i = u + 1
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
