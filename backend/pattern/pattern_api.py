"""
Pattern Recognition API Endpoints (FastAPI Router)

端點：
- GET  /api/pattern/stocks/full        全市場股票清單（db/tickers/stock_universe_2000.parquet，~1900檔）
- GET  /api/pattern/stocks/daytrade    當沖候選股清單（db/fubon_subscribe/subscribe_list.parquet，
                                        今天實際被富邦WebSocket即時收集的股票池，見該端點的說明）
- GET  /api/pattern/types             取得可用的技術型態選單清單 (含中文名稱)
- GET  /api/pattern/scan             讀取 HF 同步下來的 D1 型態掃描結果
- GET  /api/pattern/scan/submit      舊版相容入口；現在同步讀離線結果並立刻回傳
- GET  /api/pattern/{stock_id}/detail  取得單一股票的 K 線與離線型態繪圖細節（支援快取）
- POST /api/pattern/cache/clear      手動清空快取
"""

import uuid
from typing import Any, Dict, List, Optional
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
import pandas as pd

from pattern.abcd_bear.detector import AbcdBearDetector
from pattern.abcd_bull.detector import AbcdBullDetector
from pattern.breakdown_retest.detector import BreakdownRetestDetector
from pattern.breakout_retest.detector import BreakoutRetestDetector
from pattern.cup_handle.detector import CupHandleDetector
from pattern.data_loader import get_latest_candle_timestamp, get_stock_candles
from pattern.head_shoulders_bottom.detector import HeadShouldersBottomDetector
from pattern.head_shoulders_top.detector import HeadShouldersTopDetector
from pattern.m_top.detector import MTopDetector
from pattern.macd_hist_bear.detector import MacdHistBearDetector
from pattern.macd_hist_bull.detector import MacdHistBullDetector
from pattern.offline_store import available_scan_dates, read_pattern_for_stock, read_pattern_scan
from pattern.triangle.detector import TriangleDetector
from pattern.w_bottom.detector import WBottomDetector

router = APIRouter(prefix="/api/pattern", tags=["技術型態"])
PATTERN_SCAN_TIMEFRAME = "day"

# 註冊所有可用型態檢測器
DETECTORS = {
    "triangle": TriangleDetector(),
    "abcd_bull": AbcdBullDetector(),
    "abcd_bear": AbcdBearDetector(),
    "w_bottom": WBottomDetector(),
    "m_top": MTopDetector(),
    "head_shoulders_bottom": HeadShouldersBottomDetector(),
    "head_shoulders_top": HeadShouldersTopDetector(),
    "cup_handle": CupHandleDetector(),
    "breakout_retest": BreakoutRetestDetector(),
    "breakdown_retest": BreakdownRetestDetector(),
    "macd_hist_bull": MacdHistBullDetector(),
    "macd_hist_bear": MacdHistBearDetector(),
}

_EVENT_DATE_DETAIL_KEYS = (
    "event_date",
    "break_date",
    "trigger_date",
    "completion_date",
    "h2_date",
    "right_shoulder_date",
    "handle_breakout_date",
)


def _date_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)[:10]


def _latest_date_from(items: Any, keys: tuple[str, ...]) -> str:
    latest = ""
    if not isinstance(items, list):
        return latest
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            date_text = _date_text(item.get(key))
            if date_text and date_text > latest:
                latest = date_text
    return latest


def _attach_event_date(pattern_dict: Dict[str, Any]) -> None:
    """Expose a normalized event_date for the front-end VWAP signal column."""
    details = pattern_dict.get("details")
    if isinstance(details, dict):
        for key in _EVENT_DATE_DETAIL_KEYS:
            date_text = _date_text(details.get(key))
            if date_text:
                pattern_dict["event_date"] = date_text
                return

    line_date = _latest_date_from(pattern_dict.get("lines"), ("end_date", "start_date"))
    if line_date:
        pattern_dict["event_date"] = line_date
        return

    pivot_date = _latest_date_from(pattern_dict.get("pivots"), ("date",))
    if pivot_date:
        pattern_dict["event_date"] = pivot_date
        return

    date_text = _date_text(pattern_dict.get("date"))
    if date_text:
        pattern_dict["event_date"] = date_text


