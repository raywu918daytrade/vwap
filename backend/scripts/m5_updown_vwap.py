"""Short after an M5 up-then-down pair, on the first later VWAP break (Ray, 2026-10-12).

Setup (state) at an M5 close: the previous M5 was red and this M5 is black. Once a stock-day has
had the setup, the first M5 close (same bar or later) that crossed below VWAP in the last 5 minutes
and closes under VWAP triggers a short at the next M1 open.
Variants: red bar size >= 0 / 1%, black bar at least as big as the red (吞噬), setup above VWAP.
Standard 2026-10-12: ATR>=5, PR>=50, 20d turnover >= 1億, entry <= +5%, no limit-up today,
09:10-12:00, cost + 0.1% slippage, train 2020-22 / test 2023-26.

    python -m scripts.m5_updown_vwap --tables 'ft_*.parquet' --d1 d1_ma.parquet --chips chips_daily.parquet
"""

import argparse
import glob

import pandas as pd

from scripts.chips_model import half
from scripts.d1_rebound_short import SLIP, one_at_a_time, st


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--d1", required=True)
    ap.add_argument("--chips", required=True)
    args = ap.parse_args()
    cols = ["date", "stock_id", "minute", "day_atr", "vol5_pr", "chg_from_prev_close", "from_day_high", "vs_vwap",
            "vwap_cross_dn_5m", "m5_ret", "prev_m5_ret", "prev_m5_red", "short_net", "short_hold"]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in sorted(glob.glob(args.tables))], ignore_index=True)
    df["stock_id"] = df["stock_id"].astype(str)
    df = df[(df["day_atr"] >= 5) & (df["vol5_pr"] >= 50)]
    d1 = pd.read_parquet(args.d1)
    d1["stock_id"] = d1["stock_id"].astype(str)
    ch = pd.read_parquet(args.chips, columns=["stock_id", "date", "liq_amt20"])
    ch["stock_id"] = ch["stock_id"].astype(str)
    df = df.merge(d1, on=["stock_id", "date"], how="left").merge(ch, on=["stock_id", "date"], how="left")
    df = df[df["liq_amt20"].fillna(0) >= 1].sort_values(["date", "stock_id", "minute"]).reset_index(drop=True)
    hi = ((1 + df["chg_from_prev_close"] / 100) / (1 + df["from_day_high"] / 100) - 1) * 100
    # Limit-up touched at or before this bar: carry forward within the stock-day.
    df["touched"] = (hi >= 9.5).astype(int).groupby([df["date"], df["stock_id"]]).cummax().astype(bool)
    for c in ("short_net", "short_hold"):
        df[c] = df[c] - SLIP
    key = [df["date"], df["stock_id"]]
    trig = (df["vwap_cross_dn_5m"] == 1) & (df["vs_vwap"] < 0)
    tradable = (df["chg_from_prev_close"] <= 5) & ~df["touched"] & (df["minute"] <= 180)
    pair = (df["prev_m5_red"] == 1) & (df["m5_ret"] < 0)
    setups = {
        "紅接黑": pair,
        "紅≥1% 接黑": pair & (df["prev_m5_ret"] >= 1),
        "紅接黑，黑≥紅（吞噬）": pair & (-df["m5_ret"] >= df["prev_m5_ret"]),
        "紅接黑，在VWAP上方": pair & (df["vs_vwap"] > 0),
        "紅≥1% 接黑吞噬，在VWAP上方": pair & (df["prev_m5_ret"] >= 1) & (-df["m5_ret"] >= df["prev_m5_ret"]),
    }
    d1_groups = {"不看D1": pd.Series(True, index=df.index), "均線都在上方": df["d1_below_all"] == 1,
                 "均線上方+空頭排列": (df["d1_below_all"] == 1) & (df["d1_bear_stack"] == 1)}
    print("# M5 先上後下 → 之後跌破 VWAP 做空（新標準，09:10-12:00，扣成本＋滑價 0.535%）\n")
    print("| D1 | 型態 | 訓練 筆數 | 勝率 | 停利3.5 平均 | 測試 筆數 | 勝率 | 停利3.5 平均 | 測試 收盤 平均 | 正的半年 | 一次一筆 筆數 | 平均 | 回落 | 最多連輸 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    only_vwap = trig & tradable
    for dn, dm in d1_groups.items():
        rows = [("只看跌破VWAP（對照）", only_vwap)]
        for sn, sm in setups.items():
            seen = sm.astype(int).groupby(key).cummax().astype(bool)
            rows.append((sn, seen & trig & tradable))
        for name, m in rows:
            s = df[m & dm].groupby(["date", "stock_id"], sort=False).head(1)
            a, b = s[s["date"] < "2023-01-01"], s[s["date"] >= "2023-01-01"]
            g = b.groupby(half(b["date"]))["short_net"].mean()
            print(f"| {dn} | {name} | {st(a['short_net'])} | {st(b['short_net'])} | {b['short_hold'].mean():+.3f}% | "
                  f"{(g > 0).sum()}/{len(g)} | {one_at_a_time(b, 'short_net')} |")


if __name__ == "__main__":
    main()
