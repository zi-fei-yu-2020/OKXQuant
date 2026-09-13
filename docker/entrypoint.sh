#!/bin/sh
set -eu

mkdir -p /app/config /app/data /app/logs /app/backups /home/okxquant/.okx /home/okxquant/.bypy /home/okxquant/.npm-global
touch "${OKXQUANT_ENV_FILE:-/app/config/.env}"
chmod 0700 /home/okxquant/.okx /home/okxquant/.bypy /home/okxquant/.npm-global
chmod 0600 "${OKXQUANT_ENV_FILE:-/app/config/.env}"

exec "$@"
