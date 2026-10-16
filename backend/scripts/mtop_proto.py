"""Prototype: the project's own D1 M-top detector (pattern.m_top.MTopDetector, four-segment symmetry) applied to M1
bars, bar by bar; a signal is the first bar whose detect() says breakout_down (close <= neckline). Records whether
the close is also below VWAP. Used to draw examples for Ray before a full scan.
"""

import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from pattern.m_top.detector import MTopDetector

# D1 project defaults (pattern_api "m_top": MTopDetector()) except depth capped at 5% (Ray: M頭區間不要超過5%).
DET = MTopDetector(window=3, max_top_diff_pct=0.05, min_depth_pct=0.03, max_depth_pct=0.05,
                   min_top_dist=5, max_top_dist=60, min_candles=20, max_candles=120)
MIN_SCORE = 60.0  # same threshold as the D1 scan API default


def one(args):
    (d, sid), b = args
    b = b.sort_values("time").reset_index(drop=True)
    df = pd.DataFrame({"date": b["time"], "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"], "volume": b["v"]})
    o, h, l, c, vw = (b[k].to_numpy(float) for k in ("o", "h", "l", "c", "vwap"))
    hh = b["time"].tolist()
    out, seen = [], set()
    for t in range(20, len(b) - 1):
        if hh[t] < "09:10":
            continue
        if hh[t] > "12:00":
            break
        r = DET.detect(df.iloc[:t + 1], sid, "1m")
        if r is None or r.score < MIN_SCORE or r.details["breakout_status"] != "breakout_down":
            continue
        key = r.details["h2_date"]
        if key in seen:
            continue
        seen.add(key)
        pv = {p.type + str(i): p for i, p in enumerate(r.pivots)}
        l0, h1, lo, h2 = r.pivots
        e = t + 1
        m_hi = max(h1.price, h2.price)
        out.append({"date": d, "stock_id": sid, "side": "short", "time": hh[t], "l0_time": l0.date, "p1_time": h1.date, "tr_time": lo.date,
                    "p2_time": h2.date, "d_time": r.lines[3].end_date, "d_price": r.lines[3].end_price, "p1_hi": h1.price, "p2_hi": h2.price, "neck": lo.price, "m_hi": m_hi, "score": r.score,
                    "depth_pct": r.details["neckline_depth_pct"], "diff_pct": r.details["top_diff_pct"], "below_vwap": bool(c[t] < vw[t]),
                    "entry_price": o[e], "sl_near": (m_hi / o[e] - 1) * 100, "prev_close": float(b["prev_close"][0]),
                    "entry_pct": (o[e] / b["prev_close"][0] - 1) * 100, "hi_pct": (h[:t + 1].max() / b["prev_close"][0] - 1) * 100,
                    "rise_pct": (h1.price / l0.price - 1) * 100})
        if len(out) >= 3:
            break
    return out


if __name__ == "__main__":
    K = "/mnt/project-files/vwap-backtest/key-price/mtop/"
    p = pd.read_parquet(K + "mtop_2026_paths.parquet")
    p["stock_id"] = p["stock_id"].astype(str)
    groups = list(p.groupby(["date", "stock_id"]))
    if len(sys.argv) > 1:
        groups = groups[:int(sys.argv[1])]
    with Pool(8) as pool:
        res = pool.map(one, groups, chunksize=4)
    df = pd.DataFrame([r for rs in res for r in rs])
    print(len(df), df.groupby(["date", "stock_id"]).ngroups if len(df) else 0, "of", len(groups))
    if len(df):
        print(df[["depth_pct", "diff_pct", "rise_pct", "score"]].describe().round(2).to_string())
        print("below vwap at neckline break:", round(df["below_vwap"].mean(), 2))
        df.to_parquet(K + "mtop_d1def_proto.parquet", index=False)
