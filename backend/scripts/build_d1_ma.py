"""Daily MA context per stock-day, using only days before the date (known before the open).

Columns (all % relative to the previous close): d1_ma5_pct, d1_ma10_pct, d1_ma20_pct = MA / prev close - 1,
d1_ma20_slope5 = MA20 now vs 5 days ago, d1_below_all = prev close under MA5, MA10 and MA20,
d1_bear_stack = MA5 < MA10 < MA20.

    python -m scripts.build_d1_ma --out db/d1_ma.parquet
"""

import argparse

import numpy as np
import pandas as pd

from data.adjustment_query import load_pattern_day
from pattern.vwap_sr_scan import stock_ids_for_universe


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    ids = stock_ids_for_universe("daytrade")
    day = load_pattern_day(start_date="2019-06-01")
    day["stock_id"] = day["stock_id"].astype(str)
    day = day[day["stock_id"].isin(ids) & (day["close"] > 0)]
    out = []
    for sid, g in day.sort_values(["stock_id", "date"]).groupby("stock_id", sort=False):
        c = g["close"].astype(float).reset_index(drop=True)
        ma5, ma10, ma20 = c.rolling(5).mean(), c.rolling(10).mean(), c.rolling(20).mean()
        r = pd.DataFrame({"stock_id": sid, "date": g["date"].dt.strftime("%Y-%m-%d").to_numpy()})
        # Values as of the previous day's close, attached to today's row.
        prev = c.shift(1)
        r["d1_ma5_pct"] = (ma5.shift(1) / prev - 1) * 100
        r["d1_ma10_pct"] = (ma10.shift(1) / prev - 1) * 100
        r["d1_ma20_pct"] = (ma20.shift(1) / prev - 1) * 100
        r["d1_ma20_slope5"] = (ma20.shift(1) / ma20.shift(6) - 1) * 100
        r["d1_below_all"] = ((r["d1_ma5_pct"] > 0) & (r["d1_ma10_pct"] > 0) & (r["d1_ma20_pct"] > 0)).astype("int8")
        r["d1_bear_stack"] = ((ma5 < ma10) & (ma10 < ma20)).shift(1, fill_value=False).astype("int8")
        out.append(r)
    res = pd.concat(out, ignore_index=True)
    res = res[res["date"] >= "2020-01-01"]
    for c in ("d1_ma5_pct", "d1_ma10_pct", "d1_ma20_pct", "d1_ma20_slope5"):
        res[c] = res[c].astype("float32")
    res.to_parquet(args.out, index=False)
    print(len(res), res["stock_id"].nunique(), res["date"].min(), res["date"].max())
    print(res[["d1_below_all", "d1_bear_stack"]].mean().round(3).to_dict())


if __name__ == "__main__":
    main()
