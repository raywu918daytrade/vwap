"""Re-check earlier positive short ideas on the 6-year table, with and without signals whose
limit-up lies inside the 3% stop (entry up >= 6.8%) or that already touched limit-up today.

- 隔日沖 v5: 昨漲 >= X, today opened above yesterday's close, previous M5 red, this M5 closes
  below its low, 10:00-13:00; short at the next M1 open.
- 日線背離: D1 MACD bear divergence in the last 5 days, M5 with a VWAP cross down in the last 5 min.
Filters ATR >= 5%, open-5-min PR >= 50; first signal per stock-day; TP3.5/SL3 and hold-to-close.

    python -m scripts.limit_recheck --tables 'ft_*.parquet'
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import first_per_day, half, stat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    args = ap.parse_args()
    cols = ["date", "stock_id", "minute", "day_atr", "vol5_pr", "d1_prev_chg", "gap_open", "prev_m5_red",
            "break_prev_m5_low", "d1_div_bear_5d", "vwap_cross_dn_5m", "chg_from_prev_close", "from_day_high",
            "short_net", "short_hold"]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in sorted(glob.glob(args.tables))], ignore_index=True)
    df = df[(df["day_atr"] >= 5) & (df["vol5_pr"] >= 50)]
    high = ((1 + df["chg_from_prev_close"] / 100) / (1 + df["from_day_high"] / 100) - 1) * 100
    risky = (df["chg_from_prev_close"] >= 6.8) | (high >= 9.5)
    rules = {
        "隨機對照（每檔每天第一個點）": pd.Series(True, index=df.index),
        **{f"隔日沖 v5 昨漲≥{x}%": (df["d1_prev_chg"] >= x) & (df["gap_open"] > 0) & (df["prev_m5_red"] == 1)
           & (df["break_prev_m5_low"] == 1) & df["minute"].between(60, 240) for x in (5, 7, 9.5)},
        "日線頂背離＋下穿VWAP": (df["d1_div_bear_5d"] == 1) & (df["vwap_cross_dn_5m"] == 1),
    }
    print("# 舊的做空策略：排除卡漲停訊號前後（6 年寬表，ATR≥5 PR≥50，每檔每天第一筆）\n")
    print("卡漲停 = 進場時已漲 ≥ 6.8%（漲停在停損 3% 以內）或今天已碰過漲停（≥ 9.5%）。\n")
    print("| 策略 | 版本 | 2020-22 筆數 | 勝率 | 停利3.5 平均 | 2023-26 筆數 | 勝率 | 停利3.5 平均 | 2023-26 收盤平倉 平均 | 2023-26 正的半年（停利3.5） |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for name, m in rules.items():
        for ver, mm in (("原本", m), ("排除卡漲停", m & ~risky)):
            s = first_per_day(df[mm])
            a, b = s[s["date"] < "2023-01-01"], s[s["date"] >= "2023-01-01"]
            g = b.groupby(half(b["date"]))["short_net"].mean()
            print(f"| {name} | {ver} | {stat(a['short_net'])} | {stat(b['short_net'])} | {b['short_hold'].mean():+.3f}% | {(g > 0).sum()}/{len(g)} |")
        s = first_per_day(df[m])
        print(f"| {name} | 卡漲停的比例 | {risky[s.index].mean() * 100:.0f}% | | | | | | | |")


if __name__ == "__main__":
    main()
