# Oracle Cloud 維運筆記（VWAP）

這份文件是 vwap 專案上 Oracle Cloud 的部署/維運筆記，不放任何密鑰值。
實際 token、API key、憑證都放在雲端主機的 `backend/.env`，不要 commit。

## 主機資訊

- 服務商：Oracle Cloud Infrastructure（OCI）
- 區域：`ap-osaka-1`（大阪）
- 建議規格：`VM.Standard.E2.1.Micro`（AMD/amd64, 1 OCPU / 1GB RAM, Always Free）
- 公開 IP：`129.225.130.75`
- 主機用途：`vwap-prod`
- OS：Ubuntu 22.04 Minimal
- 專案路徑：`~/vwap`（`ubuntu` 使用者家目錄下）
- 建議加 2GB swap（`/swapfile`），讓 1GB RAM 有緩衝
- 建議安裝 `fail2ban`；SSH 密碼登入關閉，只用金鑰

目前 repo 內的富邦 SDK wheel 是 x86_64：

```text
backend/wheels/fubon_neo-2.2.8-cp37-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
```

所以 Oracle VM 先選 AMD/amd64。若改用 Ampere ARM VM，要先換成 ARM 可安裝的富邦 SDK。

## SSH 連線

```bash
ssh ubuntu@129.225.130.75
```

建立主機時使用本機 `~/.ssh/id_ed25519.pub` 公鑰。密碼登入應保持關閉。

如果你的來源 IP 會變動，SSH 可以在 OCI Security List / NSG 對 `0.0.0.0/0`
開放，但要靠「金鑰登入 only + fail2ban」控風險。

## OCI CLI

本機可用 OCI CLI 管理主機。設定檔應留在本機 `~/.oci/`，不要放進 Dropbox/repo：

- `~/.oci/config`
- `~/.oci/oracle_api_key.pem`

常用指令：

```bash
# 列出 compute instance
oci compute instance list --compartment-id <tenancy-ocid> \
  --query "data[].{Name:\"display-name\", State:\"lifecycle-state\"}" --output table

# 重啟主機
oci compute instance action --instance-id <instance-ocid> --action SOFTRESET

# 查 VNIC / public IP
oci compute instance list-vnics --instance-id <instance-ocid>
```

`<tenancy-ocid>`、`<instance-ocid>` 不寫進這份文件；需要時從 `~/.oci/config`
或 OCI console 查。

## Oracle 防火牆

OCI Security List 或 NSG 至少開：

- TCP `22`：SSH
- TCP `80`：Web UI

有網域與 HTTPS 後再開：

- TCP `443`：HTTPS

目前 compose 只對外開 `80`。backend 的 `8000` 只在 Docker 內網給 nginx proxy。

## 新 VM 初始安裝

新機才需要跑這段；已裝 Docker 的主機可跳過。

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git fail2ban

curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu

sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

跑完 `usermod` 後重新登入 SSH，讓 `ubuntu` 使用者拿到 docker group 權限。

## GitHub Actions 自動部署

repo 是 public：

```text
https://github.com/raywu918daytrade/vwap
```

部署方向是 GitHub Actions SSH 到 Oracle VM，把目前 commit 打包成 release
archive 上傳，再在 VM 上 build / restart Docker Compose。VM 不需要 clone private
repo，也不要放 `GITHUB_DAY_TRADE` token。

自動部署 workflow：

```text
.github/workflows/deploy-oracle.yml
```

每次 push 到 `main`，或手動在 GitHub Actions 按 `workflow_dispatch`，都會部署到
`/home/ubuntu/vwap`。

離線訊號 workflow：

```text
.github/workflows/build-pattern-scan.yml
```

離線訊號 workflow 預設每天台北時間 19:00 執行，會產生當天 D1 型態、
`09:05` activity（ATR / 5分幅 / 量PR），以及歷史查表用的
VWAP/SR/MACD/OBV/chg bundle，並上傳到 HF。第一次初始化最近三個月時，
可以手動 dispatch 並填 `init_months=3`；也可以在本機跑
`python -m scripts.build_pattern_scan --init-months 3 --upload` 與
`python -m scripts.build_vwap_activity --init-months 3 --upload`、
`python -m scripts.build_vwap_signals --init-months 3 --upload`。

Oracle backend 也預設 19:00 同步 HF；若 GHA 還沒把當天 `vwap_signals`
上傳完成，backend 會保留這次同步未完成狀態，30 分鐘後自動重試。

