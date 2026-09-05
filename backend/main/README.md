# main 模組

精簡版後端入口只負責即時資料與 VWAP/型態監控，不再載入任何模型，也不做下單。

| 檔案 | 職責 |
| --- | --- |
| `live_trader.py` | FastAPI + 富邦 M1 collector 的啟動點；每分鐘推 K 線、quote、VWAP/SR/MACD/OBV 事件 |
| `collector.py` | 富邦 WebSocket collector 的重試包裝 |
| `config.py` | `.env` 讀取，保留 watchlist quote 與收盤時間 |
| `state.py` | 執行期共用狀態：訂閱股票、SR 水位、collector backfill 狀態 |
| `startup_data.py` | 開機資料準備：HF DB 新鮮度檢查/同步與富邦訂閱清單重建 |

## 開機 HF 同步

`live_trader.py` 啟動時會先呼叫
`startup_data.sync_local_market_db_from_hf_if_stale()`。這支會檢查預期最新交易日
的 D1 completion flag；如果本機資料落後，就呼叫
`scripts.sync_market_db_from_hf.sync_market_db_from_hf()` 從 Hugging Face dataset
同步 `db/`。

這段只負責把外部維護的 HF dataset 歷史資料拉回本機，不負責每日歷史資料
更新、上傳 HF，也不負責盤中 WebSocket 補 M1 缺口。

## 盤中補資料

`main/backfill.py` 已移除，因為它原本只是在開機後用既有 `db/m1_live` 再跑一次
舊推論流程，補的是前端模型監控畫面，不是原始行情資料。

真正補盤中開服務之前缺掉的 M1 資料，是
`fubon/marketdata_ws.py::_backfill_m1_live()`。`live_trader.py` 仍然把
`state.backfill_done` 傳進 `collector.start_collector()`，collector 補完缺口後
會 set 這個 event；後端接著自動跑一次 `/vwap_sr_catchup` 同步今日 VWAP/SR
記憶體。

## 啟動

```bash
cd backend
python -m main.live_trader
```
