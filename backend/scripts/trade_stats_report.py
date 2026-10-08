"""Re-run the 交易統計 panel strategies over a date range with other rules.

Uses the same candidate/simulation code as /api/trade_stats, on locally synced
HF data (run scripts.sync_market_db_from_hf first). Prints one table per
variant: strategy x side -> trades, win rate, average and total net %.

    python -m scripts.trade_stats_report --from-date 2026-09-01 --to-date 2026-10-07
"""

from __future__ import annotations

import argparse
from collections import defaultdict

import pandas as pd

import trade_stats_api as ts
from pattern.vwap_activity import metrics_for_date as activity_for_date
from pattern.vwap_signal_store import read_vwap_signals
from pattern.vwap_sr_scan import prev_close_for_date, stock_ids_for_universe

VARIANTS = {
    "原本 停利2/停損4": {},
    "停利3/停損3＋漲跌5%限制": {"take_profit_pct": 3.0, "stop_loss_pct": 3.0, "max_chase_pct": 5.0},
}


def _rows(strategy: str, bundle: dict, rules: dict) -> list[dict]:
    if strategy == "macd_vwap":
        return ts._attach_macd(bundle.get("vwap") or [], bundle.get("macd") or {}, rules["macd_max_gap_min"])
    if strategy == "vwap_cross":
        return bundle.get("vwap") or []
    return bundle.get("sr") or []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    args = parser.parse_args()

    stock_ids = stock_ids_for_universe("daytrade")
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(args.from_date, args.to_date)]
    results: dict[str, list[dict]] = defaultdict(list)
    for date_str in days:
        bundle = read_vwap_signals(date_str, stock_ids=stock_ids)
        activity = activity_for_date(date_str) or {}
        if not bundle or not activity:
            print(f"{date_str}: 沒有訊號或活動度資料，略過", flush=True)
            continue
        prev_close = prev_close_for_date(date_str)
        bars: dict[str, pd.DataFrame] = {}
        counts = []
        for strategy, base in ts.STRATEGIES.items():
            for variant, override in VARIANTS.items():
                rules = {**base, **override}
                cands = ts._candidates(_rows(strategy, bundle, rules), activity, strategy, rules, prev_close)
                for cand in cands:
                    sid = cand["stock_id"]
                    if sid not in bars:
                        bars[sid] = ts._day_bars(sid, date_str)
                    trade = ts._simulate(cand, bars[sid], True, rules)
                    if trade["net_pct"] is not None:
                        results[(variant, strategy)].append({**trade, "date": date_str})
                counts.append(len(cands))
        print(f"{date_str}: candidates {counts}", flush=True)

    for variant in VARIANTS:
        print(f"\n===== {variant} =====")
        print("| 策略 | 方向 | 筆數 | 勝率 | 平均 | 合計 | 停利/停損/收盤 |")
        print("|---|---|---|---|---|---|---|")
        for strategy, base in ts.STRATEGIES.items():
            trades = results[(variant, strategy)]
            for side in ("long", "short", "all"):
                sel = [t for t in trades if side == "all" or t["side"] == side]
                if not sel:
                    continue
                net = [t["net_pct"] for t in sel]
                exits = "/".join(str(sum(t["status"] == k for t in sel)) for k in ("停利", "停損", "收盤平倉"))
                label = {"long": "多", "short": "空", "all": "合計"}[side]
                print(f"| {base['label']} | {label} | {len(sel)} | {sum(x > 0 for x in net) / len(net) * 100:.1f}% "
                      f"| {sum(net) / len(net):+.3f}% | {sum(net):+.1f}% | {exits} |")


if __name__ == "__main__":
    main()
