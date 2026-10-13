"""Per stock-day D1 context for filters (Ray 2026-10-13): Wilder ADX14 / +DI / -DI as of yesterday, and whether
yesterday's close broke above the prior 20-day high or below the prior 20-day low.

    python -m scripts.d1_adx --d1 d1_raw.parquet --out d1_adx.parquet
"""

import argparse

import numpy as np
import pandas as pd

N = 14
RNG = 20


def adx(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("date")
    h, l, c = g["high"].to_numpy(float), g["low"].to_numpy(float), g["close"].to_numpy(float)
    pc = np.roll(c, 1)
    pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    up, dn = h - np.roll(h, 1), np.roll(l, 1) - l
    up[0] = dn[0] = 0
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    w = lambda x: pd.Series(x).ewm(alpha=1 / N, adjust=False, min_periods=N).mean().to_numpy()
    atr = w(tr)
    pdi, mdi = 100 * w(pdm) / atr, 100 * w(mdm) / atr
    dx = 100 * abs(pdi - mdi) / (pdi + mdi)
    out = pd.DataFrame({"date": g["date"].to_numpy(), "stock_id": g["stock_id"].to_numpy(), "adx": w(dx), "pdi": pdi, "mdi": mdi})
    hh = pd.Series(h).shift(1).rolling(RNG).max().to_numpy()
    ll = pd.Series(l).shift(1).rolling(RNG).min().to_numpy()
    out["brk_up"] = c > hh
    out["brk_dn"] = c < ll
    # values are "as of yesterday" for the next trading day
    for col in ("adx", "pdi", "mdi", "brk_up", "brk_dn"):
        out[col] = out[col].shift(1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--d1", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    d1 = pd.read_parquet(args.d1)
    d1["stock_id"] = d1["stock_id"].astype(str)
    out = pd.concat([adx(g) for _, g in d1.groupby("stock_id")], ignore_index=True).dropna(subset=["adx"])
    out.to_parquet(args.out, index=False)
    print(len(out), out["adx"].describe().round(1).to_dict(), "brk_up", out["brk_up"].mean().round(3), "brk_dn", out["brk_dn"].mean().round(3))


if __name__ == "__main__":
    main()
