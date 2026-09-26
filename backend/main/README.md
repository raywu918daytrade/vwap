# Dashboard Runtime

`render_reader.py` is the only production entry point in this repository. It
serves the FastAPI/React dashboard, reads precomputed query data from TiDB, and
downloads stock lists plus M1/D1 Parquet snapshots from the configured Hugging
Face Dataset.

The runtime does not contain broker credentials, broker SDKs, subscriptions,
or market-data collection code. Realtime collection runs in the independent
`market-data-collector` project on Oracle.

Run locally with:

```bash
python -m main.render_reader
```
