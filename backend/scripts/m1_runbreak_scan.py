"""Ray 2026-10-16: 「量的比對刪掉，改成跌破最大量或最長K再空」 — after a burst of consecutive high-volume M1 bars
(same run definition as scripts.m1_climax_scan), short when an M1 closes below the low of the run's biggest-volume
bar ("maxvol") or below the low of the run's longest bar, high-low range ("longest"); long mirror after a down run.
Each trigger is scanned separately (within --within bars, before 12:00, and before price makes a new extreme beyond
the run). Entry next open. Outcomes: TP 3.5 / SL 3, hold, SL at the run's near extreme (run high for the short)
with TP 3.5 / 3 / 2.5 / 2 / hold. Runs on GitHub Actions where HF M1 data is available.

    python -m scripts.m1_runbreak_scan --days pr50_days.parquet --year 2025 --out runbreak_2025.parquet [--dump 400]
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
    ap.add_argument("--within", type=int, default=60)
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
                near = run_hi if up else run_lo
                far = run_lo if up else run_hi
                sign = -1 if up else 1
                b_vol = i + int(np.argmax(v[i:j]))
                b_len = i + int(np.argmax(h[i:j] - l[i:j]))
                last_t = j
                for trig, bi in (("maxvol", b_vol), ("longest", b_len)):
                    lvl = l[bi] if up else h[bi]
                    t = None
                    for u in range(j, min(j + args.within, nb - 1)):
                        if hh[u] > "12:00" or (up and h[u] > run_hi) or (dn and l[u] < run_lo):
                            break
                        if (up and c[u] < lvl) or (dn and c[u] > lvl):
                            t = u
                            break
                    if t is None or hh[t] < "09:10":
                        continue
                    found = True
                    e = t + 1
                    sl_near = abs(near / o[e] - 1) * 100
                    rec = {"date": d, "stock_id": sid, "trigger": trig, "side": "short" if up else "long",
                           "run_start": hh[i], "run_end": hh[j - 1], "bar": hh[bi], "level": lvl, "time": hh[t],
                           "k": k, "move": move, "run_vol_x": run_avg / max(avg, 1e-9), "bars_after": t - (j - 1),
                           "lvl_from_hi": abs(lvl / near - 1) * 100, "brk_vol_x": v[t] / max(run_avg, 1e-9),
                           "entry_pct": (o[e] / prev_close - 1) * 100, "entry_price": o[e],
                           "hi_pct": (hi_so_far[t] / prev_close - 1) * 100, "lo_pct": (lo_so_far[t] / prev_close - 1) * 100,
                           "vs_vwap": (c[t] / vwap[t] - 1) * 100, "sl_near": sl_near}
                    net, ex = outcome(o, h, l, c, e, sign, 3.5, 3.0, COST)
                    rec.update({"net": net, "exit": ex, "hold": outcome(o, h, l, c, e, sign, 1e6, 3.0, COST)[0]})
                    sn = max(sl_near, 0.3)
                    for name, tp in (("nearsl", 3.5), ("tp3_nearsl", 3.0), ("tp25_nearsl", 2.5), ("tp2_nearsl", 2.0), ("hold_nearsl", 1e6)):
                        g, gx = outcome(o, h, l, c, e, sign, tp, sn, COST)
                        rec[name] = g
                        if name == "nearsl":
                            rec["exit_nearsl"] = gx
                    rows.append(rec)
                    last_t = max(last_t, t + 1)
                i = last_t
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
