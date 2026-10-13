"""After an M5 setup, wait for an M1 to cross VWAP (short: close goes from >= VWAP to < VWAP; long:
from <= to >), then enter at the next M1 open. Exits TP 3.5 / SL 3 and no-TP / SL 3, both closing
at 13:24, cost 0.435% (slippage is added in the study). The cross must come after the setup M5 and
by --last-cross; a stock already past VWAP at the setup waits for a fresh cross.

    python -m scripts.vwap_after_setup --setups setups.csv --year 2024 --out vwap_entries_2024.parquet
"""

import argparse

import numpy as np
import pandas as pd

from data.adjustment_query import load_pattern_day
from scripts.build_feature_table import outcome
from scripts.build_feature_table_long import atr_table, load_m1_month

COST = 0.435


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setups", required=True)
    ap.add_argument("--year", required=True)
    ap.add_argument("--last-cross", default="13:00")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    st = pd.read_csv(args.setups, dtype={"stock_id": str})
    st = st[st["date"].str[:4] == args.year]
    ids = set(st["stock_id"])
    day = load_pattern_day(start_date=f"{int(args.year) - 1}-11-01", end_date=f"{args.year}-12-31")
    day["stock_id"] = day["stock_id"].astype(str)
    day = day[day["stock_id"].isin(ids) & (day["open"] > 0)]
    gaps = {k: g for k, (_, g) in atr_table(day).items()}
    rows = []
    for stem, sg in st.groupby(st["date"].str[:7].str.replace("-", "_")):
        m1 = load_m1_month(stem, ids)
        if m1.empty:
            continue
        for t in sg.itertuples(index=False):
            bars = m1[(m1["stock_id"] == t.stock_id) & (m1["day"] == t.date)].reset_index(drop=True)
            if bars.empty or (t.stock_id, t.date) not in gaps:
                continue
            o, h, l, c = (bars[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
            v = bars["volume"].astype(float).to_numpy()
            vwap = np.cumsum(c * v) / np.maximum(np.cumsum(v), 1e-9)
            hh = bars["hhmm"].to_numpy()
            prev_close = o[0] / gaps[(t.stock_id, t.date)]
            sign = -1 if t.side == "short" else 1
            entry_i = None
            for k in np.flatnonzero((hh > t.time) & (hh <= args.last_cross)):
                if k == 0 or k + 1 >= len(bars):
                    continue
                crossed = (c[k - 1] >= vwap[k - 1] and c[k] < vwap[k]) if sign < 0 else (c[k - 1] <= vwap[k - 1] and c[k] > vwap[k])
                if crossed:
                    entry_i = k + 1
                    break
            r = {"date": t.date, "stock_id": t.stock_id, "side": t.side, "setup_time": t.time, "entry_time": None}
            if entry_i is not None:
                net, ex = outcome(o, h, l, c, entry_i, sign, 3.5, 3.0, COST)
                hold, hx = outcome(o, h, l, c, entry_i, sign, 1e6, 3.0, COST)
                r.update(entry_time=hh[entry_i], entry_pct=(o[entry_i] / prev_close - 1) * 100,
                         hi_pct=(h[:entry_i].max() / prev_close - 1) * 100, lo_pct=(l[:entry_i].min() / prev_close - 1) * 100,
                         net=net, exit=ex, hold=hold)
            rows.append(r)
        print(stem, len(rows), flush=True)
    pd.DataFrame(rows).to_parquet(args.out, index=False)
    print("rows", len(rows))


if __name__ == "__main__":
    main()
