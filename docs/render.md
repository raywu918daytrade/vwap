# Render Deployment

This branch deploys the frontend and backend as one Render Web Service.

## Shape

- Render creates one web service from `render.yaml`.
- Render builds `backend/Dockerfile` with repo root as the Docker context.
- The Docker build compiles `frontend/` with Vite.
- The final Python image copies the frontend build into `/app/static`.
- FastAPI serves API routes, SSE, `/health`, and the static frontend from one public HTTP port.
- The Dockerfile defaults to `linux/amd64` because the bundled Fubon SDK wheel is amd64-only.

## Deploy

1. Push the `render` branch.
2. In Render, create a Blueprint from this repository and select `render.yaml`.
3. Fill the secret environment variables Render prompts for:
   - `FUGLE`
   - `FUGLE_DAYTRADE`
   - `FUBON_ID`
   - `FUBON_API_KEY`
   - `FUBON_CERT_B64`
   - `HF_REPO_ID`
   - `HF_TOKEN`
4. If your Fubon certificate needs a password, add `FUBON_CERT_PASS` manually in the service environment variables.

## Notes

- Do not set `PORT` manually. Render injects it at runtime, and `main.live_trader` listens on `0.0.0.0:$PORT`.
- The service is pinned to one instance because the app owns live market-data collection and local runtime files inside the process.
- No Render database is required. Runtime parquet data and caches are stored on the container filesystem and are recreated from Hugging Face syncs after redeploys or restarts.
- The default plan in `render.yaml` is `0.5c-512mb`. Increase it if startup syncs or chart queries run out of memory.
- On Apple Silicon, local smoke tests should use the same platform as Render:
  `docker build -f backend/Dockerfile -t vwap-render-test .`
