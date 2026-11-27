#!/bin/sh
set -eu

: "${REDIS_NODE_ADDRESS:?REDIS_NODE_ADDRESS must be configured}"
: "${REDIS_INITIAL_PRIMARY:?REDIS_INITIAL_PRIMARY must be configured}"
: "${REDIS_SENTINEL_HOST:?REDIS_SENTINEL_HOST must be configured}"
: "${REDIS_SENTINEL_PORT:?REDIS_SENTINEL_PORT must be configured}"
data_dir="${REDIS_DATA_DIR:-/data}"
redis_port="${REDIS_PORT:-6379}"
sentinel_name="${REDIS_SENTINEL_NAME:-posthog}"
cli_bin="${REDIS_CLI_BIN:-redis-cli}"
server_bin="${REDIS_SERVER_BIN:-redis-server}"
runtime_user="${REDIS_RUNTIME_USER:-redis}"

master_address=
master_port=
while [ -z "$master_address" ] || [ -z "$master_port" ]; do
    master_info="$("$cli_bin" --raw -h "$REDIS_SENTINEL_HOST" -p "$REDIS_SENTINEL_PORT" SENTINEL get-master-addr-by-name "$sentinel_name" 2>/dev/null || true)"
    master_address="$(printf '%s\n' "$master_info" | sed -n '1p')"
    master_port="$(printf '%s\n' "$master_info" | sed -n '2p')"
    if [ -z "$master_address" ] || [ -z "$master_port" ]; then
        sleep 2
    fi
done

set -- --port "$redis_port" --bind 127.0.0.1 "$REDIS_NODE_ADDRESS" --protected-mode yes
if [ -n "${REDIS_PASSWORD:-}" ]; then
    set -- "$@" --requirepass "$REDIS_PASSWORD" --masterauth "$REDIS_PASSWORD"
fi
set -- "$@" --replica-announce-ip "$REDIS_NODE_ADDRESS" --replica-announce-port "$redis_port"
set -- "$@" --appendonly yes --appendfsync everysec --min-replicas-to-write 1 --min-replicas-max-lag 5 --dir "$data_dir"
if [ "$master_address" != "$REDIS_NODE_ADDRESS" ]; then
    set -- "$@" --replicaof "$master_address" "$master_port"
fi

if [ "$(id -u)" -eq 0 ]; then
    chown -R "$runtime_user:$runtime_user" "$data_dir"
    exec su-exec "$runtime_user" "$server_bin" "$@"
fi

exec "$server_bin" "$@"
