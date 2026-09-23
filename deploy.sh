#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ENV_FILE=${ENV_FILE:-.env}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.hobby.yml}
CLI_DOMAIN=""
CLI_AI_PROVIDER=""
CLI_AI_BASE_URL=""
CLI_AI_MODEL=""
CLI_AI_MODELS=""
CLI_AI_API_KEY=""
die() { echo "deploy.sh: $*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) [[ $# -gt 1 ]] || die "missing value for --domain"; CLI_DOMAIN="$2"; shift 2 ;;
    --domain=*) CLI_DOMAIN="${1#*=}"; shift ;;
    --ai-provider) [[ $# -gt 1 ]] || die "missing value for --ai-provider"; CLI_AI_PROVIDER="$2"; shift 2 ;;
    --ai-provider=*) CLI_AI_PROVIDER="${1#*=}"; shift ;;
    --ai-base-url) [[ $# -gt 1 ]] || die "missing value for --ai-base-url"; CLI_AI_BASE_URL="$2"; shift 2 ;;
    --ai-base-url=*) CLI_AI_BASE_URL="${1#*=}"; shift ;;
    --ai-model) [[ $# -gt 1 ]] || die "missing value for --ai-model"; CLI_AI_MODEL="$2"; shift 2 ;;
    --ai-model=*) CLI_AI_MODEL="${1#*=}"; shift ;;
    --ai-models) [[ $# -gt 1 ]] || die "missing value for --ai-models"; CLI_AI_MODELS="$2"; shift 2 ;;
    --ai-models=*) CLI_AI_MODELS="${1#*=}"; shift ;;
    --ai-api-key) [[ $# -gt 1 ]] || die "missing value for --ai-api-key"; CLI_AI_API_KEY="$2"; shift 2 ;;
    --ai-api-key=*) CLI_AI_API_KEY="${1#*=}"; shift ;;
    --help|-h) echo "Usage: ./deploy.sh --domain DOMAIN [--ai-provider mimo|glm|qwen|custom] [--ai-model MODEL] [--ai-models LIST]"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
env_file_value() { [[ -f "$ENV_FILE" ]] && { grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- || true; }; }
DOMAIN="${CLI_DOMAIN:-${DOMAIN:-$(env_file_value DOMAIN)}}"
[[ -n "$DOMAIN" ]] || die "DOMAIN is required; pass --domain or export DOMAIN"
case "$DOMAIN" in *://*|*/*|*[[:space:]]*) die "DOMAIN must be a hostname only" ;; esac
if [ ! -f "$ENV_FILE" ]; then
  cp .env.example "$ENV_FILE"
  SECRET=$(openssl rand -hex 16)
  printf '%s\n' "DOMAIN=$DOMAIN" "SITE_URL=https://$DOMAIN" "CADDY_HOST=$DOMAIN" "TLS_BLOCK=" "POSTHOG_SECRET=$SECRET" "ENCRYPTION_SALT_KEYS=$SECRET" "POSTHOG_APP_TAG=latest" "POSTHOG_NODE_TAG=latest" "REGISTRY_URL=posthog-custom" "BUILD_LOCAL_IMAGES=1" "INGESTION_GENERAL_REPLICAS=4" "INGESTION_TOPIC_PARTITIONS=8" "CLICKHOUSE_KAFKA_CONSUMERS=8" >> "$ENV_FILE"
fi
set_env_value() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    rm -f "$ENV_FILE.bak"
  else
    printf "%s=%s
