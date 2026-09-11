# Codex Notes for VWAP

This repository powers the VWAP Pattern Monitor:

- `backend/`: Python 3.13 FastAPI/service code, market-data sync scripts, VWAP/SR/pattern APIs, and Oracle runtime.
- `frontend/`: React + Vite + Tailwind/DaisyUI frontend.
- `docker-compose.oracle.yml`: production compose file for the Oracle Cloud VM.

## Setup

For Codex cloud environments, use:

```bash
bash scripts/codex_cloud_setup.sh
```

The setup script installs backend and frontend dependencies. It can also prepare an optional SSH key from the `ORACLE_CODEX_VWAP_SSH_KEY` secret, but normal development should not need direct SSH access to Oracle.

## Useful Checks

Run these before handing off code changes when relevant:

```bash
cd frontend && npm run build
```

```bash
cd backend && ../.venv/bin/python -m compileall .
```

There is no dedicated pytest/vitest suite in this repo yet. Use focused import/build checks and any endpoint-specific manual checks that match the change.

## Deployment Boundaries

Pushing to `main` triggers `.github/workflows/deploy-oracle.yml` and deploys production to Oracle Cloud. Do not push to `main`, trigger the deploy workflow, or SSH into Oracle unless the user explicitly asks for deployment or production diagnostics.

GitHub Actions deploys to Oracle using the repository secret `ORACLE_SSH_PRIVATE_KEY`. Do not commit private keys, API tokens, `.env` files, certificates, databases, caches, or runtime logs.

Runtime secrets live outside git:

- Oracle VM: `/home/ubuntu/vwap/backend/.env`
- GitHub Actions: repository secrets such as `ORACLE_SSH_PRIVATE_KEY`, `ORACLE_KNOWN_HOSTS`, and `HF_TOKEN`
- Optional Codex cloud direct SSH: environment secret `ORACLE_CODEX_VWAP_SSH_KEY`

