"""H1 盤整區 + M5 反轉, under the 2026-10-12 backtest standard.

Boxes from scripts.h1_box_zones (resistance above after a breakdown / support below after a breakout).
Short: an M5 opens below the box bottom, its high reaches the box bottom, and it closes black below it
-> short at the next M1 open. Long: opens above the box top, low reaches the top, closes red above it.
Standard rules as in scripts.key_price_study (09:10-12:00, ≤ +5% / ≥ -5%, no limit touched, cost +
slippage, first signal per stock-day, one at a time).

    python -m scripts.box_study --tables 'ft_*.parquet' --m5 'm5_hl_*.parquet' --boxes h1_boxes.parquet --chips chips_daily.parquet
"""

import argparse

import pandas as pd

from scripts.chips_model import first_per_day
from scripts.key_price_study import load, row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--m5", required=True)
    ap.add_argument("--boxes", required=True)
    ap.add_argument("--chips", required=True)
    ap.add_argument("--picks", default=None, help="write sample signals (test period) for charts")
    ap.add_argument("--setups", default=None, help="write every stock-day's first signal (for scripts.vwap_after_setup)")
    args = ap.parse_args()
    df = load(args)
    bx = pd.read_parquet(args.boxes)
    bx["stock_id"] = bx["stock_id"].astype(str)
    sd = df[["date", "stock_id"]].drop_duplicates()
    print(f"M5 點 {len(df)}，股票日 {len(sd)}")
    for side in ("short", "long"):
        k = bx[bx["side"] == side].merge(sd, on=["date", "stock_id"])
        print(f"- {'壓力（做空）' if side == 'short' else '支撐（做多）'}盤整區：{len(k)} 個股票日（{len(k) / len(sd) * 100:.0f}%），"
              f"離昨收中位數 {(k['lo_pct'] if side == 'short' else k['hi_pct']).median():+.1f}%")
    print()
    head = ("| 條件 | 訓練 筆數 | 勝率 | 停利3.5 平均 | 測試 筆數 | 勝率 | 停利3.5 平均 | 測試 收盤 平均 | 測試 正的半年 | "
            "測試一次一筆 筆數 | 平均(停利3.5) | 回落 | 最多連輸 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    picks, allsig = [], []
    for side, net, hold in (("short", "short_net", "short_hold"), ("long", "long_net", "long_hold")):
        if side == "short":
            ok = df[(df["chg_from_prev_close"] <= 5) & (df["hi_prev"] < 9.5)]
        else:
            ok = df[(df["chg_from_prev_close"] >= -5) & (df["lo_prev"] > -9.5)]
        x = ok.merge(bx[bx["side"] == side][["date", "stock_id", "lo_pct", "hi_pct", "n_bars"]], on=["date", "stock_id"])
        o, h, l, c = x["m5_o_pct"], x["m5_h_pct"], x["m5_l_pct"], x["m5_c_pct"]
        if side == "short":
            sig = x[(o < x["lo_pct"]) & (h >= x["lo_pct"]) & (c < x["lo_pct"]) & (c < o)]
        else:
            sig = x[(o > x["hi_pct"]) & (l <= x["hi_pct"]) & (c > x["hi_pct"]) & (c > o)]
        print(f"# {'做空：反彈碰到上方盤整區，M5 收黑' if side == 'short' else '做多：回檔碰到下方盤整區，M5 收紅'}（09:10-12:00，扣成本＋滑價 0.535%）\n")
        print(head)
        print(row("對照：隨便進場", ok, net, hold))
        print(row("有盤整區的股票日，隨便進場", x, net, hold))
        print(row("碰到盤整區 + M5 反轉", sig, net, hold))
        print(row("　盤整 ≥12 根", sig[sig["n_bars"] >= 12], net, hold))
        print()
        f = first_per_day(sig)
        allsig.append(f[["date", "stock_id", "time", "lo_pct", "hi_pct"]].assign(side=side))
        picks.append(f[f["date"] >= "2023-01-01"].sample(min(5, len(f)), random_state=2).assign(side=side))
    if args.setups:
        pd.concat(allsig).to_csv(args.setups, index=False)
    if args.picks:
        pd.concat(picks).to_csv(args.picks, index=False)


if __name__ == "__main__":
    main()
