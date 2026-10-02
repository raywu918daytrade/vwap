"""Backtest the offline VWAP dashboard signals against M1 bars.

Reads the precomputed signal shards (db/vwap_signals/YYYY_MM.parquet) and the
raw M1 bars (db/m1/YYYY_MM.parquet), simulates one day-trade per signal and
prints win-rate tables per signal type.

Trade rules (defaults, all overridable by flags):
- Entry: open of the bar after the signal bar (M1 bars are labeled by start
  time, so the signal bar's close is only known one minute later).
- Side: VWAP up / divergence bull = long; VWAP down / divergence bear = short;
  SR events follow their vwap_dir.
- Exits: fixed holds of 5 / 15 / 30 minutes, end of day (close of the last
  continuous-session bar, 13:24), and a +TP% / -SL% bracket that falls back to
  end of day. When TP and SL are both touched inside one bar, SL is assumed.
- Win: net return > 0 after round-trip cost (commission both sides + day-trade
  tax), default 0.1425% * 2 + 0.15% = 0.435%.

    python -m scripts.backtest_signals --out-dir db/backtest
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_ROOT = Path(__file__).parent.parent
SIGNAL_DIR = _ROOT / "db/vwap_signals"
M1_DIR = _ROOT / "db/m1"
TICK_UNIVERSE = _ROOT / "db/tickers/tick_universe.parquet"

HOLDS = (5, 15, 30)
LAST_BAR = "13:24"
TIME_BUCKETS = [
    ("09:00", "09:00-09:14"),
    ("09:15", "09:15-09:59"),
    ("10:00", "10:00-10:59"),
    ("11:00", "11:00-11:59"),
    ("12:00", "12:00-13:10"),
]


def _time_bucket(hhmm: str) -> str:
    label = TIME_BUCKETS[0][1]
    for start, name in TIME_BUCKETS:
        if hhmm >= start:
            label = name
    return label


def _nth_bucket(n: int) -> str:
    if n <= 1:
        return "第1次"
    if n <= 3:
        return "第2-3次"
    return "第4次以上"


def _daytrade_ids() -> set[str]:
    try:
        df = pd.read_parquet(TICK_UNIVERSE)
    except Exception:
        return set()
    if "daytrade_ok" in df.columns and df["daytrade_ok"].fillna(False).astype(bool).any():
        df = df[df["daytrade_ok"].fillna(False).astype(bool)]
    return set(df["stock_id"].astype(str))


def load_events(from_date: str | None, to_date: str | None) -> pd.DataFrame:
    """Flatten all signal shards into one row per tradable event."""
    rows: list[dict] = []
    for path in sorted(SIGNAL_DIR.glob("*.parquet")):
        df = pd.read_parquet(path, columns=["scan_date", "kind", "stock_id", "time", "payload_json"])
        df = df[df["kind"].isin(["vwap", "sr", "macd", "obv"])]
        df["scan_date"] = df["scan_date"].astype(str).str[:10]
        if from_date:
            df = df[df["scan_date"] >= from_date]
        if to_date:
            df = df[df["scan_date"] <= to_date]
        for rec in df.itertuples(index=False):
            try:
                payload = json.loads(rec.payload_json or "{}")
            except json.JSONDecodeError:
                continue
            sid = str(payload.get("stock_id") or rec.stock_id)
            date = rec.scan_date
            if rec.kind == "vwap":
                up = payload.get("direction") == "up"
                rows.append({
                    "date": date, "stock_id": sid, "family": "VWAP",
                    "signal": "VWAP 上穿（做多）" if up else "VWAP 下穿（做空）",
                    "side": 1 if up else -1, "time": str(payload.get("time") or rec.time),
                })
            elif rec.kind == "sr":
                up = payload.get("vwap_dir") == "up"
                kind = {"resistance": "壓力", "support": "支撐", "both": "壓力+支撐"}.get(
                    payload.get("sr_kind"), str(payload.get("sr_kind"))
                )
                rows.append({
                    "date": date, "stock_id": sid, "family": "SR",
                    "signal": f"觸{kind}＋VWAP{'上' if up else '下'}（{'做多' if up else '做空'}）",
                    "side": 1 if up else -1, "time": str(payload.get("time") or rec.time),
                })
            else:
                name = "MACD 柱背離" if rec.kind == "macd" else "OBV 背離"
                for ev in payload.get("events") or []:
                    bull = ev.get("kind") == "bull"
                    rows.append({
                        "date": date, "stock_id": sid, "family": name.split()[0],
                        "signal": f"{name}{'多' if bull else '空'}（{'做多' if bull else '做空'}）",
                        "side": 1 if bull else -1, "time": str(ev.get("time") or ""),
                    })
    events = pd.DataFrame(rows, columns=["date", "stock_id", "family", "signal", "side", "time"])
    if events.empty:
        return events
    events = events[events["time"].str.len() == 5]
    events = events.drop_duplicates().sort_values(["date", "stock_id", "time"]).reset_index(drop=True)
    vwap = events["family"] == "VWAP"
    events.loc[vwap, "nth"] = events[vwap].groupby(["date", "stock_id"]).cumcount() + 1
    return events


def load_day_m1(date: str, stock_ids: set[str]) -> pd.DataFrame:
    path = M1_DIR / f"{date[:7].replace('-', '_')}.parquet"
    if not path.exists():
        return pd.DataFrame()
    dataset = ds.dataset(str(path), format="parquet")
    start = pd.Timestamp(date)
    end = start + pd.Timedelta(days=1)
    date_type = dataset.schema.field("date").type
    if pa.types.is_string(date_type) or pa.types.is_large_string(date_type):
        filt = (ds.field("date") >= f"{date} 00:00:00") & (ds.field("date") < end.strftime("%Y-%m-%d 00:00:00"))
    else:
        filt = (ds.field("date") >= pa.scalar(start.to_pydatetime())) & (ds.field("date") < pa.scalar(end.to_pydatetime()))
    table = dataset.to_table(filter=filt, columns=["stock_id", "date", "open", "high", "low", "close"])
    df = table.to_pandas()
    if df.empty:
        return df
    df["stock_id"] = df["stock_id"].astype(str)
    df = df[df["stock_id"].isin(stock_ids)]
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    if getattr(df["date"].dt, "tz", None) is not None:
        df["date"] = df["date"].dt.tz_localize(None)
    df["hhmm"] = df["date"].dt.strftime("%H:%M")
    df = df[df["hhmm"] <= LAST_BAR]
    return df.drop_duplicates(["stock_id", "date"], keep="last").sort_values(["stock_id", "date"])


def _trade(arrays, time: str, side: int, tp: float, sl: float, last_entry: str) -> dict | None:
    """One simulated trade on a stock's day bars; None when it cannot be entered."""
    hhmm, o, h, l, c = arrays
    if time > last_entry:
        return None
    e = int(np.searchsorted(hhmm, time, side="right"))
    if e >= len(hhmm):
        return None
    entry = o[e]
    if not np.isfinite(entry) or entry <= 0:
        return None
    last = len(c) - 1
    scale = side * 100.0 / entry
    row = {f"ret_{hold}m": (c[min(e + hold - 1, last)] - entry) * scale for hold in HOLDS}
    row["ret_eod"] = (c[last] - entry) * scale
    if side > 0:
        hit_tp = h[e:] >= entry * (1 + tp / 100)
        hit_sl = l[e:] <= entry * (1 - sl / 100)
    else:
        hit_tp = l[e:] <= entry * (1 - tp / 100)
        hit_sl = h[e:] >= entry * (1 + sl / 100)
    first_tp = int(np.argmax(hit_tp)) if hit_tp.any() else len(hit_tp)
    first_sl = int(np.argmax(hit_sl)) if hit_sl.any() else len(hit_sl)
    if first_sl <= first_tp and first_sl < len(hit_sl):
        row["ret_bracket"] = -sl
    elif first_tp < len(hit_tp):
        row["ret_bracket"] = tp
    else:
        row["ret_bracket"] = row["ret_eod"]
    row["entry_time"] = hhmm[e]
    return row


