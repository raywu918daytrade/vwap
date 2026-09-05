"""
本地資料讀取工具（唯讀，還原權息後版本，2026-08-01 重整，2026-08-03 再改）

這裡的公開函式會先讀 data/raw_query.py 的原始版本，再視本機是否存在
db/tick_adjust_factor 套用拆股/合股調整。新版預設 HF 同步不下載 tick 相關
資料；若沒有該 factor 資料夾，會維持原始 K 線價格繼續回傳。

只有明確知道自己需要**原始（未還原權息）**價格的地方（例如
data/build_tick_adjust_factor.py 這種本來就是在反推調整係數的 pipeline
腳本），才需要改用 data/raw_query.py 對應的同名函式。如果需要 pattern
型態偵測用的「完整還原（含一般除權息）」版本，請用
data/adjustment_query.py，不要用這支——理由見那支檔頭說明。

三種資料對應：
    load_m1()      → db/m1/        歷史分K（按月分檔，已還原拆股/合股）
    load_day()     → db/d1/        日K（按月分檔，已還原拆股/合股）
    load_m1_live() → db/m1_live/   今日即時分K（M1 collector 寫入）

單支股票查詢（用 pyarrow filter pushdown，不用像 load_day() 整個資料集讀進記憶體）：
    load_day_by_stock(stock_id)  → db/d1/       單一股票的全部日K

還原權息機制（2026-08-01加，2026-08-02/03改）：db/m1／db/d1 存的都是
原始（未還原權息）價格。這裡的 load_m1()/load_m3()/load_m5()/
load_m3_std()/load_m5_std()/load_day() 會在讀取時嘗試 join db/tick_adjust_factor
（見 data/build_tick_adjust_factor.py，factor 只反映拆股/合股，一般除權息
不還原、直接用原始K線——理由見那支檔頭說明）換算後再回傳，不動底層
parquet／不動 data/raw_query.py 讀到的原始資料。沒有該 factor 資料夾時，
這些函式會以 factor=1.0 的原始價格回傳。

pattern 系列是系統裡唯一的例外，需要「完整還原（含一般除權息）」的基準，
因為除息造成的真實跳空會讓型態偵測的轉折點判斷誤判（2026-08-02 用 1101
除息實測過），這部分改用 db/adjustment_day（沿用 Fugle adjusted="true"
完整還原下載）+ data/adjustment_query.py，跟這支檔案完全分開維護，不要混用。
"""

from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

from data import raw_query

_ROOT = Path(__file__).parent.parent


def load_m1(start_date: str | None = None) -> pd.DataFrame:
    """load_m1() 的「還原權息後」版本（data.raw_query.load_m1() 是原始版本）。

    db/m1 存的是原始價格（2026-08-01 改，見 data/m1_data_loader.py 頂部說明：
    故意不帶 adjusted，統一交給查詢層處理，不用管這筆資料當初是 Fugle／
    富邦／finmind 哪個來源抓的）。這支函式在讀取當下 join
    db/tick_adjust_factor 反推出的每日調整係數，把 open/high/low/close 換算
    成跟 db/fugle_day 一致的還原後基準再回傳，不動 db/m1 本身。缺 factor 的
    (stock_id, date)（該股票沒有tick資料/還沒建 factor）維持原始價格
    （factor=1.0），不會整筆丟掉。

    volume 也會跟著除以 factor（2026-08-03 加）：這裡的 factor 只反映拆股/
    合股，拆股會直接改變股數（例如1:4拆股股數變4倍），拆股前後的原始股數
    不是同一個基準，volume 不調整的話，跨過拆股日算 rolling 均量/量能指標
    會看到一個假的成交量斷崖。volume 除以 factor（factor<1時等於乘上股數
    變多的倍數）才能讓「價格×成交量」這個成交金額維持不變，是正確的還原
    方向（跟price乘上factor互為倒數關係）。除息的 factor 固定是1.0（這次
    系統預設不還原除息），所以除息不會觸發volume調整，符合預期。

    參數/回傳欄位同 data.raw_query.load_m1()。"""
    m1_df = raw_query.load_m1(start_date=start_date)
    if m1_df.empty:
        return m1_df

    m1_df["day"] = m1_df["date"].dt.strftime("%Y-%m-%d")
    factor_df = _load_adjust_factor(None, None, start_date)
    m1_df = m1_df.merge(
        factor_df[["stock_id", "date", "factor"]].rename(columns={"date": "day"}),
        on=["stock_id", "day"],
        how="left",
    )
    m1_df["factor"] = m1_df["factor"].fillna(1.0)
    for col in ["open", "high", "low", "close"]:
        m1_df[col] = (m1_df[col] * m1_df["factor"]).round(2).astype("float32")
    m1_df["volume"] = (m1_df["volume"] / m1_df["factor"]).round().astype("int64")
    return m1_df.drop(columns=["day", "factor"]).sort_values(["stock_id", "date"]).reset_index(drop=True)


