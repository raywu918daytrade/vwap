#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
pip install ./backend/wheels/*.whl
pip install -r backend/requirements.txt

(
  cd frontend
  npm ci
)

tidb_env_dir="${HOME}/.config/vwap"
tidb_env="${tidb_env_dir}/tidb.env"
mkdir -p "${tidb_env_dir}"
chmod 700 "${tidb_env_dir}"
: > "${tidb_env}"
chmod 600 "${tidb_env}"

write_export_if_set() {
  local name="$1"
  local value="${!name-}"
  if [ -n "${value}" ]; then
    printf 'export %s=%q\n' "${name}" "${value}" >> "${tidb_env}"
  fi
}

for name in \
  TIDB_DAY_TRADE_DATABASE_URL \
  TIDB_DAY_TRADE_HOST \
  TIDB_DAY_TRADE_PORT \
  TIDB_DAY_TRADE_USER \
  TIDB_DAY_TRADE_PASSWORD \
  TIDB_DAY_TRADE_DATABASE \
  TIDB_DAY_TRADE_SSL_CA \
  TIDB_DAY_TRADE_SSL_DISABLED \
  TIDB_DAY_TRADE_READ \
  TIDB_DAY_TRADE_CHART_READ \
  TIDB_DAY_TRADE_CONNECT_TIMEOUT \
  TIDB_DAY_TRADE_READ_TIMEOUT \
  TIDB_DAY_TRADE_WRITE_TIMEOUT \
  TIDB_DAY_TRADE_COMMIT_TIMEOUT \
  TIDB_DAY_TRADE_POOL_SIZE \
  TIDB_DAY_TRADE_POOL_PING_SECONDS \
  TIDB_DAY_TRADE_VERSION_CACHE_SECONDS
do
  write_export_if_set "${name}"
done

if [ -s "${tidb_env}" ]; then
  echo "Wrote optional TiDB runtime env to ${tidb_env}."
else
  rm -f "${tidb_env}"
fi

if [ -n "${ORACLE_CODEX_VWAP_SSH_KEY:-}" ]; then
  mkdir -p "${HOME}/.ssh"
  chmod 700 "${HOME}/.ssh"
  printf '%s\n' "${ORACLE_CODEX_VWAP_SSH_KEY}" > "${HOME}/.ssh/oracle_codex_vwap"
  chmod 600 "${HOME}/.ssh/oracle_codex_vwap"

  if [ -n "${ORACLE_KNOWN_HOSTS:-}" ]; then
    printf '%s\n' "${ORACLE_KNOWN_HOSTS}" > "${HOME}/.ssh/known_hosts"
  else
    ssh-keyscan -p "${ORACLE_PORT:-22}" -H "${ORACLE_HOST:-129.225.130.75}" >> "${HOME}/.ssh/known_hosts"
  fi
  chmod 600 "${HOME}/.ssh/known_hosts"

  cat > "${HOME}/.ssh/config" <<EOF
Host vwap-oracle
  HostName ${ORACLE_HOST:-129.225.130.75}
  User ${ORACLE_USER:-ubuntu}
  Port ${ORACLE_PORT:-22}
  IdentityFile ~/.ssh/oracle_codex_vwap
  IdentitiesOnly yes
  StrictHostKeyChecking yes
EOF
  chmod 600 "${HOME}/.ssh/config"
fi

echo "Codex cloud setup complete."
