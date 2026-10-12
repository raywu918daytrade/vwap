"""M5 open/high/low/close (% vs prev close, same basis as the feature table) for given stock-days.

The feature table only stores M5 returns and ranges; the key-price study needs where each M5 high and
low sits relative to the previous close. Rows are keyed like the table: time = last M1 of the M5.

    python -m scripts.build_m5_hl --days stockdays.parquet --from-month 2020-01 --to-month 2020-12 --out m5_hl_2020.parquet
"""

import argparse

import numpy as np
import pandas as pd

from data.adjustment_query import load_pattern_day
from scripts.build_feature_table_long import atr_table, load_m1_month, months
from scripts.build_feature_table import LAST_ENTRY


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", required=True)
    ap.add_argument("--from-month", required=True)
    ap.add_argument("--to-month", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    want = pd.read_parquet(args.days)
    want["stock_id"] = want["stock_id"].astype(str)
    want = set(zip(want["stock_id"], want["date"]))
    ids = {s for s, _ in want}
    first = pd.Timestamp(args.from_month + "-01")
    day = load_pattern_day(start_date=(first - pd.Timedelta(days=60)).strftime("%Y-%m-%d"),
                           end_date=pd.Period(args.to_month, freq="M").end_time.strftime("%Y-%m-%d"))
    day["stock_id"] = day["stock_id"].astype(str)
    day = day[day["stock_id"].isin(ids) & (day["open"] > 0)]
    gaps = {k: g for k, (_, g) in atr_table(day).items()}
    out = []
    for stem in months(args.from_month, args.to_month):
        m1 = load_m1_month(stem, ids)
        if m1.empty:
            continue
        n0 = len(out)
        for (sid, d), bars in m1.groupby(["stock_id", "day"], sort=False):
            if (sid, d) not in want or (sid, d) not in gaps:
                continue
            bars = bars.reset_index(drop=True)
            prev_close = float(bars["open"].iloc[0]) / gaps[(sid, d)]
            mins = bars["hhmm"].str[:2].astype(int) * 60 + bars["hhmm"].str[3:].astype(int)
            bars["bucket"] = mins // 5
            g = bars.groupby("bucket", sort=True)
            m5 = pd.DataFrame({"time": g["hhmm"].last(), "o": g["open"].first(), "h": g["high"].max(),
                               "l": g["low"].min(), "c": g["close"].last()})
            m5 = m5[(m5["time"] >= "09:09") & (m5["time"] < LAST_ENTRY)]
            r = pd.DataFrame({"date": d, "stock_id": sid, "time": m5["time"].to_numpy()})
            for k in ("o", "h", "l", "c"):
                r[f"m5_{k}_pct"] = ((m5[k].to_numpy(float) / prev_close - 1) * 100).astype("float32")
            out.append(r)
        print(stem, len(out) - n0, "stock-days", flush=True)
    res = pd.concat(out, ignore_index=True)
    res.to_parquet(args.out, index=False, compression="zstd")
    print("rows", len(res))


if __name__ == "__main__":
    main()