def _adjust_ohlc(df: pd.DataFrame, start_date: str | None) -> pd.DataFrame:
    """共用邏輯：load_m3()/load_m5()/load_m3_std()/load_m5_std()/load_day() 都是
    跟 load_m1() 一樣的「完整時間戳識別、OHLC乘上當日係數、volume除以當日係數」
    模式，抽成共用函式避免好幾份幾乎一樣的程式碼（load_m1() 沒有直接用這支，
    是因為它比這幾支早寫、當時還沒抽出來，行為完全等價，不用特別去改）。
    volume 為什麼要除以 factor：見 load_m1() 的說明。"""
    if df.empty:
        return df
    df["day"] = df["date"].dt.strftime("%Y-%m-%d")
    factor_df = _load_adjust_factor(None, None, start_date)
    df = df.merge(
        factor_df[["stock_id", "date", "factor"]].rename(columns={"date": "day"}),
        on=["stock_id", "day"],
        how="left",
    )
    df["factor"] = df["factor"].fillna(1.0)
    for col in ["open", "high", "low", "close"]:
        df[col] = (df[col] * df["factor"]).round(2).astype("float32")
    if "volume" in df.columns:
        df["volume"] = (df["volume"] / df["factor"]).round().astype("int64")
    return df.drop(columns=["day", "factor"]).sort_values(["stock_id", "date"]).reset_index(drop=True)


def load_m3(start_date: str | None = None) -> pd.DataFrame:
    """load_m3() 的「還原權息後」版本（data.raw_query.load_m3() 是原始版本）。
    3 分鐘K，rolling 版本，每分鐘一列（由 build_m3_m5_rolling.py 預先聚合）。
    說明同 load_m1()。"""
    return _adjust_ohlc(raw_query.load_m3(start_date=start_date), start_date)


def load_m5(start_date: str | None = None) -> pd.DataFrame:
    """load_m5() 的「還原權息後」版本（data.raw_query.load_m5() 是原始版本）。
    5 分鐘K，rolling 版本，每分鐘一列（由 build_m3_m5_rolling.py 預先聚合）。
    說明同 load_m1()。"""
    return _adjust_ohlc(raw_query.load_m5(start_date=start_date), start_date)


def load_m3_std(start_date: str | None = None) -> pd.DataFrame:
    """load_m3_std() 的「還原權息後」版本（data.raw_query.load_m3_std() 是
    原始版本）。標準獨立 3 分K棒，一根K棒一列（由 build_m3_m5_std.py 預先
    聚合）。說明同 load_m1()。"""
    return _adjust_ohlc(raw_query.load_m3_std(start_date=start_date), start_date)


def load_m5_std(start_date: str | None = None) -> pd.DataFrame:
    """load_m5_std() 的「還原權息後」版本（data.raw_query.load_m5_std() 是
    原始版本）。標準獨立 5 分K棒，一根K棒一列（由 build_m3_m5_std.py 預先
    聚合）。說明同 load_m1()。"""
    return _adjust_ohlc(raw_query.load_m5_std(start_date=start_date), start_date)