def _read_tick_universe() -> tuple[set[str], Dict[str, str]]:
    """Read the HF-synced day-trade universe and stock-name map."""
    try:
        df = pd.read_parquet(
            Path(__file__).parent.parent / "db/tickers/tick_universe.parquet",
            columns=["stock_id", "name"],
        )
        universe = set(df["stock_id"].astype(str))
        names = {
            str(sid): str(name).strip()
            for sid, name in zip(df["stock_id"], df["name"])
            if pd.notna(name) and str(name).strip()
        }
        return universe, names
    except Exception:
        return set(), {}


def _read_full_universe() -> List[Dict[str, str]]:
    """Read the HF-synced full stock universe used by the stock picker."""
    try:
        df = pd.read_parquet(
            Path(__file__).parent.parent / "db/tickers/stock_universe_2000.parquet",
            columns=["stock_id", "name"],
        )
        return [
            {"stock_id": str(sid), "name": str(name).strip()}
            for sid, name in zip(df["stock_id"], df["name"])
            if pd.notna(name) and str(name).strip()
        ]
    except Exception:
        return []


# 從 db/tickers/tick_universe.parquet 載入股票集合與名稱對照。服務 24 小時
# 常駐時，19:00 HF 同步後會呼叫 reload_universe_cache() 重讀這兩份檔案。
TICK_UNIVERSE_SET, STOCK_NAME_MAP = _read_tick_universe()

# 全市場股票清單（db/tickers/stock_universe_2000.parquet，~1900檔，含中文
# 名稱），供前端「股票清單」欄的全市場選項用（見 GET /stocks/full）。跟
# TICK_UNIVERSE_SET 是不同來源、不同數量的股票池，分開載入。
FULL_UNIVERSE_LIST = _read_full_universe()


def _load_daytrade_list() -> List[Dict[str, str]]:
    """讀 db/tickers/tick_universe.parquet 裡的當沖候選清單。

    HF 同步下來的 tick_universe.parquet 可能已經是過濾後的候選
    母體，只保留 day_trade_tier/rank/forced_include 等欄位，沒有舊版
    daytrade_ok。這種格式就直接回傳檔案內容；若遇到舊格式含 daytrade_ok，
    則沿用 daytrade_ok=True 的列。

    舊版語意：main/premarket.py::refresh_tickers() 每天早上6點實際呼叫
    fubon/subscribe_list.py::build_and_save_subscribe_list() 驗證過的結果，是
    「今天實際會被富邦WebSocket即時收集」的股票池，db/m1_live 有哪些股票
    就是看這份決定的。

    2026-08-19改版：股票清單欄的「當沖候選」選項原本讀
    db/tickers/tick_universe.parquet 整份（不分今天能不能當沖）——這會
    出現「這支股票明明在清單裡選得到，但今天完全沒有m1資料」的困惑
    （母體有它，但今天當沖資格驗證沒過，例如臨時被列入注意股/處置股），
    改成只取 daytrade_ok=True 的子集才會跟 db/m1_live 的真實內容一致。

    2026-08-19再改：原本這裡讀的是另外存的 db/fubon_subscribe/
    subscribe_list.parquet，跟 tick_universe.parquet 內容高度重疊（同一批
    股票代號，只差 connection_id/驗證日期），使用者要求合併成一份，daytrade_ok/
    connection_id/verify_date 現在都是 tick_universe.parquet 自己的欄位
    （見 fubon/subscribe_list.py::build_and_save_subscribe_list() 的說明），
    不用再讀第二個檔案。

    欄位名用 stock_id（不是 id）：跟 FULL_UNIVERSE_LIST/dashboard.html
    navMonitoring() 的既有慣例一致（見那邊的說明）。每次呼叫都重新讀檔
    （不像 TICK_UNIVERSE_SET/FULL_UNIVERSE_LIST 是模組載入時讀一次快取
    起來）——這份清單理論上一天只會被 refresh_tickers() 覆寫一次，但
    每天什麼時候被覆寫、backend process 什麼時候啟動兩者不保證誰先誰後，
    重新讀檔確保拿到當下最新版本，讀檔成本可以忽略。"""
    path = Path(__file__).parent.parent / "db/tickers/tick_universe.parquet"
    try:
        df = pd.read_parquet(path, columns=["stock_id", "name", "daytrade_ok"])
    except Exception:
        try:
            df = pd.read_parquet(path, columns=["stock_id", "name"])
        except Exception:
            return []
    if "daytrade_ok" in df.columns:
        df = df[df["daytrade_ok"] == True]  # noqa: E712
    return [
        {"stock_id": str(sid), "name": str(name).strip()}
        for sid, name in zip(df["stock_id"], df["name"])
        if pd.notna(name) and str(name).strip()
    ]

