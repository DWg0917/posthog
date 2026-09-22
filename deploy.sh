#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ENV_FILE=${ENV_FILE-.env}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.hobby.yml}
DOMAIN=${DOMAIN-dwg.asia}
if [ ! -f "$ENV_FILE" ]; then
  cp .env.example "$ENV_FILE"
  SECRET=$(openssl rand -hex 16)
  printf '%s\n' "DOMAIN=$DOMAIN" "SITE_URL=https://$DOMAIN" "CADDY_HOST=$DOMAIN" "TLS_BLOCK=" "POSTHOG_SECRET=$SECRET" "ENCRYPTION_SALT_KEYS=$SECRET" "POSTHOG_APP_TAG=latest" "POSTHOG_NODE_TAG=latest" "REGISTRY_URL=posthog-custom" "BUILD_LOCAL_IMAGES=1" "INGESTION_GENERAL_REPLICAS=4" "INGESTION_TOPIC_PARTITIONS=8" "CLICKHOUSE_KAFKA_CONSUMERS=8" >> "$ENV_FILE"
fi

env_value() {
  awk -F= -v key="$1" '$1 == key {print substr($0, index($0, "=") + 1); exit}' "$ENV_FILE"
}

INGESTION_GENERAL_REPLICAS=${INGESTION_GENERAL_REPLICAS:-$(env_value INGESTION_GENERAL_REPLICAS)}
INGESTION_GENERAL_REPLICAS=${INGESTION_GENERAL_REPLICAS:-4}
INGESTION_TOPIC_PARTITIONS=${INGESTION_TOPIC_PARTITIONS:-$(env_value INGESTION_TOPIC_PARTITIONS)}
INGESTION_TOPIC_PARTITIONS=${INGESTION_TOPIC_PARTITIONS:-8}
CLICKHOUSE_KAFKA_CONSUMERS=${CLICKHOUSE_KAFKA_CONSUMERS:-$(env_value CLICKHOUSE_KAFKA_CONSUMERS)}
CLICKHOUSE_KAFKA_CONSUMERS=${CLICKHOUSE_KAFKA_CONSUMERS:-8}
chmod 600 "$ENV_FILE"
sudo -n docker info >/dev/null
sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --quiet
APP_IMAGE=posthog-custom:latest
NODE_IMAGE=posthog-custom-node:latest
if ! sudo -n docker image inspect "$APP_IMAGE" >/dev/null 2>&1; then
  sudo -n docker build --progress=plain -t "$APP_IMAGE" -f Dockerfile .
fi
if ! sudo -n docker image inspect "$NODE_IMAGE" >/dev/null 2>&1; then
  sudo -n docker build --progress=plain -t "$NODE_IMAGE" -f Dockerfile.node .
fi
mkdir -p share
GEOIP_CONTAINER=$(sudo -n docker create "$APP_IMAGE")
sudo -n docker cp "$GEOIP_CONTAINER:/code/share/GeoLite2-City.mmdb" share/GeoLite2-City.mmdb
sudo -n docker rm "$GEOIP_CONTAINER" >/dev/null
sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d db
sleep 10
sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm web python manage.py migrate --noinput
echo "Applying hobby personhog migrations..."
sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm web python manage.py apply_persons_migrations --hobby
sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm web python manage.py migrate_clickhouse
if ! sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T db psql -U posthog -d posthog -Atc "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE tablename = cyclotron_jobs)" | grep -q t; then for migration in rust/cyclotron-node-migrations/*.sql; do sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T db psql -U posthog -d posthog -v ON_ERROR_STOP=1 < "$migration" || exit 1; done; fi
sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --scale ingestion-general="$INGESTION_GENERAL_REPLICAS"
sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --force-recreate proxy web worker temporal-django-worker plugins ingestion-error-tracking
python3 bin/scale_hobby_consumers.py --apply \
  --partitions "$INGESTION_TOPIC_PARTITIONS" \
  --consumers "$CLICKHOUSE_KAFKA_CONSUMERS" \
  --backup-dir "${INGESTION_BACKUP_DIR:-$PWD/share/ingestion-backups}"
for _ in $(seq 1 90); do
  if curl -kfsS --resolve "$DOMAIN:443:127.0.0.1" --max-time 5 "https://$DOMAIN/_health" >/dev/null; then
    echo "PostHog is healthy: https://$DOMAIN"
    exit 0
  fi
  sleep 5
done
echo "PostHog did not become healthy in time" >&2
sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps >&2
exit 1
