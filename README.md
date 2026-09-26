# VWAP Pattern Monitor

這個專案已瘦身成兩個入口資料夾：

- `backend/`：FastAPI、HF 報價/歷史資料同步、可選 TiDB 線上查詢層、日K/M1K 查詢與 VWAP/SR/型態 API。
- `frontend/`：React + Vite + Tailwind + DaisyUI 純前端，保留 VWAP框、觀察框、TradingView 日K/M1K 圖表與即時連線狀態；型態掃描已合併到 VWAP 框內。

已移除模型推論、策略回測、下單、部位、成交回報與舊監控畫面。歷史資料與即時資料保留在 `backend/db/`，不納入 git。

## 啟動

後端：

```bash
cd backend
python -m main.render_reader
```

前端：

```bash
cd frontend
npm install
npm run dev
```

前端開發伺服器預設跑 `http://127.0.0.1:8001`，由 Vite proxy 連到 `TRADING_BACKEND_URL`；未設定時使用 `http://127.0.0.1:8000`。若要純靜態部署，可在建置時設定 `VITE_BACKEND_URL`。

## Render

`render` branch 可用根目錄的 `render.yaml` 建立單一 Render Web Service。Render 會用 `backend/Dockerfile` build 一個 Docker image：先建置 `frontend/`，再把 Vite 產物放進 FastAPI 的 `/app/static`，最後由輕量 reader process 服務 `/`、歷史 API 與 `/health`。

部署時在 Render Blueprint 需填入 `HF_REPO_ID`、`HF_TOKEN`；若要讓 Render 優先讀 TiDB，另填 `TIDB_DAY_TRADE_DATABASE_URL`。盤中 M1 也由 HF Dataset 短週期更新，這個專案不含任何 broker SDK 或憑證。不要手動設定 `PORT`；Render 會注入，後端會綁定 `0.0.0.0:$PORT`。詳細注意事項見 `docs/render.md`。

## 保留功能

- VWAP框：`/vwap_sr_replay`、`/vwap_breakout/today`、`/sr_vwap_cross/today`、`/vwap_activity`、`/vwap_macd_div`、`/vwap_obv_div`，並整合 `/api/pattern/types`、`/api/pattern/scan` 離線型態掃描結果
- 日期切換：前端日曆讀 `/vwap_signal/dates`，只允許 HF 已有離線盤勢/型態資料的日期；直接打週末或未產生的日期會忠實回空
- 觀察框：前端 localStorage 保存觀察股票，沿用 VWAP/SR/MACD/OBV 顯示
- K線圖：`/api/pattern/{stock_id}/detail` 提供日K、M1K、型態線、轉折點、VWAP、日壓力支撐疊圖；VWAP/觀察列另保留 MACD、OBV、0050 子面板
- 歷史資料同步：`backend/scripts/sync_market_db_from_hf.py` 從外部維護的 Hugging Face dataset 下載 `db/`，包含 `db/pattern_scan/d1`、`db/vwap_activity` 與 `db/vwap_signals`
- 離線訊號產生：`backend/scripts/build_pattern_scan.py`、`backend/scripts/build_vwap_activity.py`、`backend/scripts/build_vwap_signals.py` 可在本機初始化最近 N 個月結果並上傳 HF；加 `--sync-tidb` 會把同一批日期 upsert 到 TiDB；`backend/scripts/sync_chart_to_tidb.py` 會把同日期的 D1/M1 圖表 K 線同步到 TiDB；`.github/workflows/build-pattern-scan.yml` 會每天台北 18:00 掃最近 7 天 D1 型態、09:05 activity、VWAP/SR/MACD/OBV/chg，並上傳 HF，若有 TiDB direct connection secrets 也會同步 TiDB
- HF 同步：Render 與 Oracle 統一使用 `HF_DATA_MODE=on-demand`。未開 TiDB 時，啟動只同步 GHA 產出的 `pattern_scan` / `vwap_activity` / `vwap_signals` 與 `tickers`，D1/M1 圖表按日期下載月份檔，本機最多快取 12 個月；開 TiDB 查詢時，啟動只同步 `tickers`，離線結果與歷史圖表 K 線都改先查 TiDB，DB miss 才回退 HF/parquet。服務每天 18:00 檢查新結果，尚未上傳完成時 30 分鐘後重試
- 盤中報價：Oracle `market-data-collector` 寫入 HF Dataset，Render 以短 TTL 讀取 `db/m1_live/YYYY-MM-DD.parquet`
