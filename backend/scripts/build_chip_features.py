"""Daily chip features (institutional net buy, margin / short balances, liquidity) per stock-day.

Every feature for trading date t uses only data up to t-1 (the previous trading day), so it
is known before the open. Sources:
- db/ib, db/margin from the public HF dataset raywu918python/j1s-data (downloaded to --chips-dir)
- D1 from db/adjustment_day (load_pattern_day) for volume, turnover and the trading calendar

Usage: python -m scripts.build_chip_features --chips-dir j1s --out db/chips_daily.parquet
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from data.adjustment_query import load_pattern_day
from pattern.vwap_sr_scan import stock_ids_for_universe

START = "2019-09-01"


def read_dir(d: Path) -> pd.DataFrame:
    parts = []
    for p in sorted(d.glob("*.parquet")):
        x = pd.read_parquet(p)
        x["stock_id"] = x["stock_id"].astype(str)
        x["date"] = pd.to_datetime(x["date"].astype(str).str[:10])
        parts.append(x)
    return pd.concat(parts, ignore_index=True)


def streak(s: pd.Series) -> pd.Series:
    """Signed count of consecutive days with the same sign (buy +, sell -)."""
    sign = np.sign(s.fillna(0)).astype(int)
    grp = (sign != sign.shift()).cumsum()
    return sign * sign.groupby(grp).cumcount().add(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chips-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    root = Path(args.chips_dir)

    stock_ids = stock_ids_for_universe("daytrade")
    day = load_pattern_day(start_date=START)
    day["stock_id"] = day["stock_id"].astype(str)
    day = day[day["stock_id"].isin(stock_ids) & (day["open"] > 0)][["stock_id", "date", "close", "volume"]]

    ib = read_dir(root / "ib")
    ib = ib[ib["stock_id"].isin(stock_ids) & (ib["date"] >= START)]
    ib = ib.drop_duplicates(["stock_id", "date", "name"], keep="last")
    ib["net"] = ib["buy"].astype(float) - ib["sell"].astype(float)
    group = ib["name"].map({"Foreign_Investor": "f", "Foreign_Dealer_Self": "f", "Investment_Trust": "t",
                            "Dealer_self": "d", "Dealer_Hedging": "d"})
    print("ib names", ib["name"].value_counts().to_dict(), flush=True)
    ib = ib.assign(g=group).dropna(subset=["g"])
    ibp = ib.pivot_table(index=["stock_id", "date"], columns="g", values="net", aggfunc="sum").reset_index()
    gross = ib.assign(gross=ib["buy"].astype(float) + ib["sell"].astype(float)).groupby(["stock_id", "date"])["gross"].sum()

    mg = read_dir(root / "margin")
    mg = mg[mg["stock_id"].isin(stock_ids) & (mg["date"] >= START)].drop_duplicates(["stock_id", "date"], keep="last")
    mg = mg[["stock_id", "date", "margin_purchase_today_balance", "margin_purchase_limit", "margin_purchase_buy",
             "margin_purchase_sell", "short_sale_today_balance", "short_sale_sell", "short_sale_buy",
             "offset_loan_and_short"]].astype({c: float for c in ["margin_purchase_today_balance", "margin_purchase_limit",
                                               "margin_purchase_buy", "margin_purchase_sell", "short_sale_today_balance",
                                               "short_sale_sell", "short_sale_buy", "offset_loan_and_short"]})

    df = day.merge(ibp, on=["stock_id", "date"], how="left").merge(mg, on=["stock_id", "date"], how="left")
    df = df.merge(gross.rename("ib_gross").reset_index(), on=["stock_id", "date"], how="left")

    # ib is in shares; D1 volume may be in shares or lots. Margin is in lots.
    ratio = (df["ib_gross"] / df["volume"].replace(0, np.nan)).median()
    vol_shares = df["volume"] * (1000.0 if ratio > 50 else 1.0)
    print(f"median ib_gross/volume {ratio:.2f} -> D1 volume in {'lots' if ratio > 50 else 'shares'}", flush=True)
    df["vol_sh"] = vol_shares
    df["vol_lot"] = vol_shares / 1000.0
    df["amt"] = df["close"] * vol_shares

    out = []
    for sid, g in df.sort_values(["stock_id", "date"]).groupby("stock_id", sort=False):
        g = g.reset_index(drop=True)
        r = pd.DataFrame({"stock_id": sid, "date": g["date"]})
        v, v5, v20 = g["vol_sh"], g["vol_sh"].rolling(5).sum(), g["vol_sh"].rolling(20).sum()
        for k in ("f", "t", "d"):
            net = g[k] if k in g else pd.Series(np.nan, index=g.index)
            r[f"ch_{k}_net1"] = net / v
            r[f"ch_{k}_net5"] = net.rolling(5).sum() / v5
            r[f"ch_{k}_net20"] = net.rolling(20).sum() / v20
            r[f"ch_{k}_streak"] = streak(net)
        allnet = g[[c for c in ("f", "t", "d") if c in g]].sum(axis=1, min_count=1)
        r["ch_all_net1"] = allnet / v
        r["ch_all_net5"] = allnet.rolling(5).sum() / v5
        mb, sb = g["margin_purchase_today_balance"], g["short_sale_today_balance"]
        vl20 = g["vol_lot"].rolling(20).mean()
        r["ch_mg_chg1"] = (mb / mb.shift(1) - 1) * 100
        r["ch_mg_chg5"] = (mb / mb.shift(5) - 1) * 100
        r["ch_ss_chg1"] = (sb - sb.shift(1)) / vl20
        r["ch_ss_chg5"] = (sb - sb.shift(5)) / vl20
        r["ch_mg_bal_vol"] = mb / vl20
        r["ch_ss_bal_vol"] = sb / vl20
        r["ch_ss_mg_ratio"] = sb / mb.replace(0, np.nan) * 100
        r["ch_mg_use"] = mb / g["margin_purchase_limit"].replace(0, np.nan) * 100
        r["ch_mg_net_vol"] = (g["margin_purchase_buy"] - g["margin_purchase_sell"]) / g["vol_lot"]
        r["ch_offset_vol"] = g["offset_loan_and_short"] / g["vol_lot"]
        r["liq_amt20"] = g["amt"].rolling(20).mean() / 1e8
        r["liq_amt1"] = g["amt"] / 1e8
        feats = [c for c in r.columns if c.startswith(("ch_", "liq_"))]
        # Shift by one trading day: the row for date t carries t-1's values.
        r[feats] = r[feats].shift(1)
        out.append(r)
    res = pd.concat(out, ignore_index=True)
    res = res[res["date"] >= "2020-01-01"]
    feats = [c for c in res.columns if c.startswith(("ch_", "liq_"))]
    res[feats] = res[feats].replace([np.inf, -np.inf], np.nan).astype("float32")
    res["date"] = res["date"].dt.strftime("%Y-%m-%d")
    res.to_parquet(args.out, index=False)
    print(f"rows {len(res)}, stocks {res['stock_id'].nunique()}, dates {res['date'].min()}..{res['date'].max()}")
    print("non-null share by year:")
    print(res.assign(y=res["date"].str[:4]).groupby("y")[["ch_f_net1", "ch_mg_chg1", "liq_amt20"]].apply(lambda x: x.notna().mean()).round(3).to_string())
    print(res[feats].describe(percentiles=[0.05, 0.5, 0.95]).T.round(4).to_string())


if __name__ == "__main__":
    main()
