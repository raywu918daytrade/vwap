# Render Deployment

This branch deploys the frontend and backend as one Render Web Service.

## Shape

- Render creates one web service from `render.yaml`.
- Render builds `backend/Dockerfile` with repo root as the Docker context.
- The Docker build compiles `frontend/` with Vite.
- The final Python image copies the frontend build into `/app/static`.
- FastAPI serves historical API routes, `/health`, and the static frontend from one public HTTP port.
- Render runs `main.render_reader`; it does not import the Fubon collector or open a broker session.

## Deploy

1. Push the `render` branch.
2. In Render, create a Blueprint from this repository and select `render.yaml`.
3. Fill the `HF_REPO_ID` and `HF_TOKEN` secret environment variables.

## Notes

- Do not set `PORT` manually. Render injects it at runtime, and `main.render_reader` listens on `0.0.0.0:$PORT`.
- `HF_DATA_MODE=on-demand` is shared with Oracle. Startup downloads only precomputed signal/ticker shards; chart requests fetch exact monthly D1/M1 shards and retain at most 12 months locally.
- `/health` returns 503 until the startup shards are ready, so Render does not route normal traffic into an incomplete startup.
- No Render database is required. Runtime parquet data and disk caches are recreated from Hugging Face after redeploys or restarts.
- Docker writes a `YYYYMMDD-HHMM` build version into the image. `/health` exposes it and the frontend displays it.
- On Apple Silicon, local smoke tests should use the same platform as Render:
  `docker build -f backend/Dockerfile -t vwap-render-test .`
