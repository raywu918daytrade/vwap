"""Compare exit rules on the same short signals.

Input: a CSV of (date, stock_id, time) signals; entry is the next M1 open after
`time`. Each exit rule is simulated on raw M1 bars up to the 13:24 close. Price
stops/targets fill at their level (stop first when one bar hits both); rules
that react to a bar's close exit at the next bar's open.

Rules are chosen on 2023-2024 and checked on 2025-2026.

    python -m scripts.exit_study --signals scripts/exit_signals.csv --out db/exit_study.md
"""

from __future__ import annotations

import argparse
from collections import defaultdict

import numpy as np
import pandas as pd

from scripts.build_feature_table_long import load_m1_month

COST = 0.435
LAST_BAR = "13:24"


def short_exit(o, h, l, c, vwap, i, *, tp=None, sl=3.0, trail=None, be=None, vwap_exit=False, hold=None):
    """Net % for a short entered at bar i's open."""
    entry = o[i]
    stop = entry * (1 + sl / 100)
    target = entry * (1 - tp / 100) if tp else None
    low = entry
    for j in range(i, len(o)):
        if h[j] >= stop:
            return (entry - stop) / entry * 100 - COST
        if target is not None and l[j] <= target:
            return (entry - target) / entry * 100 - COST
        low = min(low, l[j])
        if trail is not None:
            stop = min(stop, low * (1 + trail / 100))
        if be is not None and low <= entry * (1 - be / 100):
            stop = min(stop, entry)
        nxt = j + 1 < len(o)
        if vwap_exit and nxt and c[j] > vwap[j]:
            return (entry - o[j + 1]) / entry * 100 - COST
        if hold is not None and nxt and j - i + 1 >= hold:
            return (entry - o[j + 1]) / entry * 100 - COST
    return (entry - c[-1]) / entry * 100 - COST


RULES: dict[str, dict] = {"基準 停利3.5/停損3": {"tp": 3.5, "sl": 3.0}}
for tp in (1.5, 2.0, 2.5, 5.0, None):
    for sl in (1.5, 2.0, 3.0, 4.0):
        RULES[f"停利{tp or '無'}/停損{sl:g}"] = {"tp": tp, "sl": sl}
RULES["停利3.5/停損2"] = {"tp": 3.5, "sl": 2.0}
for tr in (1.0, 1.5, 2.0, 3.0):
    RULES[f"移動停損{tr:g}%（初始停損3）"] = {"tp": None, "sl": 3.0, "trail": tr}
    RULES[f"移動停損{tr:g}% + 停利3.5"] = {"tp": 3.5, "sl": 3.0, "trail": tr}
for be in (1.0, 1.5, 2.0):
    RULES[f"賺{be:g}%後停損移到成本 + 停利3.5"] = {"tp": 3.5, "sl": 3.0, "be": be}
RULES["收盤站回VWAP出場（停損3）"] = {"tp": None, "sl": 3.0, "vwap_exit": True}
RULES["收盤站回VWAP出場 + 停利3.5"] = {"tp": 3.5, "sl": 3.0, "vwap_exit": True}
for hold in (15, 30, 60, 120):
    RULES[f"持有{hold}分鐘（停損3）"] = {"tp": None, "sl": 3.0, "hold": hold}
    RULES[f"持有{hold}分鐘 + 停利3.5"] = {"tp": 3.5, "sl": 3.0, "hold": hold}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    sig = pd.read_csv(args.signals, dtype={"stock_id": str, "date": str, "time": str})
    by_month = defaultdict(list)
    for r in sig.itertuples(index=False):
        by_month[r.date[:7].replace("-", "_")].append(r)

    rows = []
    for stem in sorted(by_month):
        m1 = load_m1_month(stem, set(s.stock_id for s in by_month[stem]))
        if m1.empty:
            print(f"{stem} 沒有 M1", flush=True)
            continue
        groups = {k: g for k, g in m1.groupby(["stock_id", "day"], sort=False)}
        for s in by_month[stem]:
            bars = groups.get((s.stock_id, s.date))
            if bars is None:
                continue
            bars = bars.sort_values("hhmm")
            o, h, l, c = (bars[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
            v = bars["volume"].astype(float).to_numpy()
            vwap = np.cumsum(c * v) / np.maximum(np.cumsum(v), 1e-9)
            hh = bars["hhmm"].to_numpy()
            idx = np.nonzero(hh > s.time)[0]
            if len(idx) == 0 or not o[idx[0]] > 0:
                continue
            i = int(idx[0])
            row = {"date": s.date, "stock_id": s.stock_id, "ref": s.short_net}
            for name, kw in RULES.items():
                row[name] = short_exit(o, h, l, c, vwap, i, **kw)
            rows.append(row)
        print(f"{stem} {len(by_month[stem])} 訊號", flush=True)

    df = pd.DataFrame(rows)
    df.to_parquet(args.out.replace(".md", ".parquet"), index=False)
    base = "基準 停利3.5/停損3"
    lines = [f"訊號 {len(sig)}，模擬 {len(df)}；基準與特徵表結果相差 > 0.01% 的筆數："
             f"{int((df[base] - df['ref']).abs().gt(0.01).sum())}\n",
             "| 出場方式 | 2023-24 筆數 | 勝率 | 平均 | 2025-26 筆數 | 勝率 | 平均 |", "|---|---|---|---|---|---|---|"]
    a, b = df[df.date < "2025-01-01"], df[df.date >= "2025-01-01"]
    order = sorted(RULES, key=lambda k: -a[k].mean())
    for k in order:
        lines.append(f"| {k} | {len(a)} | {(a[k] > 0).mean() * 100:.1f}% | {a[k].mean():+.3f}% "
                     f"| {len(b)} | {(b[k] > 0).mean() * 100:.1f}% | {b[k].mean():+.3f}% |")
    half = df.date.str[:4] + np.where(df.date.str[5:7] <= "06", "上", "下")
    lines += ["\n依半年（前 6 名 + 基準，平均 %）\n", "| 出場方式 | " + " | ".join(sorted(half.unique())) + " |",
              "|---|" + "---|" * half.nunique()]
    for k in [base] + [x for x in order[:6] if x != base]:
        lines.append(f"| {k} | " + " | ".join(f"{df[k][half == h].mean():+.3f}" for h in sorted(half.unique())) + " |")
    text = "\n".join(lines)
    print(text)
    open(args.out, "w").write(text + "\n")


if __name__ == "__main__":
    main()