def load_day(start_date: str | None = None) -> pd.DataFrame:
    """load_day() 的「還原權息後（只還原拆股/合股）」版本
    （data.raw_query.load_day() 是原始版本，讀 db/d1）。

    db/d1 存的是原始價格（2026-08-03 改，取代原本的 db/fugle_day）。這支
    函式在讀取當下 join db/tick_adjust_factor 反推出的每日拆股/合股調整
    係數，換算後回傳，不動 db/d1 本身。缺 factor 的 (stock_id, date) 維持
    原始價格（factor=1.0）。volume 不受影響。

    如果需要 pattern 型態偵測用的完整還原版本（含一般除權息），請用
    data/adjustment_query.py::load_pattern_day()，不要用這支——說明見
    data/adjustment_query.py 檔頭。

    start_date：同 load_m1() 的說明，預設 None = 讀全部。"""
    return _adjust_ohlc(raw_query.load_day(start_date=start_date), start_date)


def load_day_by_stock(stock_id: str, date: str = None) -> pd.DataFrame:
    """load_day_by_stock() 的「還原權息後（只還原拆股/合股）」版本
    （data.raw_query.load_day_by_stock() 是原始版本）。

    只讀該股票的 row group，不用像 load_day() 一樣把全市場都讀進記憶體，
    適合只需要單支股票時用（例如查前一交易日收盤價）。

    date: 選填，格式 "YYYY-MM-DD"，指定只回傳該日那一筆；不填則回傳該股票
    全部日K（依日期排序）。查無資料一律回傳空 DataFrame。"""
    day_df = raw_query.load_day_by_stock(stock_id, date=date)
    if day_df.empty:
        return day_df

    day_df["day"] = day_df["date"].dt.strftime("%Y-%m-%d")
    factor_df = _load_adjust_factor(stock_id, date, None)
    day_df = day_df.merge(
        factor_df[["stock_id", "date", "factor"]].rename(columns={"date": "day"}),
        on=["stock_id", "day"],
        how="left",
    )
    day_df["factor"] = day_df["factor"].fillna(1.0)
    for col in ["open", "high", "low", "close"]:
        day_df[col] = (day_df[col] * day_df["factor"]).round(2).astype("float32")
    day_df["volume"] = (day_df["volume"] / day_df["factor"]).round().astype("int64")
    return day_df.drop(columns=["day", "factor"]).sort_values("date").reset_index(drop=True)


def load_m1_live(date: str = None) -> pd.DataFrame:
    """載入今日即時分K（db/m1_live/YYYY-MM-DD.parquet），盤後自動 backfill
    補齊。「今天」相對於自己必然是同一個基準，raw==adjusted，不用換算。"""
    if date is None:
        date = pd.Timestamp.now().strftime("%Y-%m-%d")
    path = _ROOT / f"db/m1_live/{date}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["stock_id", "date"]).reset_index(drop=True)


def _load_adjust_factor(stock_id: str | None, date: str | None, start_date: str | None) -> pd.DataFrame:
    """載入可選的 db/tick_adjust_factor/，給這支檔案裡的 K 線查詢共用。"""
    path = _ROOT / "db/tick_adjust_factor"
    if not path.exists():
        return pd.DataFrame(columns=["stock_id", "date", "factor"])

    eff_start = start_date
    if date is not None and (eff_start is None or date < eff_start):
        eff_start = date

    paths = raw_query._dataset_paths(path, eff_start)
    if not paths:
        return pd.DataFrame(columns=["stock_id", "date", "factor"])


    dataset = ds.dataset(paths, format="parquet")
    filt = None
    if stock_id is not None:
        filt = ds.field("stock_id") == stock_id
    if date is not None:
        filt = (filt & (ds.field("date") == date)) if filt is not None else (ds.field("date") == date)

    table = dataset.to_table(filter=filt)
    if table.num_rows == 0:
        return pd.DataFrame(columns=["stock_id", "date", "factor"])
    return table.to_pandas()
