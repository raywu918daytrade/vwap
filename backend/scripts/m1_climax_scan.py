"""Ray 2026-10-14: 連續大量後的無力反轉. Climax = >= --k consecutive M1 bars, each with volume >= --vol x the
average M1 volume of the day so far (before the run), net move >= --move % with at most one counter bar inside the run. Exhaustion =
within --within bars after the run, the first bar whose volume <= --dry x the run's average volume AND whose close
reverses through the last run bar (below its low after an up-run -> short; above its high after a down-run ->
long). Standard outcomes (next-M1-open entry, TP3.5/SL3 and hold, cost 0.435 baked) and reversed outcomes are
recorded; --dump saves the M1 paths (with volume) of the most recent stock-days with an event.

    python -m scripts.m1_climax_scan --days pr90_days.parquet --year 2026 --out climax_2026.parquet --dump 12
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
    ap.add_argument("--within", type=int, default=5)
    ap.add_argument("--dry", type=float, default=0.5)
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
            prev_close = o[0] / gaps[(sid, d)]
            hi_so_far, lo_so_far = np.maximum.accumulate(h), np.minimum.accumulate(l)
            cum = np.cumsum(v)
            found = False
            i = 5
            while i < len(bars) - 1:
                if hh[i] > "13:00":
                    break
                # run start candidate at i: avg volume of the day before i
                avg = cum[i - 1] / i
                j = i
                while j < len(bars) and v[j] >= args.vol * avg and j - i < 30:
                    j += 1
                k = j - i
                if k < args.k:
                    i += 1
                    continue
                seg_c = c[i:j]
                n_dn = sum(seg_c[t] < seg_c[t - 1] for t in range(1, k))
                n_up = sum(seg_c[t] > seg_c[t - 1] for t in range(1, k))
                move = (c[j - 1] / o[i] - 1) * 100
                up = move >= args.move and n_dn <= 1  # at most one counter bar inside the run
                dn = move <= -args.move and n_up <= 1
                if not (up or dn):
                    i += 1
                    continue
                run_avg = v[i:j].mean()
                side = None
                for t in range(j, min(j + args.within, len(bars) - 1)):
                    if v[t] <= args.dry * run_avg and ((up and c[t] < l[j - 1]) or (dn and c[t] > h[j - 1])):
                        side = "short" if up else "long"
                        break
                if side is None:
                    i = j
                    continue
                if hh[t] < "09:10":
                    i = j
                    continue
                found = True
                sign = -1 if side == "short" else 1
                net, ex = outcome(o, h, l, c, t + 1, sign, 3.5, 3.0, COST)
                hold, _ = outcome(o, h, l, c, t + 1, sign, 1e6, 3.0, COST)
                net_r, _ = outcome(o, h, l, c, t + 1, -sign, 3.5, 3.0, COST)
                hold_r, _ = outcome(o, h, l, c, t + 1, -sign, 1e6, 3.0, COST)
                rows.append({"date": d, "stock_id": sid, "side": side, "run_start": hh[i], "run_end": hh[j - 1], "time": hh[t],
                             "k": k, "move": move, "run_vol_x": run_avg / max(avg, 1e-9), "rev_vol_x": v[t] / max(run_avg, 1e-9),
                             "bars_after": t - (j - 1), "entry_pct": (o[t + 1] / prev_close - 1) * 100, "entry_price": o[t + 1],
                             "hi_pct": (hi_so_far[t] / prev_close - 1) * 100, "lo_pct": (lo_so_far[t] / prev_close - 1) * 100,
                             "vs_vwap": (c[t] / vwap[t] - 1) * 100,
                             "net": net, "exit": ex, "hold": hold, "net_rev": net_r, "hold_rev": hold_r})
                i = t + 1
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