GitHub repo 需要設定這些 Secrets / Variables：

必填 Secret：

- `ORACLE_SSH_PRIVATE_KEY`：GitHub Actions 登入 Oracle VM 用的私鑰。
- `HF_TOKEN`：Hugging Face dataset 寫入 token，供離線訊號 workflow 上傳 parquet。

建議 Secret：

- `ORACLE_KNOWN_HOSTS`：Oracle VM 的 SSH known_hosts 指紋，避免每次用
  `ssh-keyscan` 動態取得。

可選 Variables（不設就用預設值）：

- `ORACLE_HOST`：預設 `129.225.130.75`
- `ORACLE_USER`：預設 `ubuntu`
- `ORACLE_PORT`：預設 `22`
- `ORACLE_DEPLOY_PATH`：預設 `/home/ubuntu/vwap`
- `HF_REPO_ID`：Hugging Face dataset repo id；離線訊號 workflow 預設用
  `raywu918daytrade/daytrade`，只有要改 dataset 時才需要覆蓋。若不放
  Variable，也可改放同名 Secret。

建議產生一把只給 GitHub Actions 用的 SSH key，不要用自己的日常登入私鑰：

```bash
ssh-keygen -t ed25519 -C "github-actions-vwap" -f /tmp/vwap_oracle_deploy -N ""
```

把公鑰加到 Oracle VM：

```bash
cat /tmp/vwap_oracle_deploy.pub | ssh ubuntu@129.225.130.75 \
  'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
```

把私鑰放到 GitHub Secret：

```bash
gh secret set ORACLE_SSH_PRIVATE_KEY --repo raywu918daytrade/vwap < /tmp/vwap_oracle_deploy
```

建議也把 known_hosts 固定下來：

```bash
ssh-keyscan -H 129.225.130.75 | gh secret set ORACLE_KNOWN_HOSTS --repo raywu918daytrade/vwap
```

設定完成後刪掉本機暫存私鑰：

```bash
rm -f /tmp/vwap_oracle_deploy /tmp/vwap_oracle_deploy.pub
```

## 第一次部署

```bash
cd ~/vwap
cp backend/.env.example backend/.env
mkdir -p backend/db backend/log backend/logs backend/.cache
```

編輯 `backend/.env`：

```bash
nano backend/.env
```

雲端需要設定：

- `FUGLE`
- `FUGLE_DAYTRADE`
- `FUBON_ID`
- `FUBON_API_KEY`
- `FUBON_CERT_B64`
- `FUBON_CERT_PASS`
- `HF_REPO_ID`
- `HF_TOKEN`

`FUBON_CERT_B64` 在本機可這樣產生，再貼到雲端 `.env`：

```bash
base64 -i your-cert.p12 | tr -d '\n'
```

啟動：

```bash
docker compose -f docker-compose.oracle.yml up -d --build
```

看狀態：

```bash
docker compose -f docker-compose.oracle.yml ps
docker compose -f docker-compose.oracle.yml logs -f backend
```

健康檢查：

```bash
curl http://127.0.0.1/health
curl http://129.225.130.75/health
```

第一次啟動如果 `backend/db` 是空的，backend 會依 D1 flag 判斷資料落後，從 HF
下載保留資料夾。`m1` / `m5_std` 只抓 rolling 24 個月。

## 更新部署

平常不用 SSH 手動更新；push 到 `main` 後 GitHub Actions 會自動部署。

部署腳本：

```text
scripts/deploy_oracle.sh
```

部署時會保留 VM 上的 runtime/secret：

- `backend/.env`
- `backend/db`
- `backend/log`
- `backend/logs`
- `backend/.cache`

其他 source code 會以目前 commit 覆蓋，避免舊檔案殘留。

若 GitHub Actions 暫時不能用，才手動部署：

```bash
ssh ubuntu@129.225.130.75
cd ~/vwap
docker compose -f docker-compose.oracle.yml up -d --build --remove-orphans
```

若只要重啟服務：

```bash
docker compose -f docker-compose.oracle.yml restart backend
docker compose -f docker-compose.oracle.yml restart web
```

## 架構

`docker-compose.oracle.yml` 只跑兩個 container：

- `backend`：FastAPI + `main.live_trader`，負責 HF 同步、富邦即時 M1、API、SSE。
- `web`：Nginx serve React build，並把 API/SSE proxy 到 backend。

