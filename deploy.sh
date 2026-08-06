#!/bin/bash
set -euo pipefail

# =============================================================================
# PostHog Production Deployment Script
# =============================================================================
# Usage:
#   ./deploy.sh              # First-time deploy (data + app layer)
#   ./deploy.sh --upgrade    # Upgrade app layer only
#   ./deploy.sh --data-only  # Start data layer only
#   ./deploy.sh --app-only   # Start app layer only

MODE="${1:-full}"

echo "=========================================="
echo "PostHog Production Deployment"
echo "Mode: ${MODE}"
echo "=========================================="

# Validate .env
if [ ! -f .env ]; then
    echo "ERROR: .env file not found. Copy .env.prod to .env first."
    exit 1
fi

source .env

# Check required vars
for var in DOMAIN POSTHOG_SECRET ENCRYPTION_SALT_KEYS REGISTRY_URL POSTHOG_APP_TAG DATA_LAYER_HOST; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: Required variable ${var} is not set in .env"
        exit 1
    fi
done

# Select ClickHouse cluster config
if [ ! -f docker/clickhouse/config.d/cluster.xml ]; then
    echo "No cluster.xml found, using single-node config..."
    cp docker/clickhouse/config.d/cluster-single.xml docker/clickhouse/config.d/cluster.xml
fi

echo ""
echo "ClickHouse mode: $(head -1 docker/clickhouse/config.d/cluster.xml)"

# ---- Data Layer ----
start_data_layer() {
    echo ""
    echo "[1/3] Starting data layer..."
    docker compose -f docker-compose.hobby.yml -f docker-compose.data.yml up -d

    echo ""
    echo "[2/3] Waiting for data layer health checks..."
    local max_wait=120
    local elapsed=0
    while [ $elapsed -lt $max_wait ]; do
        if docker compose -f docker-compose.hobby.yml -f docker-compose.data.yml ps --format json 2>/dev/null | \
           python3 -c "
import sys, json
services = ['db', 'redis7', 'clickhouse', 'kafka', 'zookeeper']
for line in sys.stdin:
    try:
        c = json.loads(line)
        name = c.get('Name', c.get('name', ''))
        state = c.get('State', c.get('state', ''))
        for s in services:
            if s in name and 'running' in state.lower():
                services.remove(s)
    except: pass
sys.exit(0 if not services else 1)
" 2>/dev/null; then
            echo "  Data layer is healthy!"
            break
        fi
        echo "  Waiting... (${elapsed}s)"
        sleep 5
        elapsed=$((elapsed + 5))
    done

    if [ $elapsed -ge $max_wait ]; then
        echo "WARNING: Data layer health check timed out after ${max_wait}s"
        echo "Check: docker compose -f docker-compose.hobby.yml -f docker-compose.data.yml ps"
    fi
}

# ---- Migrations ----
run_migrations() {
    echo ""
    echo "Running migrations..."
    docker compose -f docker-compose.hobby.yml -f docker-compose.data.yml run --rm \
        -e DATABASE_URL="postgres://${POSTGRES_USER:-posthog}:${POSTGRES_PASSWORD:-posthog}@db:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-posthog}" \
        -e CLICKHOUSE_HOST=clickhouse \
        -e SECRET_KEY="${POSTHOG_SECRET}" \
        -e ENCRYPTION_SALT_KEYS="${ENCRYPTION_SALT_KEYS}" \
        web sh -c "python manage.py migrate && python manage.py migrate_clickhouse"
    echo "  Migrations complete!"
}

# ---- App Layer ----
start_app_layer() {
    echo ""
    echo "Starting app layer..."
    docker compose -f docker-compose.hobby.yml -f docker-compose.app.yml up -d
    echo "  App layer started!"
}

# ---- Execute ----
case "${MODE}" in
    full)
        start_data_layer
        run_migrations
        start_app_layer
        ;;
    --upgrade)
        echo "Upgrading app layer (data layer stays running)..."
        start_app_layer
        ;;
    --data-only)
        start_data_layer
        run_migrations
        ;;
    --app-only)
        start_app_layer
        ;;
    *)
        echo "Unknown mode: ${MODE}"
        echo "Usage: ./deploy.sh [--upgrade|--data-only|--app-only]"
        exit 1
        ;;
esac

echo ""
echo "=========================================="
echo "Deployment complete!"
echo "=========================================="
echo ""
echo "Access PostHog at: https://${DOMAIN}"
echo ""
echo "Useful commands:"
echo "  Data layer logs:  docker compose -f docker-compose.hobby.yml -f docker-compose.data.yml logs -f"
echo "  App layer logs:   docker compose -f docker-compose.hobby.yml -f docker-compose.app.yml logs -f"
echo "  Data layer ps:    docker compose -f docker-compose.hobby.yml -f docker-compose.data.yml ps"
echo "  App layer ps:     docker compose -f docker-compose.hobby.yml -f docker-compose.app.yml ps"
