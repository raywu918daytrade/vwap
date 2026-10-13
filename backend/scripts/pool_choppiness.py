"""Ray 2026-10-13: the volatility pool looks 亂. Measure per stock-day how "clean" the day was (VWAP crossings,
path efficiency = |net move| / sum of |M5 moves|) and look for conditions known at 09:05 (or by 09:30) that
separate clean days from choppy ones. Pool = ATR>=5, PR>=50, (open5_rng>=3 or ATR>=7), 20d turnover >= 1億.

    python -m scripts.pool_choppiness --ft 'ft_20??.parquet' --d1 d1_raw.parquet --adx d1_adx.parquet \
        --band d1_vwap_band.parquet --chips chips_daily.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.d1_rebound_short import SPLIT
from scripts.d1_volatility import feats


def main() -> None:
    ap = argparse.ArgumentParser()
    for a in ("--ft", "--d1", "--adx", "--band", "--chips"):
        ap.add_argument(a, required=True)
    args = ap.parse_args()
    cols = ["date", "stock_id", "minute", "day_atr", "vol5_pr", "open5_rng", "chg_from_prev_close", "gap_open", "chg_from_open",
            "from_day_high", "from_day_low", "vs_vwap", "m5_vol_ratio", "cum_vol_vs_prev_day"]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in sorted(glob.glob(args.ft))], ignore_index=True)
    df = df[(df["day_atr"] >= 5) & (df["vol5_pr"] >= 50) & ((df["open5_rng"] >= 3) | (df["day_atr"] >= 7))]
    df["stock_id"] = df["stock_id"].astype(str)
    df = df.sort_values(["date", "stock_id", "minute"])
    g = df.groupby(["date", "stock_id"])
    sign = np.sign(df["vs_vwap"])
    df["x"] = (sign != g["vs_vwap"].shift().pipe(np.sign)).astype(int) * (df["minute"] >= 14)
    df["step"] = g["chg_from_prev_close"].diff().abs()
    early = df[df["minute"] <= 30].groupby(["date", "stock_id"]).agg(e_chg=("chg_from_open", "last"), e_x=("x", "sum"), e_vs=("vs_vwap", "last"))
    day = g.agg(day_atr=("day_atr", "first"), pr=("vol5_pr", "first"), o5=("open5_rng", "first"), gap=("gap_open", "first"),
                oc=("chg_from_open", "last"), hi=("from_day_high", "min"), lo=("from_day_low", "max"), crosses=("x", "sum"),
                path=("step", "sum"), cum_vol=("cum_vol_vs_prev_day", "last"), n=("minute", "size")).reset_index()
    day = day[day["n"] >= 40]
    day = day.merge(early, on=["date", "stock_id"])
    day["eff"] = day["oc"].abs() / day["path"].replace(0, np.nan)
    d1 = pd.read_parquet(args.d1)
    d1["stock_id"] = d1["stock_id"].astype(str)
    d1["date"] = pd.to_datetime(d1["date"]).dt.strftime("%Y-%m-%d")
    d1 = pd.concat([feats(x) for _, x in d1.groupby("stock_id")], ignore_index=True)
    d1 = d1[["date", "stock_id", "rng", "y_rng", "y_chg", "y_limit", "chg5", "chg20", "vol_ratio", "up_streak"]]
    ax = pd.read_parquet(args.adx, columns=["date", "stock_id", "adx", "brk_up", "brk_dn"])
    ax["stock_id"] = ax["stock_id"].astype(str)
    ax["date"] = pd.to_datetime(ax["date"]).dt.strftime("%Y-%m-%d")
    bd = pd.read_parquet(args.band, columns=["date", "stock_id", "z"])
    bd["stock_id"] = bd["stock_id"].astype(str)
    ch = pd.read_parquet(args.chips, columns=["stock_id", "date", "liq_amt20"])
    ch["stock_id"] = ch["stock_id"].astype(str)
    day = day.merge(d1, on=["date", "stock_id"], how="left").merge(ax, on=["date", "stock_id"], how="left")
    day = day.merge(bd, on=["date", "stock_id"], how="left").merge(ch, on=["date", "stock_id"], how="left")
    day = day[day["liq_amt20"].fillna(0) >= 1].dropna(subset=["eff"])
    day["clean"] = (day["eff"] >= 0.5) & (day["crosses"] <= 2)
    base = day["clean"].mean()
    print(f"# 高波動池的「順／亂」（股票日 {len(day)}）\n")
    print(f"穿越 VWAP 次數（09:10 後，M5 收盤判定）：平均 {day['crosses'].mean():.1f}，中位數 {day['crosses'].median():.0f}；"
          f"≤2 次的比例 {(day['crosses'] <= 2).mean() * 100:.0f}%")
    print(f"路徑效率（|開→收| ÷ M5 來回總路程）：中位數 {day['eff'].median():.2f}；≥0.5 的比例 {(day['eff'] >= 0.5).mean() * 100:.0f}%")
    print(f"定義「順」= 效率 ≥0.5 且 穿越 ≤2 次：{base * 100:.0f}%；順的日子 開→收 平均 {day.loc[day['clean'], 'oc'].abs().mean():.2f}%（絕對值），"
          f"亂的 {day.loc[~day['clean'], 'oc'].abs().mean():.2f}%\n")
    print("| 條件 | 股票日 | 順的比例 | 穿越次數 平均 | 效率 中位數 | 開→收 平均 | 順的日子裡 開→收 平均 | 訓練 順% | 測試 順% | 測試半年順%高於整體 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    ovr = day.groupby(half(day["date"]))["clean"].mean()
    conds = [
        ("全部", day.index == day.index),
        ("開盤 5 分振幅 <3%（靠 ATR≥7 入池）", day["o5"] < 3), ("開盤 5 分振幅 3～5%", (day["o5"] >= 3) & (day["o5"] < 5)),
        ("開盤 5 分振幅 5～7%", (day["o5"] >= 5) & (day["o5"] < 7)), ("開盤 5 分振幅 ≥7%", day["o5"] >= 7),
        ("PR 50～80", day["pr"] < 80), ("PR ≥ 80", day["pr"] >= 80), ("PR 100", day["pr"] >= 100),
        ("ATR 5～7", day["day_atr"] < 7), ("ATR 7～10", (day["day_atr"] >= 7) & (day["day_atr"] < 10)), ("ATR ≥10", day["day_atr"] >= 10),
        ("開高 ≥3%", day["gap"] >= 3), ("開高 1～3%", (day["gap"] >= 1) & (day["gap"] < 3)), ("開平 ±1%", day["gap"].abs() < 1),
        ("開低 ≤−1%", day["gap"] <= -1), ("開低 ≤−3%", day["gap"] <= -3),
        ("昨天漲停", day["y_limit"] == True), ("昨天漲 ≥5%", day["y_chg"] >= 5), ("昨天跌 ≤−5%", day["y_chg"] <= -5),
        ("昨天振幅 ≥10%", day["y_rng"] >= 10), ("昨天振幅 <5%", day["y_rng"] < 5),
        ("昨天量 ≥3 倍", day["vol_ratio"] >= 3), ("昨天量 <1.5 倍", day["vol_ratio"] < 1.5),
        ("20 日漲 ≥30%", day["chg20"] >= 30), ("20 日跌 ≤−20%", day["chg20"] <= -20),
        ("昨收突破前 20 日高", day["brk_up"] == True), ("昨收跌破前 20 日低", day["brk_dn"] == True),
        ("昨收 > D1 +2σ", day["z"] > 2), ("昨收 < D1 −2σ", day["z"] < -2), ("昨收 ±1σ 內", day["z"].abs() < 1),
        ("ADX ≥45", day["adx"] >= 45), ("ADX <20", day["adx"] < 20), ("連漲 ≥3 天", day["up_streak"] >= 3),
        ("—— 09:30 可知 ——", day.index != day.index),
        ("09:30 比開盤高 ≥2%", day["e_chg"] >= 2), ("09:30 比開盤低 ≤−2%", day["e_chg"] <= -2), ("09:30 在開盤 ±1% 內", day["e_chg"].abs() < 1),
        ("09:10～09:30 沒穿越 VWAP", day["e_x"] == 0), ("09:10～09:30 穿越 ≥2 次", day["e_x"] >= 2),
        ("09:30 在 VWAP 上 ≥1%", day["e_vs"] >= 1), ("09:30 在 VWAP 下 ≤−1%", day["e_vs"] <= -1),
    ]
    for n, m in conds:
        s = day[m]
        if len(s) < 100:
            print(f"| {n} | {len(s)} | | | | | | | | |")
            continue
        a, b = s[s["date"] < SPLIT], s[s["date"] >= SPLIT]
        gh = b.groupby(half(b["date"]))["clean"].mean()
        better = (gh > ovr.reindex(gh.index)).sum()
        print(f"| {n} | {len(s)} | {s['clean'].mean() * 100:.0f}% | {s['crosses'].mean():.1f} | {s['eff'].median():.2f} | {s['oc'].mean():+.2f}% | "
              f"{s.loc[s['clean'], 'oc'].mean():+.2f}% | {a['clean'].mean() * 100:.0f}% | {b['clean'].mean() * 100:.0f}% | {better}/{len(gh)} |")
    day.to_parquet("/mnt/project-files/vwap-backtest/key-price/pool_days.parquet", index=False)


if __name__ == "__main__":
    main()
