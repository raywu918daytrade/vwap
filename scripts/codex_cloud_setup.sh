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