對外路徑：

- `/`：React 前端
- `/api/*`：pattern / log API
- `/vwap_*`：盤中訊號 API
- `/sr_vwap_cross/*`：SR crossing API
- `/chart/*`、`/quote/*`：圖表/報價 API
- `/stream`：SSE 即時推送
- `/health`：健康檢查

runtime 目錄用 bind mount 留在 VM：

- `backend/db:/app/db`
- `backend/log:/app/log`
- `backend/logs:/app/logs`
- `backend/.cache:/app/.cache`

## 資料保留規則

- `db/m1`, `db/m5_std`：rolling 24 個月份。
- `db/pattern_scan/d1`：離線 D1 型態掃描結果，按月 parquet shard 全量保留。
- `db/vwap_activity`：離線 09:05 activity 結果，按月 parquet shard 全量保留。
- `db/vwap_signals`：離線 VWAP/SR/MACD/OBV/chg 結果，按月 parquet shard 全量保留。
- `db/m1_live`：最近 14 個交易檔。
- `log`：最近 7 個日曆天。
- `logs`：最近 14 個日曆天。
- `d1`, `adjustment_day`, `adjustment_factor`, `tick_adjust_factor`, `tickers`,
  flags：全量保留。

可在 `backend/.env` 調整：

```bash
MARKET_INTRADAY_RETENTION_MONTHS=24
M1_LIVE_RETENTION_FILES=14
SDK_LOG_RETENTION_DAYS=7
APP_LOG_RETENTION_DAYS=14
```

## 手動維運

```bash
# 看 container
docker compose -f docker-compose.oracle.yml ps

# 看後端最近 log
docker compose -f docker-compose.oracle.yml logs --tail=200 backend

# 持續看後端 log
docker compose -f docker-compose.oracle.yml logs -f backend

# 手動跑 HF 同步
docker compose -f docker-compose.oracle.yml exec backend python -m scripts.sync_market_db_from_hf

# 只同步離線查表資料
docker compose -f docker-compose.oracle.yml exec backend python -m scripts.sync_market_db_from_hf --only pattern_scan vwap_activity vwap_signals

# 本機第一次初始化最近 3 個月 D1 型態並上傳 HF
cd backend
python -m scripts.build_pattern_scan --init-months 3 --upload
python -m scripts.build_vwap_activity --init-months 3 --upload
python -m scripts.build_vwap_signals --init-months 3 --upload

# 本機或 GitHub Actions 手動補單日離線訊號
cd backend
python -m scripts.build_pattern_scan --date 2026-09-04 --upload
python -m scripts.build_vwap_activity --date 2026-09-04 --upload
python -m scripts.build_vwap_signals --date 2026-09-04 --upload

# 手動調成保留 36 個月分K
docker compose -f docker-compose.oracle.yml exec backend python -m scripts.sync_market_db_from_hf --intraday-months 36

# 看資料大小
du -sh backend/db backend/log backend/logs backend/.cache
```

## 本機與雲端同時跑

雲端跑 `main.live_trader` 後，會登入富邦並開即時行情 WebSocket。
如果本機也用同一組富邦帳號跑 live trader，可能會互搶 session 或碰到連線上限。

正式讓雲端 24 小時跑時，建議本機不要同時開同一組即時服務；本機只做開發與測試。

## 備份觀念

必備備份：

- `backend/.env`：密鑰與憑證設定，GitHub 不會有。

可重建：

- `backend/db`：主要資料可從 HF 重新下載。
- `backend/.cache`：HF cache，可刪可重建。
- `backend/log`、`backend/logs`：runtime log，不需要長期備份。
- `backend/db/m1_live`：短期盤中 runtime 檔，只保留最近幾個交易日作為 fallback。

## 已知限制

- 1GB RAM 偏緊，建議保留 2GB swap；build 或 HF sync 時尤其有幫助。
- 公開 IP `129.225.130.75` 若不是 Reserved Public IP，重開機後有機會改變。
- 目前只有 HTTP。要 HTTPS 可以接 Cloudflare、Caddy，或在 OCI 上另外放反向代理。
- 第一輪 HF sync 會下載 rolling 24 個月的 `m1` / `m5_std`，第一次啟動會比平常久；離線訊號只下載 `pattern_scan` / `vwap_activity` / `vwap_signals` 小檔時會快很多。
