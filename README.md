# VWAP Pattern Monitor

這個專案已瘦身成兩個入口資料夾：

- `backend/`：FastAPI、資料下載、日K/M1K 查詢、富邦即時 M1 collector、VWAP/SR/型態 API。
- `frontend/`：Django dashboard 與後端 proxy，只負責顯示型態框、VWAP框、日K、M1K、即時連線狀態。

已移除模型推論、策略回測、下單、部位、成交回報與舊監控畫面。歷史資料與即時資料保留在 `backend/db/`，不納入 git。

## 啟動

後端：

```bash
cd backend
python -m main.live_trader
```

前端：

```bash
cd frontend
python manage.py runserver 127.0.0.1:8001
```

前端預設透過 `/proxy` 連到 `TRADING_BACKEND_URL`，未設定時使用 `http://localhost:8000`。

## 保留功能

- 型態框：`/api/pattern/*`
- VWAP框：`/vwap_breakout/today`、`/sr_vwap_cross/today`、`/vwap_sr_catchup`、`/vwap_sr_replay`
- 日K/M1K：`/chart/{stock_id}/candles/history`、`/chart/{stock_id}/candles`
- 歷史資料下載：`backend/scripts/update_daily.py` 與相關 `backend/finmind/`、`backend/data/` 工具
- 開機 HF 同步：`backend/main/startup_data.py` 會視本機 D1 flag 新鮮度呼叫 `backend/scripts/sync_market_db_from_hf.py`
- 即時連線：`backend/fubon/marketdata_ws.py`

## 盤中補資料

刪掉的 `backend/main/backfill.py` 原本只補舊模型監控畫面，不是補原始 M1K。

盤中啟動服務時，真正補 M1K 缺口的是 `backend/fubon/marketdata_ws.py::_backfill_m1_live()`。`backend/main/live_trader.py` 仍會把 `state.backfill_done` 傳給 collector；collector 補完後，後端會再做一次 VWAP/SR catchup，讓前端框內事件補齊。