def simulate(events: pd.DataFrame, tp: float, sl: float, last_entry: str) -> pd.DataFrame:
    """Return events with gross % returns for each exit rule (NaN when untradable)."""
    cols = [f"ret_{h}m" for h in HOLDS] + ["ret_eod", "ret_bracket"]
    out = events.copy()
    for col in cols:
        out[col] = np.nan
    out["entry_time"] = ""
    for date, day_events in events.groupby("date", sort=True):
        m1 = load_day_m1(date, set(day_events["stock_id"]))
        if m1.empty:
            print(f"{date}: 缺 M1，略過 {len(day_events)} 筆", flush=True)
            continue
        bars = {
            sid: (
                g["hhmm"].to_numpy(),
                g["open"].to_numpy(dtype=float),
                g["high"].to_numpy(dtype=float),
                g["low"].to_numpy(dtype=float),
                g["close"].to_numpy(dtype=float),
            )
            for sid, g in m1.groupby("stock_id", sort=False)
        }
        results: dict[str, list] = {col: [] for col in cols + ["entry_time"]}
        for sid, time, side in zip(day_events["stock_id"], day_events["time"], day_events["side"]):
            arrays = bars.get(sid)
            row = _trade(arrays, time, int(side), tp, sl, last_entry) if arrays else None
            for col in results:
                results[col].append(row[col] if row else (np.nan if col != "entry_time" else ""))
        for col, values in results.items():
            out.loc[day_events.index, col] = values
        done = out.loc[day_events.index, "ret_eod"].notna().sum()
        print(f"{date}: {done}/{len(day_events)} 筆可交易", flush=True)
    return out


