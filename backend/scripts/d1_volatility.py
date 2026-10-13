"""D1 study (Ray 2026-10-13: 先抓到會有波動的區間, direction later): which D1 conditions, known at yesterday's close,
make the next day's intraday range (high-low, % of prev close) clearly larger? Universe = the standard stock-days
(ATR14>=5, opening vol PR>=50, 20d turnover >= 1億).

    python -m scripts.d1_volatility --d1 d1_raw.parquet --adx d1_adx.parquet --band d1_vwap_band.parquet \
        --days stockdays.parquet --chips chips_daily.parquet --ft 'ft_20??.parquet'
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.d1_rebound_short import SPLIT


def feats(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("date").copy()
    pc = g["close"].shift(1)
    g["rng"] = (g["high"] - g["low"]) / pc * 100  # today's range, the target
    g["oc"] = (g["close"] / g["open"] - 1) * 100
    g["chg"] = (g["close"] / pc - 1) * 100
    # features as of yesterday
    g["y_rng"] = g["rng"].shift(1)
    g["y_chg"] = g["chg"].shift(1)
    g["rng5"] = g["rng"].rolling(5).mean().shift(1)
    g["rng20"] = g["rng"].rolling(20).mean().shift(1)
    g["vol_ratio"] = (g["volume"] / g["volume"].rolling(20).mean().shift(1)).shift(1)
    g["chg5"] = (g["close"].shift(1) / g["close"].shift(6) - 1) * 100
    g["chg20"] = (g["close"].shift(1) / g["close"].shift(21) - 1) * 100
    g["hi20"] = (g["close"].shift(1) / g["high"].shift(1).rolling(20).max() - 1) * 100
    g["lo20"] = (g["close"].shift(1) / g["low"].shift(1).rolling(20).min() - 1) * 100
    up = (g["chg"] > 0).astype(int)
    g["up_streak"] = up.groupby((up != up.shift()).cumsum()).cumsum().where(up == 1, 0).shift(1)
    dn = (g["chg"] < 0).astype(int)
    g["dn_streak"] = dn.groupby((dn != dn.shift()).cumsum()).cumsum().where(dn == 1, 0).shift(1)
    g["y_limit"] = (g["high"] / pc - 1 >= 0.095).shift(1)
    return g


def main() -> None:
    ap = argparse.ArgumentParser()
    for a in ("--d1", "--adx", "--band", "--days", "--chips", "--ft"):
        ap.add_argument(a, required=True)
    args = ap.parse_args()
    d1 = pd.read_parquet(args.d1)
    d1["stock_id"] = d1["stock_id"].astype(str)
    d1["date"] = pd.to_datetime(d1["date"]).dt.strftime("%Y-%m-%d")
    d = pd.concat([feats(g) for _, g in d1.groupby("stock_id")], ignore_index=True)
    ax = pd.read_parquet(args.adx)
    ax["stock_id"] = ax["stock_id"].astype(str)
    ax["date"] = pd.to_datetime(ax["date"]).dt.strftime("%Y-%m-%d")
    bd = pd.read_parquet(args.band, columns=["date", "stock_id", "z"])
    bd["stock_id"] = bd["stock_id"].astype(str)
    sd = pd.read_parquet(args.days)
    sd["stock_id"] = sd["stock_id"].astype(str)
    ch = pd.read_parquet(args.chips, columns=["stock_id", "date", "liq_amt20"])
    ch["stock_id"] = ch["stock_id"].astype(str)
    ft = pd.concat([pd.read_parquet(p, columns=["date", "stock_id", "day_atr", "vol5_pr", "d1_vs_ma20"]).drop_duplicates(["date", "stock_id"])
                    for p in sorted(glob.glob(args.ft))])
    ft["stock_id"] = ft["stock_id"].astype(str)
    d = sd.merge(d, on=["date", "stock_id"]).merge(ax, on=["date", "stock_id"], how="left").merge(bd, on=["date", "stock_id"], how="left")
    d = d.merge(ch, on=["date", "stock_id"], how="left").merge(ft, on=["date", "stock_id"], how="left")
    d = d[d["liq_amt20"].fillna(0) >= 1].dropna(subset=["rng", "rng20"])
    base = d["rng"].mean()
    print(f"股票日 {len(d)}；隔天振幅平均 {base:.2f}%，中位數 {d['rng'].median():.2f}%，≥5% 的比例 {(d['rng'] >= 5).mean() * 100:.0f}%\n")
    print("| D1 條件（昨收時已知） | 股票日 | 隔天振幅 平均 | 中位數 | 振幅≥5% | 振幅≥7% | 開→收 平均 | 碰漲停 | 訓練期振幅 | 測試期振幅 | 測試半年高於整體 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    ovr = d.groupby(half(d["date"]))["rng"].mean()
    conds = [
        ("全部", d.index == d.index),
        ("ATR 5～7%", d["day_atr"] < 7), ("ATR 7～10%", (d["day_atr"] >= 7) & (d["day_atr"] < 10)), ("ATR ≥ 10%", d["day_atr"] >= 10),
        ("昨天振幅 ≥ 10%", d["y_rng"] >= 10), ("昨天振幅 ≥ 7%", d["y_rng"] >= 7), ("昨天振幅 < 4%（壓縮）", d["y_rng"] < 4),
        ("近 5 日振幅 < 近 20 日 0.7 倍（壓縮）", d["rng5"] < 0.7 * d["rng20"]), ("近 5 日振幅 > 近 20 日 1.3 倍（擴張）", d["rng5"] > 1.3 * d["rng20"]),
        ("昨天漲停", d["y_limit"] == True), ("昨天漲 ≥ 5%", d["y_chg"] >= 5), ("昨天跌 ≤ −5%", d["y_chg"] <= -5), ("昨天 ±2% 內", d["y_chg"].abs() < 2),
        ("昨天量 ≥ 20 日均量 2 倍", d["vol_ratio"] >= 2), ("昨天量 ≥ 3 倍", d["vol_ratio"] >= 3), ("昨天量 < 均量", d["vol_ratio"] < 1),
        ("5 日漲 ≥ 15%", d["chg5"] >= 15), ("5 日跌 ≤ −15%", d["chg5"] <= -15), ("20 日漲 ≥ 30%", d["chg20"] >= 30), ("20 日跌 ≤ −20%", d["chg20"] <= -20),
        ("昨收突破前 20 日高", d["brk_up"] == True), ("昨收跌破前 20 日低", d["brk_dn"] == True),
        ("昨收離 20 日高 < 3%", d["hi20"] > -3), ("昨收離 20 日低 < 3%", d["lo20"] < 3), ("昨收在 20 日高低中間 1/3", (d["hi20"] < -8) & (d["lo20"] > 8)),
        ("ADX < 20", d["adx"] < 20), ("ADX 20～30", (d["adx"] >= 20) & (d["adx"] < 30)), ("ADX 30～45", (d["adx"] >= 30) & (d["adx"] < 45)), ("ADX ≥ 45", d["adx"] >= 45),
        ("ADX ≥ 25 多頭", (d["adx"] >= 25) & (d["pdi"] > d["mdi"])), ("ADX ≥ 25 空頭", (d["adx"] >= 25) & (d["mdi"] > d["pdi"])),
        ("昨收 > D1 VWAP20 +2σ", d["z"] > 2), ("昨收 < D1 VWAP20 −2σ", d["z"] < -2), ("昨收在 ±1σ 內", d["z"].abs() < 1),
        ("連漲 ≥ 3 天", d["up_streak"] >= 3), ("連跌 ≥ 3 天", d["dn_streak"] >= 3),
        ("昨收在 MA20 上 ≥ 20%", d["d1_vs_ma20"] >= 20), ("昨收在 MA20 下方", d["d1_vs_ma20"] < 0),
        ("組合：昨天振幅 ≥ 7% 且 量 ≥ 2 倍", (d["y_rng"] >= 7) & (d["vol_ratio"] >= 2)),
        ("組合：ATR ≥ 7% 且 昨天漲停", (d["day_atr"] >= 7) & (d["y_limit"] == True)),
        ("組合：ATR ≥ 7% 且 突破前 20 日高", (d["day_atr"] >= 7) & (d["brk_up"] == True)),
    ]
    for n, m in conds:
        s = d[m]
        if len(s) < 100:
            print(f"| {n} | {len(s)} | 太少 | | | | | | | | |")
            continue
        a, b = s[s["date"] < SPLIT], s[s["date"] >= SPLIT]
        g = b.groupby(half(b["date"]))["rng"].mean()
        better = (g > ovr.reindex(g.index)).sum()
        print(f"| {n} | {len(s)} | {s['rng'].mean():.2f}% | {s['rng'].median():.2f}% | {(s['rng'] >= 5).mean() * 100:.0f}% | "
              f"{(s['rng'] >= 7).mean() * 100:.0f}% | {s['oc'].mean():+.2f}% | {(s['high'] / s['close'].shift(0) * 0 + (s['rng'] * 0 + 1)).mean() * 0 + ((s['high'] / (s['close'] / (1 + s['chg'] / 100)) - 1) >= 0.095).mean() * 100:.0f}% | "
              f"{a['rng'].mean():.2f}% | {b['rng'].mean():.2f}% | {better}/{len(g)} |")


if __name__ == "__main__":
    main()
