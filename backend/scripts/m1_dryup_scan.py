"""Ray 2026-10-14: 「那改成量縮呢」 — after a burst of consecutive high-volume M1 bars (same run definition as
scripts.m1_climax_scan), look for a 量縮整理: >= --n consecutive bars each with volume <= --dry x the run's average
volume whose closes stay inside the run's high-low range. The box is those dry bars' high/low (extended while bars stay
dry and inside the run range, max --maxbox). Then within --within bars the first M1 close above the box high -> long,
below the box low -> short; entry at the next open. Outcomes: TP 3.5 / SL 3 (net, exit, hold) and SL at the opposite
box edge (net_boxsl). Runs on GitHub Actions where the HF M1 data is available.

    python -m scripts.m1_dryup_scan --days pr50_days.parquet --year 2024 --out dryup_2024.parquet [--dump 10]
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
    ap.add_argument("--n", type=int, default=3, help="min dry bars in the box")
    ap.add_argument("--dry", type=float, default=0.5, help="dry bar volume <= this x run avg volume")
    ap.add_argument("--maxbox", type=int, default=20)
    ap.add_argument("--within", type=int, default=15, help="bars after the box to wait for the break")
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
            nb = len(bars)
            while i < nb - 1:
                if hh[i] > "12:30":
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
                # dry box: consecutive bars from j with low volume and closes inside the run range
                b = j
                while b < nb - 1 and b - j < args.maxbox and v[b] <= args.dry * run_avg and run_lo <= c[b] <= run_hi:
                    b += 1
                nbox = b - j
                if nbox < args.n:
                    i = j
                    continue
                box_hi, box_lo = h[j:b].max(), l[j:b].min()
                side, t = None, None
                for t in range(b, min(b + args.within, nb - 1)):
                    if hh[t] > "12:30":
                        break
                    if c[t] > box_hi:
                        side = "long"
                        break
                    if c[t] < box_lo:
                        side = "short"
                        break
                if side is None or hh[t] < "09:10":
                    i = b
                    continue
                found = True
                sign = 1 if side == "long" else -1
                e0 = o[t + 1]
                sl_box = abs((box_lo if side == "long" else box_hi) / e0 - 1) * 100
                net, ex = outcome(o, h, l, c, t + 1, sign, 3.5, 3.0, COST)
                rows.append({"date": d, "stock_id": sid, "side": side, "run_dir": "up" if up else "dn",
                             "run_start": hh[i], "run_end": hh[j - 1], "box_end": hh[b - 1], "time": hh[t],
                             "k": k, "move": move, "run_vol_x": run_avg / max(avg, 1e-9), "box_n": nbox,
                             "box_rng": (box_hi / box_lo - 1) * 100, "box_vol_x": v[j:b].mean() / max(run_avg, 1e-9),
                             "brk_vol_x": v[t] / max(run_avg, 1e-9), "sl_box": sl_box,
                             "entry_pct": (e0 / prev_close - 1) * 100, "entry_price": e0,
                             "hi_pct": (hi_so_far[t] / prev_close - 1) * 100, "lo_pct": (lo_so_far[t] / prev_close - 1) * 100,
                             "vs_vwap": (c[t] / vwap[t] - 1) * 100,
                             "net": net, "exit": ex, "hold": outcome(o, h, l, c, t + 1, sign, 1e6, 3.0, COST)[0],
                             "net_boxsl": outcome(o, h, l, c, t + 1, sign, 3.5, max(sl_box, 0.3), COST)[0],
                             "hold_boxsl": outcome(o, h, l, c, t + 1, sign, 1e6, max(sl_box, 0.3), COST)[0],
                             "net_rev": outcome(o, h, l, c, t + 1, -sign, 3.5, 3.0, COST)[0],
                             "hold_rev": outcome(o, h, l, c, t + 1, -sign, 1e6, 3.0, COST)[0]})
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
