"""Market and sector context per minute, from every stock in db/m1.

For each trading day and minute (09:00-13:24), across all stocks with M1 that day:
- mkt_chg_prev: median % change vs each stock's previous-day last M1 close
- mkt_chg_open: median % change vs each stock's first M1 open of the day
- mkt_m5_ret: median % change over the last 5 minutes
- mkt_breadth: share of stocks above their previous close
The same medians per industry (from db/tickers, when an industry column exists)
go to a second file, along with the stock -> industry map.

Bars only cover minutes in which a stock traded; prices are carried forward so
quiet stocks still count.

    python -m scripts.build_market_features --from-month 2019-12 --to-month 2026-10 --out-dir db/market_ctx
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
M1_DIR = ROOT / "db/m1"
INDUSTRY_COLS = ("industry", "industry_category", "sector", "產業別", "industry_name")


def industry_map() -> pd.Series:
    for f in sorted((ROOT / "db/tickers").glob("*.parquet")):
        t = pd.read_parquet(f)
        print(f"{f.name}: {list(t.columns)[:15]}  {len(t)} 列", flush=True)
        col = next((c for c in INDUSTRY_COLS if c in t.columns), None)
        sid = next((c for c in ("stock_id", "code", "symbol") if c in t.columns), None)
        if col and sid:
            m = t[[sid, col]].dropna().astype(str).drop_duplicates(sid).set_index(sid)[col]
            print(f"  產業欄位 {col}：{m.nunique()} 個產業，{len(m)} 檔", flush=True)
            return m
    return pd.Series(dtype=str)


def month_frame(stem: str) -> pd.DataFrame:
    path = M1_DIR / f"{stem}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pq.read_table(path, columns=["stock_id", "date", "open", "close"]).to_pandas()
    df["stock_id"] = df["stock_id"].astype(str)
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    if getattr(df["date"].dt, "tz", None) is not None:
        df["date"] = df["date"].dt.tz_localize(None)
    df = df[(df["close"] > 0) & (df["open"] > 0)]
    df["day"] = df["date"].dt.strftime("%Y-%m-%d")
    df["hhmm"] = df["date"].dt.strftime("%H:%M")
    df = df[(df["hhmm"] >= "09:00") & (df["hhmm"] <= "13:24")]
    return df.drop_duplicates(["stock_id", "date"], keep="last")


def day_panel(g: pd.DataFrame, prev_close: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Wide (minute x stock) close, % vs prev close, % vs open, 5-minute return."""
    close = g.pivot(index="hhmm", columns="stock_id", values="close").sort_index().ffill()
    first_open = g.sort_values("hhmm").groupby("stock_id")["open"].first()
    pc = prev_close.reindex(close.columns)
    chg_prev = (close / pc - 1) * 100
    chg_open = (close / first_open.reindex(close.columns) - 1) * 100
    ret5 = (close / close.shift(5) - 1) * 100
    return chg_prev, chg_open, ret5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-month", required=True)
    parser.add_argument("--to-month", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    ind = industry_map()
    ind.rename("industry").rename_axis("stock_id").reset_index().to_parquet(out / "industry_map.parquet", index=False)

    mkt_rows, sec_parts = [], []
    prev_close = pd.Series(dtype=float)
    for stem in [p.strftime("%Y_%m") for p in pd.period_range(args.from_month, args.to_month, freq="M")]:
        df = month_frame(stem)
        if df.empty:
            print(f"{stem} 沒有 M1", flush=True)
            continue
        for day, g in df.groupby("day", sort=True):
            chg_prev, chg_open, ret5 = day_panel(g, prev_close)
            n = chg_prev.notna().sum(axis=1)
            mkt = pd.DataFrame({
                "date": day, "time": chg_prev.index,
                "mkt_chg_prev": chg_prev.median(axis=1).to_numpy(),
                "mkt_chg_open": chg_open.median(axis=1).to_numpy(),
                "mkt_m5_ret": ret5.median(axis=1).to_numpy(),
                "mkt_breadth": ((chg_prev > 0).sum(axis=1) / n.replace(0, np.nan)).to_numpy(),
                "mkt_n": n.to_numpy(),
            })
            mkt_rows.append(mkt)
            if not ind.empty:
                cols = [c for c in chg_prev.columns if c in ind.index]
                labels = ind.reindex(cols)
                sp = chg_prev[cols].T.groupby(labels.to_numpy()).median().T
                so = chg_open[cols].T.groupby(labels.to_numpy()).median().T
                s = sp.stack().rename("sec_chg_prev").to_frame().join(so.stack().rename("sec_chg_open"))
                s = s.reset_index().rename(columns={"hhmm": "time", "level_1": "industry"})
                s.columns = ["time", "industry", "sec_chg_prev", "sec_chg_open"]
                s.insert(0, "date", day)
                s = s[s["time"].str[-1].isin(["4", "9"])]
                sec_parts.append(s.astype({"sec_chg_prev": "float32", "sec_chg_open": "float32"}))
            last = g.sort_values("hhmm").groupby("stock_id")["close"].last()
            prev_close = pd.concat([prev_close[~prev_close.index.isin(last.index)], last])
        print(f"{stem} 完成，{df['day'].nunique()} 天，約 {df['stock_id'].nunique()} 檔", flush=True)

    m = pd.concat(mkt_rows, ignore_index=True)
    m.to_parquet(out / "market_minute.parquet", index=False, compression="zstd")
    print(f"market_minute: {len(m)} 列")
    if sec_parts:
        s = pd.concat(sec_parts, ignore_index=True)
        s.to_parquet(out / "sector_minute.parquet", index=False, compression="zstd")
        print(f"sector_minute: {len(s)} 列")


if __name__ == "__main__":
    main()