# 記憶體快取 (In-Memory Cache)
_SCAN_CACHE: Dict[tuple, Dict[str, Any]] = {}
_DETAIL_CACHE: Dict[tuple, Dict[str, Any]] = {}


def reload_universe_cache() -> None:
    """Reload stock universes after HF overwrites db/tickers files."""
    global TICK_UNIVERSE_SET, STOCK_NAME_MAP, FULL_UNIVERSE_LIST
    TICK_UNIVERSE_SET, STOCK_NAME_MAP = _read_tick_universe()
    FULL_UNIVERSE_LIST = _read_full_universe()


def _normalize_scan_timeframe(timeframe: str | None) -> str:
    """Pattern scanning only supports D1; intraday K lines are chart-only."""
    value = str(timeframe or PATTERN_SCAN_TIMEFRAME).strip().lower()
    if value in (PATTERN_SCAN_TIMEFRAME, "d1"):
        return PATTERN_SCAN_TIMEFRAME
    raise HTTPException(status_code=400, detail="型態掃描只支援 D1（日K），已移除 1m/3m/5m 掃描")


def _selected_pattern_types(pattern_type: str) -> list[str]:
    """Normalize a scan pattern selector into registered detector keys."""
    raw_types = [t.strip() for t in str(pattern_type or "").split(",") if t.strip()]
    if "all" in raw_types:
        return list(DETECTORS.keys())

    selected_types = []
    invalid_types = []
    for p in raw_types:
        if p in DETECTORS:
            if p not in selected_types:
                selected_types.append(p)
        else:
            invalid_types.append(p)

    if invalid_types:
        raise HTTPException(
            status_code=400,
            detail=f"尚未支援或無效的型態: {invalid_types}。可用型態: {list(DETECTORS.keys())} 或 all",
        )
    if not selected_types:
        raise HTTPException(
            status_code=400,
            detail=f"未指定有效的型態。可用型態: {list(DETECTORS.keys())} 或 all",
        )
    return selected_types


def _horizontal_sr_lines(df: pd.DataFrame, to_epoch, stock_id: str) -> List[Dict[str, Any]]:
    """日K 橫向壓力／支撐（雙轉折水平線），不要求型態過關。

    股票清單日K／分K 疊圖用：跟 VWAP+壓力支撐同一套 horizontal_sr_prices。

    2026-08-20改：原本自己拿 df（get_stock_candles 抓回來的、含今天還在跑
    的合成K棒）切最後120筆算，跟 main/live_trader.py::_refresh_sr_levels()/
    pattern/vwap_sr_scan.py::sr_levels_for_date()（180日曆天、明確排除今天）
    用不同窗口——導致D1圖跟VWAP突破欄m1疊圖的「日壓力/日支撐」對不起來
    （使用者實測3443：D1顯示5956.51，m1疊圖顯示5337.50，差了快12%，見
    對話紀錄）。改成直接呼叫 sr_levels_for_date()，兩邊共用同一個計算＋
    同一份cache，不會再各自維護一套邏輯、以後也不會再漂移。"""
    if df is None or df.empty or len(df) < 25:
        return []
    date_str = str(df["date"].iloc[-1])[:10]
    today = pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y-%m-%d")
    if date_str != today:
        from pattern.vwap_signal_store import read_vwap_signals

        bundle = read_vwap_signals(date_str, stock_ids={str(stock_id)})
        levels = (bundle or {}).get("sr_levels", {}).get(str(stock_id), {})
        res, sup = levels.get("resistance"), levels.get("support")
    else:
        from pattern.vwap_sr_scan import sr_levels_for_date

        res, sup = sr_levels_for_date(date_str, {str(stock_id)}).get(str(stock_id), (None, None))
    if res is None and sup is None:
        return []

    sub = df.iloc[-120:].reset_index(drop=True)
    start_date = str(sub["date"].iloc[0])
    end_date = str(sub["date"].iloc[-1])
    try:
        t_start = to_epoch(pd.Timestamp(start_date))
        t_end = to_epoch(pd.Timestamp(end_date))
    except Exception:
        return []

    lines: List[Dict[str, Any]] = []
    if res is not None:
        lines.append(
            {
                "start_time": t_start,
                "end_time": t_end,
                "start_price": round(float(res), 2),
                "end_price": round(float(res), 2),
                "line_type": "resistance",
            }
        )
    if sup is not None:
        lines.append(
            {
                "start_time": t_start,
                "end_time": t_end,
                "start_price": round(float(sup), 2),
                "end_price": round(float(sup), 2),
                "line_type": "support",
            }
        )
    return lines


