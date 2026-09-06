"""Background cache warmup for the dashboard's common historical reads."""

from __future__ import annotations

from time import perf_counter, sleep


def _recent_offline_dates(month_limit: int) -> list[str]:
    from pattern.activity_store import available_activity_dates
    from pattern.offline_store import available_scan_dates
    from pattern.vwap_signal_store import available_signal_dates

    if month_limit <= 0:
        return []

    dates = sorted(
        set(available_signal_dates())
        | set(available_activity_dates())
        | set(available_scan_dates())
    )
    if not dates:
        return []

    import pandas as pd

    latest = pd.Timestamp(dates[-1])
    start = (latest - pd.DateOffset(months=month_limit)).strftime("%Y-%m-%d")
    return list(reversed([date for date in dates if date >= start]))


def prewarm_historical_caches(
    *,
    month_limit: int,
    chart_month_limit: int,
    chart_date_limit: int,
    chart_rows: int,
    chart_stocks: list[str],
    activity_filters: dict[str, float],
    chart_pause_sec: float = 0.0,
    universe: str = "daytrade",
) -> dict[str, int]:
    """Warm recent list and default chart caches after HF sync.

    This intentionally runs in a background thread. The API can answer requests
    while Oracle's filesystem cache and Python in-memory caches are being
    primed for the dates users are most likely to open first.
    """
    if month_limit <= 0:
        return {"dates": 0, "charts": 0, "errors": 0}

    from pattern.pattern_api import get_pattern_detail, scan_patterns
    from pattern.vwap_activity import metrics_for_date
    from pattern.vwap_signal_store import read_vwap_signals
    from pattern.vwap_sr_scan import stock_ids_for_universe

    dates = _recent_offline_dates(month_limit)
    chart_dates = _chart_dates(dates, chart_month_limit, chart_date_limit)
    stock_ids = stock_ids_for_universe(universe)
    fixed_chart_stocks = list(dict.fromkeys(str(sid) for sid in chart_stocks if str(sid).strip()))
    charts = 0
    errors = 0
    started = perf_counter()

    print(
        f"[快取預熱] 開始：清單最近 {month_limit} 個月 / {len(dates)} 個日期；"
        f"圖表最近 {chart_month_limit} 個月 / {len(chart_dates)} 個日期；"
        f"每日預設條件最多 {chart_rows} 檔；由最新日期往前預熱",
        flush=True,
    )
    for date in dates:
        t0 = perf_counter()
        try:
            read_vwap_signals(date, stock_ids=stock_ids)
            metrics_for_date(date, universe=universe)
            scan_patterns(pattern_type="all", timeframe="day", date=date, min_score=60.0, limit=120)
            print(
                f"  [快取預熱] {date} 清單完成 ({perf_counter() - t0:.1f}s)",
                flush=True,
            )
        except Exception as exc:
            errors += 1
            print(f"  [快取預熱] {date} 清單失敗: {exc}", flush=True)

    if chart_dates:
        print("[快取預熱] 清單階段完成，開始分批預熱 K 圖 detail", flush=True)
    for date in chart_dates:
        t0 = perf_counter()
        try:
            bundle = read_vwap_signals(date, stock_ids=stock_ids) or {}
            activity = metrics_for_date(date, universe=universe)
            warm_stocks = _chart_stocks_for_date(
                bundle,
                activity,
                fixed_chart_stocks,
                activity_filters,
                chart_rows,
            )
            for stock_id in warm_stocks:
                try:
                    get_pattern_detail(
                        stock_id,
                        pattern_type="none",
                        timeframe="day",
                        date=date,
                        limit=120,
                        full_day=False,
                        force_live=False,
                    )
                    get_pattern_detail(
                        stock_id,
                        pattern_type="none",
                        timeframe="1m",
                        date=date,
                        limit=120,
                        full_day=True,
                        force_live=False,
                    )
                    charts += 2
                except Exception as exc:
                    errors += 1
                    print(f"  [快取預熱] {date} {stock_id} 圖表略過: {exc}", flush=True)
                if chart_pause_sec > 0:
                    sleep(chart_pause_sec)
            print(
                f"  [快取預熱] {date} 圖表完成：{len(warm_stocks)} 檔 ({perf_counter() - t0:.1f}s)",
                flush=True,
            )
        except Exception as exc:
            errors += 1
            print(f"  [快取預熱] {date} 圖表失敗: {exc}", flush=True)

    elapsed = perf_counter() - started
    print(
        f"[快取預熱] 完成：{len(dates)} 日期 / {charts} 圖表 / errors={errors} ({elapsed:.1f}s)",
        flush=True,
    )
    return {"dates": len(dates), "charts": charts, "errors": errors}


def _chart_dates(dates: list[str], month_limit: int, date_limit: int) -> list[str]:
    if month_limit <= 0 or not dates:
        return []

    import pandas as pd

    latest = pd.Timestamp(dates[0])
    start = (latest - pd.DateOffset(months=month_limit)).strftime("%Y-%m-%d")
    selected = [date for date in dates if date >= start]
    if date_limit > 0:
        selected = selected[:date_limit]
    return selected


def _chart_stocks_for_date(
    bundle: dict,
    activity: dict[str, dict],
    fixed_chart_stocks: list[str],
    activity_filters: dict[str, float],
    chart_rows: int,
) -> list[str]:
    """Pick fixed symbols plus rows matching the frontend's default filters."""
    if chart_rows <= 0:
        return fixed_chart_stocks

    vwap_latest = _latest_by_stock(bundle.get("vwap", []) or [])
    source = {str(row.get("stock_id") or ""): row for row in vwap_latest}
    sr_stock_ids = set()
    for row in bundle.get("sr", []) or []:
        stock_id = str(row.get("stock_id") or "").strip()
        if not stock_id:
            continue
        sr_stock_ids.add(stock_id)
        source.setdefault(stock_id, {"stock_id": stock_id, "time": ""})

    candidates = [
        row
        for stock_id, row in source.items()
        if stock_id in sr_stock_ids and _passes_activity(activity.get(stock_id) or {}, activity_filters)
    ]
    candidates.sort(
        key=lambda row: (str(row.get("time") or ""), str(row.get("stock_id") or "")),
        reverse=True,
    )

    candidate_ids = [str(row.get("stock_id")) for row in candidates[:chart_rows]]
    return list(dict.fromkeys([*fixed_chart_stocks, *candidate_ids]))


def _latest_by_stock(rows: list[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        stock_id = str(row.get("stock_id") or "").strip()
        if not stock_id:
            continue
        prev = latest.get(stock_id)
        if prev is None or str(row.get("time") or "") > str(prev.get("time") or ""):
            latest[stock_id] = row
    return list(latest.values())


def _passes_activity(row: dict, filters: dict[str, float]) -> bool:
    for key, threshold in filters.items():
        try:
            if row.get(key) is None or float(row.get(key)) < float(threshold):
                return False
        except Exception:
            return False
    return True