def _wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def summarize(trades: pd.DataFrame, keys: list[str], col: str, cost: float, min_n: int) -> pd.DataFrame:
    rows = []
    for key, g in trades.dropna(subset=[col]).groupby(keys, sort=True):
        net = g[col].to_numpy() - cost
        n = len(net)
        if n < min_n:
            continue
        wins = int((net > 0).sum())
        lo, hi = _wilson(wins, n)
        gains, losses = net[net > 0].sum(), -net[net <= 0].sum()
        key = key if isinstance(key, tuple) else (key,)
        rows.append({
            **dict(zip(keys, key)),
            "筆數": n,
            "毛勝率%": round(float((g[col] > 0).mean() * 100), 1),
            "淨勝率%": round(wins / n * 100, 1),
            "95%CI": f"{lo * 100:.1f}-{hi * 100:.1f}",
            "平均淨報酬%": round(float(net.mean()), 3),
            "中位數淨報酬%": round(float(np.median(net)), 3),
            "獲利因子": round(float(gains / losses), 2) if losses > 0 else math.inf,
        })
    return pd.DataFrame(rows)


def _md(df: pd.DataFrame) -> str:
    if df.empty:
        return "_無資料_\n"
    head = "| " + " | ".join(df.columns) + " |\n|" + "---|" * len(df.columns) + "\n"
    body = "".join("| " + " | ".join(str(v) for v in row) + " |\n" for row in df.itertuples(index=False))
    return head + body


def build_report(trades: pd.DataFrame, cost: float, tp: float, sl: float, min_n: int) -> str:
    tradable = trades.dropna(subset=["ret_eod"])
    dates = sorted(tradable["date"].unique())
    lines = [
        "# 盤勢雷達訊號回測",
        "",
        f"- 期間：{dates[0] if dates else '-'} ~ {dates[-1] if dates else '-'}（{len(dates)} 個交易日），可交易訊號 {len(tradable)} 筆",
        f"- 進場：訊號K的下一根開盤；出場：持有 N 分鐘收盤、或 13:24 收盤；停利停損 +{tp}% / -{sl}%（同根都碰到算停損）",
        f"- 淨報酬扣來回成本 {cost}%；勝率＝淨報酬 > 0",
        "",
    ]
    exits = [(f"ret_{h}m", f"持有 {h} 分鐘") for h in HOLDS] + [("ret_eod", "抱到 13:24"), ("ret_bracket", f"停利 {tp}% / 停損 {sl}%")]
    for universe in ("當沖股池", "全部"):
        subset = tradable[tradable["daytrade"]] if universe == "當沖股池" else tradable
        lines.append(f"## {universe}（{len(subset)} 筆）")
        lines.append("")
        for col, label in exits:
            lines += [f"### 出場：{label}", "", _md(summarize(subset, ["signal"], col, cost, min_n)), ""]
    pool = tradable[tradable["daytrade"]]
    lines += ["## 當沖股池：依訊號時段（停利停損出場）", "", _md(summarize(pool, ["signal", "時段"], "ret_bracket", cost, min_n)), ""]
    vwap = pool[pool["family"] == "VWAP"]
    lines += ["## 當沖股池：VWAP 當日第幾次穿越（停利停損出場）", "", _md(summarize(vwap, ["signal", "第幾次"], "ret_bracket", cost, min_n)), ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest offline VWAP dashboard signals on M1 bars")
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--cost", type=float, default=0.435, help="來回成本 %%，預設手續費 0.1425%%x2 + 當沖稅 0.15%%")
    parser.add_argument("--tp", type=float, default=1.0, help="停利 %%")
    parser.add_argument("--sl", type=float, default=1.0, help="停損 %%")
    parser.add_argument("--last-entry", default="13:10", help="晚於此時間的訊號不進場")
    parser.add_argument("--min-n", type=int, default=30, help="樣本少於此數的分組不列出")
    parser.add_argument("--out-dir", default="db/backtest")
    args = parser.parse_args()

    events = load_events(args.from_date, args.to_date)
    if events.empty:
        raise RuntimeError("db/vwap_signals 沒有訊號，請先同步 HF")
    print(f"讀到 {len(events)} 筆訊號，{events['date'].nunique()} 個交易日", flush=True)
    trades = simulate(events, args.tp, args.sl, args.last_entry)
    daytrade = _daytrade_ids()
    trades["daytrade"] = trades["stock_id"].isin(daytrade) if daytrade else True
    trades["時段"] = trades["time"].map(_time_bucket)
    trades["第幾次"] = trades["nth"].map(lambda n: _nth_bucket(int(n)) if pd.notna(n) else "")

    out_dir = _ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out_dir / "trades.csv.gz", index=False)
    report = build_report(trades, args.cost, args.tp, args.sl, args.min_n)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    print(report, flush=True)
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")


if __name__ == "__main__":
    main()