@router.post("/cache/clear", summary="手動清空 Pattern 快取")
def clear_pattern_cache() -> Dict[str, Any]:
    """清空記憶體中的 Pattern 掃描與詳情快取。"""
    scan_count = len(_SCAN_CACHE)
    detail_count = len(_DETAIL_CACHE)
    _SCAN_CACHE.clear()
    _DETAIL_CACHE.clear()
    return {
        "ok": True,
        "message": f"已清空快取 (scan 快取: {scan_count} 筆, detail 快取: {detail_count} 筆)",
    }


@router.get("/stocks/full", summary="全市場股票清單（供前端股票清單欄選擇用）")
def get_full_stock_list() -> Dict[str, Any]:
    """回傳全市場(~1900檔)股票代號＋中文名稱清單，來源
    db/tickers/stock_universe_2000.parquet，供前端「股票清單」欄渲染。"""
    return {"universe": "full", "stocks": FULL_UNIVERSE_LIST}


@router.get("/stocks/daytrade", summary="當沖候選股清單（供前端股票清單欄選擇用）")
def get_daytrade_stock_list() -> Dict[str, Any]:
    """回傳今天實際被富邦WebSocket即時收集的當沖候選股清單（db/m1_live
    實際涵蓋的股票池就是這份決定的），來源 db/fubon_subscribe/
    subscribe_list.parquet（main/premarket.py::refresh_tickers() 每天06:00
    更新，見 _load_daytrade_list() 的說明），供前端「股票清單」欄渲染。"""
    return {"universe": "daytrade", "stocks": _load_daytrade_list()}


@router.get("/types", summary="取得可用的技術型態選單清單")
def get_pattern_types() -> Dict[str, Any]:
    """回傳所有已註冊的技術型態 ID 與中文名稱，供前端選單使用。"""
    return {
        "patterns": [
            {
                "id": key,
                "name": detector.display_name,
            }
            for key, detector in DETECTORS.items()
        ]
    }


@router.get("/scan", summary="過濾篩選符合特定 D1 型態的股票清單")
def scan_patterns(
    pattern_type: str = Query("triangle", description="型態種類: 可帶單一型態(triangle)、逗號分隔多型態(triangle,w_bottom)、或全型態(all)。可用型態: triangle, abcd_bull, abcd_bear, w_bottom, m_top, head_shoulders_bottom, head_shoulders_top, cup_handle, breakout_retest, breakdown_retest, macd_hist_bull, macd_hist_bear, all"),
    timeframe: str = Query(PATTERN_SCAN_TIMEFRAME, description="型態掃描固定只支援 D1/day"),
    date: Optional[str] = Query(None, description="基準日期 (YYYY-MM-DD)，預設為最新交易日"),
    min_score: float = Query(60.0, description="最小信心度分數 (0~100)"),
    limit: int = Query(120, description="K 線視窗根數，預設 120 根"),
) -> Dict[str, Any]:
    """Read precomputed D1 pattern rows downloaded from HF.

    The Oracle runtime no longer runs pattern detectors on request. Offline
    jobs produce `db/pattern_scan/d1/YYYY_MM.parquet`; missing dates return an
    empty list so non-trading days stay blank.
    """
    # 處理直接在 Python 內部調用函式時可能傳入 Query 物件的情況
    if hasattr(pattern_type, "default"):
        pattern_type = pattern_type.default
    if hasattr(timeframe, "default"):
        timeframe = timeframe.default
    if hasattr(min_score, "default"):
        min_score = min_score.default
    if hasattr(limit, "default"):
        limit = limit.default
    timeframe = _normalize_scan_timeframe(timeframe)
    del limit
    selected_types = _selected_pattern_types(pattern_type)
    scan_date, matches = read_pattern_scan(date, selected_types, min_score=float(min_score))

    result = {
        "pattern_type": pattern_type,
        "pattern_types": selected_types,
        "timeframe": timeframe,
        "date": scan_date or date,
        "total_matches": len(matches),
        "results": matches,
    }
    return result


