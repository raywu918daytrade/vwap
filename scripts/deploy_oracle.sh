#!/usr/bin/env bash
set -Eeuo pipefail

deploy_path="${DEPLOY_PATH:-$HOME/vwap}"
archive="${DEPLOY_ARCHIVE:?DEPLOY_ARCHIVE is required}"
commit="${DEPLOY_COMMIT:-unknown}"
export APP_VERSION="${APP_VERSION:-$(TZ=Asia/Taipei date +%Y%m%d-%H%M)}"
compose_file="docker-compose.oracle.yml"
base_dir="$(dirname "${deploy_path}")"
release_dir="$(mktemp -d "${base_dir}/.vwap-release.XXXXXX")"

cleanup() {
  rm -rf "${release_dir}"
  rm -f "${archive}"
}
trap cleanup EXIT

echo "Deploying vwap ${commit} (${APP_VERSION}) to ${deploy_path}"

if [ ! -f "${archive}" ]; then
  echo "Release archive not found: ${archive}" >&2
  exit 1
fi

tar -xzf "${archive}" -C "${release_dir}"

if [ -f "${deploy_path}/backend/.env" ]; then
  cp "${deploy_path}/backend/.env" "${release_dir}/backend/.env"
elif [ -f "${release_dir}/backend/.env.example" ]; then
  cp "${release_dir}/backend/.env.example" "${release_dir}/backend/.env"
fi

cd "${release_dir}"
docker compose -f "${compose_file}" config --quiet
docker compose -f "${compose_file}" build

mkdir -p "${deploy_path}/backend"

# Remove old source files, but keep runtime data and secrets in place.
find "${deploy_path}" -mindepth 1 -maxdepth 1 ! -name backend -exec rm -rf -- {} +
find "${deploy_path}/backend" -mindepth 1 -maxdepth 1 \
  ! -name .env \
  ! -name db \
  ! -name log \
  ! -name logs \
  ! -name .cache \
  -exec rm -rf -- {} +

rm -f "${release_dir}/backend/.env"
cp -a "${release_dir}/." "${deploy_path}/"

mkdir -p \
  "${deploy_path}/backend/db" \
  "${deploy_path}/backend/log" \
  "${deploy_path}/backend/logs" \
  "${deploy_path}/backend/.cache"

if [ ! -f "${deploy_path}/backend/.env" ]; then
  cp "${deploy_path}/backend/.env.example" "${deploy_path}/backend/.env"
fi

chmod 600 "${deploy_path}/backend/.env"
chmod 700 "${deploy_path}/backend/.cache"

cd "${deploy_path}"
docker compose -f "${compose_file}" up -d --no-build --force-recreate --remove-orphans

for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1/health >/tmp/vwap-health.json; then
    echo "Health check passed:"
    cat /tmp/vwap-health.json
    echo

    # /health can be HTTP 200 even when the Fubon realtime collector is down.
    # Give the collector a short grace period, then print backend runtime logs
    # into the deploy job so production connection failures are diagnosable.
    collector_status="$(python3 - <<'PY'
import json
try:
    with open('/tmp/vwap-health.json', 'r', encoding='utf-8') as fh:
        print(json.load(fh).get('collector', 'unknown'))
except Exception:
    print('unknown')
PY
)"
    if [ "${collector_status}" != "running" ]; then
      echo "Collector is ${collector_status}; waiting 15s and collecting backend diagnostics..."
      sleep 15
      curl -fsS http://127.0.0.1/health >/tmp/vwap-health-after.json || true
      echo "Health after grace period:"
      cat /tmp/vwap-health-after.json 2>/dev/null || true
      echo
      echo "Recent backend logs:"
      docker compose -f "${compose_file}" logs --tail=160 backend || true
    fi

    docker compose -f "${compose_file}" ps
    docker image prune -f >/dev/null || true
    exit 0
  fi
  sleep 2
done

echo "Health check failed after deploy" >&2
docker compose -f "${compose_file}" ps >&2 || true
docker compose -f "${compose_file}" logs --tail=120 >&2 || true
exit 1
