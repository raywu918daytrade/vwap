"""H1 bars from M1 for the daytrade universe: 09:00-09:59, 10:00-10:59, 11:00-11:59, 12:00-12:59,
13:00-close (hour 0..4), raw prices like M1.

    python -m scripts.build_h1 --from-month 2020-01 --to-month 2020-12 --out h1_2020.parquet
"""

import argparse

import pandas as pd

from pattern.vwap_sr_scan import stock_ids_for_universe
from scripts.build_feature_table_long import load_m1_month, months


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-month", required=True)
    ap.add_argument("--to-month", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    ids = set(stock_ids_for_universe("daytrade"))
    out = []
    for stem in months(args.from_month, args.to_month):
        m1 = load_m1_month(stem, ids)
        if m1.empty:
            continue
        m1["hour"] = (m1["hhmm"].str[:2].astype(int) - 9).clip(upper=4).astype("int8")
        g = m1.groupby(["stock_id", "day", "hour"], sort=True)
        h1 = pd.DataFrame({"o": g["open"].first(), "h": g["high"].max(), "l": g["low"].min(),
                           "c": g["close"].last(), "v": g["volume"].sum()}).reset_index()
        out.append(h1.rename(columns={"day": "date"}))
        print(stem, len(h1), flush=True)
    res = pd.concat(out, ignore_index=True)
    for k in ("o", "h", "l", "c", "v"):
        res[k] = res[k].astype("float32")
    res.to_parquet(args.out, index=False, compression="zstd")
    print("rows", len(res), res["stock_id"].nunique())


if __name__ == "__main__":
    main()