@router.get("/scan/dates", summary="取得已有離線型態結果的日期")
def get_pattern_scan_dates() -> Dict[str, Any]:
    """Return dates that have at least one precomputed D1 pattern result."""
    dates = available_scan_dates()
    return {
        "dates": dates,
        "latest": dates[-1] if dates else None,
        "total": len(dates),
    }


# ── 相容入口 ─────────────────────────────────────────────────────────────
# 前端已改成直接讀 /scan。/scan/submit 保留給舊頁面或外部呼叫，現在也是讀
# HF 同步的離線 parquet，不再在 Oracle runtime 上開背景 detector。
_scan_jobs: Dict[str, Dict[str, Any]] = {}


@router.get("/scan/submit", summary="舊版相容入口：讀取離線型態掃描結果")
async def submit_scan(
    pattern_type: str = Query("triangle", description="同 /scan 的說明"),
    timeframe: str = Query(PATTERN_SCAN_TIMEFRAME, description="型態掃描固定只支援 D1/day"),
    date: Optional[str] = Query(None, description="基準日期 (YYYY-MM-DD)，預設為最新交易日"),
    min_score: float = Query(60.0, description="最小信心度分數 (0~100)"),
    limit: int = Query(120, description="K 線視窗根數，預設 120 根"),
) -> Dict[str, Any]:
    """Return a completed job payload without starting runtime detector work."""
    job_id = str(uuid.uuid4())
    result = scan_patterns(pattern_type, timeframe, date, min_score, limit)
    _scan_jobs[job_id] = {"status": "done", "data": result}
    try:
        from api import _broadcast

        _broadcast({"type": "pattern_scan_done", "job_id": job_id, "data": result})
    except Exception:
        pass
    return {"job_id": job_id, "data": result}


