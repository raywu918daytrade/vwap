"""D1 均線壓制 + 當天反彈下殺 做空, under the 2026-10-12 backtest standard.

D1 (as of yesterday): prev close below MA5/MA10/MA20 (均線都在上方), optionally MA5 < MA10 < MA20.
Intraday: price first bounced (day high >= open +R%, or day high reached yesterday's MA5 / MA10 price),
then the M5 close triggers the short: crossed below VWAP in the last 5 minutes, or closed under the
previous M5 low while >= 2% off the day high.
Standard: ATR>=5, PR>=50, 20d turnover >= 1億, entry <= +5% vs prev close, no limit-up touched today,
cost 0.435% + slippage 0.1%, train 2020-22 / test 2023-26, first signal per stock-day and one at a time.

    python -m scripts.d1_rebound_short --tables 'ft_*.parquet' --d1 d1_ma.parquet --chips chips_daily.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import first_per_day, half
from scripts.one_at_a_time import flat_dd, streaks

SLIP = 0.1
SPLIT = "2023-01-01"


def st(r: pd.Series) -> str:
    if len(r) == 0:
        return "0 | - | -"
    return f"{len(r)} | {(r > 0).mean() * 100:.1f}% | {r.mean():+.3f}%"


def one_at_a_time(s: pd.DataFrame, col: str) -> str:
    s = s.sort_values(["date", "minute", "stock_id"]).groupby("date").head(1)
    r = s[col].to_numpy(float)
    if len(r) == 0:
        return "0 | - | - | -"
    _, bl, _ = streaks([bool(x) for x in r > 0])
    return f"{len(r)} | {r.mean():+.3f}% | {flat_dd(r):.0f}% | {bl}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--d1", required=True)
    ap.add_argument("--chips", required=True)
    args = ap.parse_args()
    cols = ["date", "stock_id", "minute", "day_atr", "vol5_pr", "chg_from_prev_close", "chg_from_open", "from_day_high",
            "vs_vwap", "vwap_cross_dn_5m", "break_prev_m5_low", "short_net", "short_hold"]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in sorted(glob.glob(args.tables))], ignore_index=True)
    df["stock_id"] = df["stock_id"].astype(str)
    df = df[(df["day_atr"] >= 5) & (df["vol5_pr"] >= 50)]
    d1 = pd.read_parquet(args.d1)
    d1["stock_id"] = d1["stock_id"].astype(str)
    ch = pd.read_parquet(args.chips, columns=["stock_id", "date", "liq_amt20"])
    ch["stock_id"] = ch["stock_id"].astype(str)
    df = df.merge(d1, on=["stock_id", "date"], how="left").merge(ch, on=["stock_id", "date"], how="left")
    df = df[df["liq_amt20"].fillna(0) >= 1]
    hi_prev = ((1 + df["chg_from_prev_close"] / 100) / (1 + df["from_day_high"] / 100) - 1) * 100
    hi_open = ((1 + df["chg_from_open"] / 100) / (1 + df["from_day_high"] / 100) - 1) * 100
    ok = (df["chg_from_prev_close"] <= 5) & (hi_prev < 9.5) & (df["minute"] <= 180)
    df = df[ok].copy()
    hi_prev, hi_open = hi_prev[df.index], hi_open[df.index]
    for c in ("short_net", "short_hold"):
        df[c] = df[c] - SLIP
    print(f"符合標準的 M5 點 {len(df)}，股票日 {df[['date', 'stock_id']].drop_duplicates().shape[0]}；有 D1 均線資料 "
          f"{df['d1_ma5_pct'].notna().mean() * 100:.0f}%\n")

    d1_groups = {"不看 D1": pd.Series(True, index=df.index),
                 "均線都在上方": df["d1_below_all"] == 1,
                 "均線都在上方＋空頭排列": (df["d1_below_all"] == 1) & (df["d1_bear_stack"] == 1)}
    rebounds = {"比開盤高≥1%": hi_open >= 1, "比開盤高≥2%": hi_open >= 2, "比開盤高≥3%": hi_open >= 3,
                "碰到昨日MA5": hi_prev >= df["d1_ma5_pct"], "碰到昨日MA10": hi_prev >= df["d1_ma10_pct"]}
    triggers = {"下穿VWAP": (df["vwap_cross_dn_5m"] == 1) & (df["vs_vwap"] < 0),
                "跌破前M5低且離高點≥2%": (df["break_prev_m5_low"] == 1) & (df["from_day_high"] <= -2)}
    is_tr = df["date"] < SPLIT

    print("# D1 均線壓制 + 反彈下殺 做空（09:10-12:00，扣成本＋滑價 0.535%）\n")
    print("每檔每天第一筆；一次一筆 = 每天只做全市場最早的一筆。停利3.5 = 停利 3.5%/停損 3%；收盤 = 不停利/停損 3%/13:24 平倉。\n")
    print("| D1 | 反彈 | 觸發 | 訓練 筆數 | 勝率 | 停利3.5 平均 | 測試 筆數 | 勝率 | 停利3.5 平均 | 測試 收盤 平均 | 測試 正的半年 | 測試一次一筆 筆數 | 平均(停利3.5) | 回落 | 最多連輸 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for dn, dm in d1_groups.items():
        c = first_per_day(df[dm])
        a, b = c[c["date"] < SPLIT], c[c["date"] >= SPLIT]
        g = b.groupby(half(b["date"]))["short_net"].mean()
        print(f"| {dn} | 對照：隨便空 | - | {st(a['short_net'])} | {st(b['short_net'])} | {b['short_hold'].mean():+.3f}% | "
              f"{(g > 0).sum()}/{len(g)} | {one_at_a_time(b, 'short_net')} |")
        for rn, rm in rebounds.items():
            for tn, tm in triggers.items():
                s = first_per_day(df[dm & rm & tm])
                a, b = s[s["date"] < SPLIT], s[s["date"] >= SPLIT]
                g = b.groupby(half(b["date"]))["short_net"].mean()
                print(f"| {dn} | {rn} | {tn} | {st(a['short_net'])} | {st(b['short_net'])} | "
                      f"{b['short_hold'].mean() if len(b) else float('nan'):+.3f}% | {(g > 0).sum()}/{len(g)} | {one_at_a_time(b, 'short_net')} |")


if __name__ == "__main__":
    main()
