#!/bin/sh
set -eu

: "${REDIS_NODE_ADDRESS:?REDIS_NODE_ADDRESS must be configured}"
: "${REDIS_INITIAL_PRIMARY:?REDIS_INITIAL_PRIMARY must be configured}"
data_dir="${REDIS_DATA_DIR:-/data}"
mkdir -p "$data_dir"
config_file="$data_dir/sentinel.conf"
redis_port="${REDIS_PORT:-6379}"
sentinel_port="${REDIS_SENTINEL_PORT:-26379}"
sentinel_name="${REDIS_SENTINEL_NAME:-posthog}"
server_bin="${REDIS_SERVER_BIN:-redis-server}"
runtime_user="${REDIS_RUNTIME_USER:-redis}"
if [ ! -s "$config_file" ]; then
    umask 077
    cat > "$config_file" <<EOF
bind $REDIS_NODE_ADDRESS
protected-mode yes
port $sentinel_port
dir $data_dir
sentinel monitor $sentinel_name $REDIS_INITIAL_PRIMARY $redis_port 2
sentinel down-after-milliseconds $sentinel_name 5000
sentinel failover-timeout $sentinel_name 30000
sentinel parallel-syncs $sentinel_name 1
EOF
    if [ -n "${REDIS_PASSWORD:-}" ]; then
        printf 'sentinel auth-pass %s %s\n' "$sentinel_name" "$REDIS_PASSWORD" >> "$config_file"
    fi
fi

if [ "$(id -u)" -eq 0 ]; then
    chown -R "$runtime_user:$runtime_user" "$data_dir"
    exec su-exec "$runtime_user" "$server_bin" "$config_file" --sentinel
fi

exec "$server_bin" "$config_file" --sentinel
