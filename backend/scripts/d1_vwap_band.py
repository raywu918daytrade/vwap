"""D1 VWAP20 ± 2σ: does the band on the daily chart say anything about today's open-to-close move?

VWAP20 = volume-weighted mean of the typical price (H+L+C)/3 over the 20 days before today;
σ = volume-weighted std of the typical price around it. z = (yesterday's close − VWAP20) / σ, known
before the open. Outcome = today's open → close % (a short gains its negative), on the stock-days of
the standard filter (ATR ≥ 5, PR ≥ 50). Also the touch version: today's high reaches the upper band
(short from the band to the close) / low reaches the lower band (long).

    python -m scripts.d1_vwap_band --d1 d1_raw.parquet --days stockdays.parquet --out d1_vwap_band.parquet
"""

import argparse

import numpy as np
import pandas as pd

N = 20


def bands(g: pd.DataFrame) -> pd.DataFrame:
    tp = (g["high"] + g["low"] + g["close"]) / 3
    v = g["volume"].clip(lower=0)
    sv = v.rolling(N).sum()
    vw = (tp * v).rolling(N).sum() / sv
    var = (v * tp ** 2).rolling(N).sum() / sv - vw ** 2
    sd = np.sqrt(var.clip(lower=0))
    out = pd.DataFrame({"vwap20": vw.shift(1), "sd20": sd.shift(1)}, index=g.index)
    out["up2"], out["dn2"] = out["vwap20"] + 2 * out["sd20"], out["vwap20"] - 2 * out["sd20"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--d1", required=True)
    ap.add_argument("--days", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    d = pd.read_parquet(args.d1)
    d["stock_id"] = d["stock_id"].astype(str)
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d = d[(d["close"] > 0) & (d["volume"] > 0)].sort_values(["stock_id", "date"]).reset_index(drop=True)
    d = pd.concat([d, d.groupby("stock_id", group_keys=False).apply(bands)], axis=1)
    d["prev_close"] = d.groupby("stock_id")["close"].shift(1)
    d["z"] = (d["prev_close"] - d["vwap20"]) / d["sd20"]
    d["oc"] = (d["close"] / d["open"] - 1) * 100
    d["gap"] = (d["open"] / d["prev_close"] - 1) * 100
    want = pd.read_parquet(args.days)
    want["stock_id"] = want["stock_id"].astype(str)
    x = d.merge(want, on=["date", "stock_id"]).dropna(subset=["z"])
    x.to_parquet(args.out, index=False)
    cost = 0.535
    print(f"股票日 {len(x)}（ATR≥5、PR≥50）；z = (昨收 − VWAP20) / σ\n")
    print("## 昨收在 D1 VWAP 通道的位置 → 今天開盤到收盤\n")
    print("| 昨收位置 | 股票日 | 今天開→收 平均 | 收黑比例 | 做空扣成本 平均 | 做多扣成本 平均 |\n|---|---|---|---|---|---|")
    cuts = [(-99, -2, "< −2σ"), (-2, -1, "−2σ ~ −1σ"), (-1, 0, "−1σ ~ 0"), (0, 1, "0 ~ +1σ"), (1, 2, "+1σ ~ +2σ"), (2, 99, "> +2σ")]
    for a, b, name in cuts:
        s = x[(x["z"] >= a) & (x["z"] < b)]
        print(f"| {name} | {len(s)} | {s['oc'].mean():+.2f}% | {(s['oc'] < 0).mean() * 100:.0f}% | "
              f"{(-s['oc'] - cost).mean():+.2f}% | {(s['oc'] - cost).mean():+.2f}% |")
    print("\n## 今天盤中碰到通道（從通道價進場、收盤出場；未含停損）\n")
    print("| 條件 | 股票日 | 平均 | 勝率 | 2020-22 | 2023-26 |\n|---|---|---|---|---|---|")
    up = x[(x["open"] < x["up2"]) & (x["high"] >= x["up2"]) & ((x["up2"] / x["prev_close"] - 1) * 100 <= 5)]
    r = (1 - up["close"] / up["up2"]) * 100 - cost
    dn = x[(x["open"] > x["dn2"]) & (x["low"] <= x["dn2"]) & ((x["dn2"] / x["prev_close"] - 1) * 100 >= -5)]
    q = (dn["close"] / dn["dn2"] - 1) * 100 - cost
    for name, s, rr in (("碰到 +2σ 做空", up, r), ("碰到 −2σ 做多", dn, q)):
        tr, te = rr[s["date"] < "2023-01-01"], rr[s["date"] >= "2023-01-01"]
        print(f"| {name} | {len(s)} | {rr.mean():+.2f}% | {(rr > 0).mean() * 100:.0f}% | {tr.mean():+.2f}% ({len(tr)}) | {te.mean():+.2f}% ({len(te)}) |")


if __name__ == "__main__":
    main()
