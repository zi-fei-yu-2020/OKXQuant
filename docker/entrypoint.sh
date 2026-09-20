#!/bin/sh
set -eu
umask 077

# Root is used only for named-volume ownership migration, never the API/jobs.
# Do not chmod/chown an environment-supplied arbitrary host/container path.
ENV_PATH=${OKXQUANT_ENV_FILE:-/app/config/.env}
[ "$ENV_PATH" = /app/config/.env ] || { echo "Unsafe environment file location" >&2; exit 1; }
for directory in /app/config /app/data /app/logs /app/backups /home/okxquant/.okx /home/okxquant/.bypy /home/okxquant/.npm-global; do
    [ ! -L "$directory" ] || { echo "Linked runtime directory refused" >&2; exit 1; }
    mkdir -p "$directory"
    if [ "$(id -u)" = 0 ]; then
        chown -hR okxquant:okxquant "$directory"
    fi
done
[ ! -L "$ENV_PATH" ] || { echo "Linked environment file refused" >&2; exit 1; }
touch "$ENV_PATH"
chmod 0700 /home/okxquant/.okx /home/okxquant/.bypy /home/okxquant/.npm-global
chmod 0600 "$ENV_PATH"
if [ "$(id -u)" = 0 ]; then
    chown okxquant:okxquant "$ENV_PATH"
    exec gosu okxquant:okxquant "$@"
fi
exec "$@"