@router.get("/{stock_id}/detail", summary="單一股票 K 線與型態繪圖細節")
def get_pattern_detail(
    stock_id: str,
    pattern_type: str = Query(
        "triangle",
        description="型態種類: triangle, w_bottom, m_top, abcd_bull, abcd_bear, head_shoulders_bottom, cup_handle, macd_hist_bull, macd_hist_bear；或 none（只回 K 線／VWAP，不跑型態偵測——股票清單欄型態全關時用）",
    ),
    timeframe: str = Query("day", description="圖表週期: 1m, 3m, 5m, day；pattern_type 非 none 時只支援 D1/day"),
    date: Optional[str] = Query(None, description="基準日期 (YYYY-MM-DD)"),
    limit: int = Query(120, description="K 線視窗根數，預設 120 根，full_day=true 時忽略"),
    full_day: bool = Query(False, description="True 時忽略 limit，改成回傳 date（沒帶則今天）當天完整一天的K線（開盤到現在/收盤），只對 1m/3m/5m 有意義"),
    force_live: bool = Query(False, description="True 時略過快取，強制重新讀取一次（股票清單欄「即時」按鈕/自動刷新用）。cache_key 版本號 day 看 db/adjustment_day mtime、intraday 看2330最新一根K線時間戳，理論上都會自動跟著變、不用強制略過，這個參數是給使用者手動要求「現在立刻重抓」時的保險，不用等下一次自然變化"),
) -> Dict[str, Any]:
    """回傳 K 線數據（時間已轉為前端所需的 UTC timestamp）以及型態關鍵轉折點與趨勢線線段資訊（支援快取）。"""
    # 處理直接在 Python 內部調用函式時可能傳入 Query 物件的情況
    if hasattr(pattern_type, "default"):
        pattern_type = pattern_type.default
    if hasattr(timeframe, "default"):
        timeframe = timeframe.default
    if hasattr(date, "default"):
        date = date.default
    if hasattr(limit, "default"):
        limit = limit.default
    if hasattr(full_day, "default"):
        full_day = full_day.default
    if hasattr(force_live, "default"):
        force_live = force_live.default

    # none：不跑偵測器，只回 K 線／VWAP（股票清單型態全關、
    # 或盤中圖只要日K水位疊加時的 intraday 底圖）。
    skip_pattern = pattern_type in (None, "", "none")
    if not skip_pattern and pattern_type not in DETECTORS:
        raise HTTPException(
            status_code=400,
            detail=f"尚未支援或無效的型態: {pattern_type}。可用型態: {list(DETECTORS.keys())} 或 none",
        )
    if not skip_pattern:
        timeframe = _normalize_scan_timeframe(timeframe)

    # 取得最新 K 線時間戳以構造智慧快取 Key
    latest_ts = get_latest_candle_timestamp(timeframe=timeframe, date=date)
    cache_key = (stock_id, pattern_type if not skip_pattern else "none", timeframe, date or "latest", limit, full_day, latest_ts)

    if not force_live and cache_key in _DETAIL_CACHE:
        return _DETAIL_CACHE[cache_key]

    df_candles = get_stock_candles(stock_id=stock_id, timeframe=timeframe, date=date, limit=limit, full_day=full_day)
    if df_candles.empty:
        raise HTTPException(status_code=404, detail=f"查無 {stock_id} 在 timeframe={timeframe} 的 K 線資料")

    detector = None if skip_pattern else DETECTORS[pattern_type]

    # 轉換 K 線給前端圖表使用 (依照 CLAUDE.md 使用 tw_naive_to_epoch)
    from api import tw_naive_to_epoch

    candles_output = []
    for _, row in df_candles.iterrows():
        dt = row["date"]
        ts = tw_naive_to_epoch(dt)
        candles_output.append({
            "time": ts,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        })

    # VWAP（2026-08-12加，供股票清單欄畫圖用）：本質上是「當日盤中」指標，
    # 每天從0重新累積，日K（timeframe=day）沒有意義，只算 intraday。公式
    # 依日期分組，使用 cum(close*volume)/cum(volume)；跟 live VWAP 掃描共用
    # 同一個 session VWAP 定義。
    vwap_output = []
    if timeframe in ("1m", "3m", "5m") and not df_candles.empty:
        vwap_df = df_candles.copy()
        vwap_df["_day"] = vwap_df["date"].dt.strftime("%Y-%m-%d")
        vwap_df["_pv"] = vwap_df["close"] * vwap_df["volume"]
        g = vwap_df.groupby("_day")
        cum_vol = g["volume"].transform("cumsum")
        cum_pv = g["_pv"].transform("cumsum")
        vwap_series = cum_pv / cum_vol.replace(0, float("nan"))
        for dt, v in zip(vwap_df["date"], vwap_series):
            if pd.notna(v):
                vwap_output.append({"time": tw_naive_to_epoch(dt), "value": round(float(v), 2)})

    pattern_output = None
    if not skip_pattern and timeframe == PATTERN_SCAN_TIMEFRAME:
        pattern_dict = read_pattern_for_stock(
            stock_id=stock_id,
            pattern_type=pattern_type,
            date=str(date)[:10] if date else None,
            min_score=60.0,
        )
        if pattern_dict:
            pattern_dict["pattern_name"] = pattern_dict.get("pattern_name") or detector.display_name
            _attach_event_date(pattern_dict)
            # 把 lines 和 pivots 裡的時間也轉成 epoch 秒數方便前端畫圖
            for p in pattern_dict.get("pivots", []):
                try:
                    p["time"] = tw_naive_to_epoch(pd.Timestamp(p["date"]))
                except Exception:
                    p["time"] = None

            for l in pattern_dict.get("lines", []):
                try:
                    t1 = tw_naive_to_epoch(pd.Timestamp(l["start_date"]))
                    t2 = tw_naive_to_epoch(pd.Timestamp(l["end_date"]))
                    l["start_time"] = t1
                    l["end_time"] = t2
                except Exception:
                    l["start_time"] = None
                    l["end_time"] = None

            pattern_output = pattern_dict

    # 日K 橫向壓力／支撐：不綁型態掃描。分K 疊圖要的是「反覆碰到的水平水位」。
    sr_lines_output: List[Dict[str, Any]] = []
    if timeframe == "day":
        try:
            sr_lines_output = _horizontal_sr_lines(df_candles, tw_naive_to_epoch, stock_id)
        except Exception:
            sr_lines_output = []

    result = {
        "stock_id": stock_id,
        "stock_name": STOCK_NAME_MAP.get(str(stock_id), str(stock_id)),
        "in_tick_universe": str(stock_id) in TICK_UNIVERSE_SET,
        "timeframe": timeframe,
        "pattern_type": "none" if skip_pattern else pattern_type,
        "pattern_name": None if skip_pattern else detector.display_name,
        "candles": candles_output,
        "vwap": vwap_output,
        "pattern": pattern_output,
        "sr_lines": sr_lines_output,
    }

    _DETAIL_CACHE[cache_key] = result
    return result