" "$key" "$value" >> "$ENV_FILE"
  fi
}
provider_env_value() { local key="$1"; [[ -n "${!key:-}" ]] && printf "%s" "${!key}" || env_file_value "$key"; }
set_env_value DOMAIN "$DOMAIN"
set_env_value SITE_URL "https://$DOMAIN"
set_env_value CADDY_HOST "$DOMAIN"
AI_PROVIDER="${CLI_AI_PROVIDER:-${AI_PROVIDER:-${LLM_PROVIDER:-$(env_file_value AI_PROVIDER)}}}"
AI_PROVIDER="${AI_PROVIDER,,}"
AI_BASE_URL="${CLI_AI_BASE_URL:-${AI_BASE_URL:-$(env_file_value AI_BASE_URL)}}"
AI_MODEL="${CLI_AI_MODEL:-${AI_MODEL:-$(env_file_value AI_MODEL)}}"
AI_MODELS="${CLI_AI_MODELS:-${AI_SUPPORTED_MODELS:-$(env_file_value AI_SUPPORTED_MODELS)}}"
if [[ -n "$AI_PROVIDER" ]]; then
  case "$AI_PROVIDER" in
    glm) KEY_VAR=GLM_API_KEY; BASE_VAR=GLM_BASE_URL; MODELS_VAR=GLM_SUPPORTED_MODELS; DEFAULT_BASE=https://open.bigmodel.cn/api/paas/v4 ;;
    mimo) KEY_VAR=MIMO_API_KEY; BASE_VAR=MIMO_BASE_URL; MODELS_VAR=MIMO_SUPPORTED_MODELS; DEFAULT_BASE=https://token-plan-cn.xiaomimimo.com/v1 ;;
    qwen) KEY_VAR=QWEN_API_KEY; BASE_VAR=QWEN_BASE_URL; MODELS_VAR=QWEN_SUPPORTED_MODELS; DEFAULT_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1 ;;
    custom) KEY_VAR=CUSTOM_LLM_API_KEY; BASE_VAR=CUSTOM_LLM_BASE_URL; MODELS_VAR=CUSTOM_LLM_MODELS; DEFAULT_BASE="" ;;
    openai) KEY_VAR=OPENAI_API_KEY; BASE_VAR=OPENAI_BASE_URL; MODELS_VAR=OPENAI_SUPPORTED_MODELS; DEFAULT_BASE="" ;;
    anthropic) KEY_VAR=ANTHROPIC_API_KEY; BASE_VAR=ANTHROPIC_BASE_URL; MODELS_VAR=ANTHROPIC_SUPPORTED_MODELS; DEFAULT_BASE="" ;;
    *) die "unsupported AI_PROVIDER: $AI_PROVIDER" ;;
  esac
  KEY="${CLI_AI_API_KEY:-${AI_API_KEY:-$(provider_env_value "$KEY_VAR")}}"
  [[ -n "$KEY" ]] || die "$KEY_VAR is required when AI_PROVIDER=$AI_PROVIDER"
  AI_BASE_URL="${AI_BASE_URL:-$(provider_env_value "$BASE_VAR")}"
  AI_BASE_URL="${AI_BASE_URL:-$DEFAULT_BASE}"
  AI_MODELS="${AI_MODELS:-$(provider_env_value "$MODELS_VAR")}"
  set_env_value "$KEY_VAR" "$KEY"
  set_env_value AI_PROVIDER "$AI_PROVIDER"
  set_env_value LLM_PROVIDER "$AI_PROVIDER"
  [[ -n "$AI_BASE_URL" ]] && set_env_value "$BASE_VAR" "$AI_BASE_URL"
  [[ -n "$AI_MODEL" ]] && set_env_value AI_MODEL "$AI_MODEL"
  [[ -n "$AI_MODELS" ]] && set_env_value "$MODELS_VAR" "$AI_MODELS"
  [[ -n "$AI_MODELS" ]] && set_env_value AI_SUPPORTED_MODELS "$AI_MODELS"
  echo "Configured AI provider: $AI_PROVIDER"
else
  echo "No AI provider selected; existing provider settings will be preserved."
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
  sudo -n docker buildx build --progress=plain --load --build-arg COMMIT_HASH="$(git rev-parse --short HEAD)" -t "$APP_IMAGE" -f Dockerfile .
fi
if ! sudo -n docker image inspect "$NODE_IMAGE" >/dev/null 2>&1; then
  sudo -n docker buildx build --progress=plain --load --build-arg COMMIT_HASH="$(git rev-parse --short HEAD)" -t "$NODE_IMAGE" -f Dockerfile.node .
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
if [[ "$(timeout 30s sudo -n docker exec "$(sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q db | head -n1)" psql -U posthog -d posthog -Atc "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'cyclotron_jobs')" 2>/dev/null || true)" != "t" ]] then for migration in rust/cyclotron-node-migrations/*.sql; do sudo -n docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T db psql -U posthog -d posthog -v ON_ERROR_STOP=1 < "$migration" || exit 1; done; fi
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
