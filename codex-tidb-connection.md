# Codex TiDB Connection Notes

This file intentionally stores only TiDB connection names, shapes, and setup notes.
Do not paste a real password, full database URL, Data Service private key, or `.env` contents into this repository, ChatGPT files, or chat messages.

## Where TiDB Credentials Live

Use the same secret name across services when possible:

```text
TIDB_DAY_TRADE_DATABASE_URL
```

Store the real value only in:

- GitHub Actions repository secret `TIDB_DAY_TRADE_DATABASE_URL`
- Render environment secret `TIDB_DAY_TRADE_DATABASE_URL`
- Oracle VM file `/home/ubuntu/vwap/backend/.env`
- Optional Codex cloud environment secret `TIDB_DAY_TRADE_DATABASE_URL`

Do not commit the real value.

## Preferred Direct Connection Shape

The backend expects a MySQL-compatible TiDB Cloud connection:

```text
mysql+pymysql://<user>:<password>@<host>:4000/<database>?ssl_ca=<path-or-ca-name>
```

Supported URL schemes:

```text
mysql://
mysql+pymysql://
```

Port defaults to:

```text
4000
```

## Split Env Var Alternative

If a service cannot use a single URL, set these instead:

```text
TIDB_DAY_TRADE_HOST=<host>
TIDB_DAY_TRADE_PORT=4000
TIDB_DAY_TRADE_USER=<user>
TIDB_DAY_TRADE_PASSWORD=<password>
TIDB_DAY_TRADE_DATABASE=<database>
TIDB_DAY_TRADE_SSL_CA=<optional-ca-path>
```

The code also accepts legacy names:

```text
DB_HOST
DB_PORT
DB_USERNAME
DB_PASSWORD
DB_DATABASE
```

## Read Flags And Timeouts

These are not secrets and can be stored as normal environment variables:

```text
TIDB_DAY_TRADE_READ=auto
TIDB_DAY_TRADE_CHART_READ=auto
TIDB_DAY_TRADE_CONNECT_TIMEOUT=8
TIDB_DAY_TRADE_READ_TIMEOUT=20
TIDB_DAY_TRADE_WRITE_TIMEOUT=20
TIDB_DAY_TRADE_COMMIT_TIMEOUT=120
TIDB_DAY_TRADE_POOL_SIZE=4
TIDB_DAY_TRADE_POOL_PING_SECONDS=60
TIDB_DAY_TRADE_VERSION_CACHE_SECONDS=60
```

## Codex Cloud Usage

Add `TIDB_DAY_TRADE_DATABASE_URL` as a Codex cloud environment secret.
The setup script writes provided TiDB env vars to:

```text
~/.config/vwap/tidb.env
```

That file is created with `600` permissions in the ephemeral cloud environment and is not part of git.

Before running a TiDB command in Codex cloud:

```bash
source ~/.config/vwap/tidb.env
```

Useful commands:

```bash
cd backend
../.venv/bin/python -m scripts.init_tidb_database
../.venv/bin/python -m scripts.sync_offline_to_tidb --init-months 3
../.venv/bin/python -m scripts.sync_chart_to_tidb --init-months 3 --timeframe all
```

## Important Limitation

`TIDB_DAY_TRADE_PUBLIC_KEY` and `TIDB_DAY_TRADE_PRIVATE_KEY` are Data Service keys.
They are not enough for the current bulk parquet sync path unless there is a deployed custom endpoint. The existing sync scripts need `TIDB_DAY_TRADE_DATABASE_URL` or the split direct MySQL-compatible connection variables.

