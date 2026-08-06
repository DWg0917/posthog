#!/bin/bash
set -euo pipefail

# =============================================================================
# PostHog Custom Image Build Script
# =============================================================================

REGISTRY="${REGISTRY_URL:-your-registry.com/posthog}"
TAG="${BUILD_TAG:-latest}"

echo "=========================================="
echo "Building PostHog Custom Images"
echo "Registry: ${REGISTRY}"
echo "Tag: ${TAG}"
echo "=========================================="

# 1. Django App (REQUIRED — contains code changes from Steps 1 & 11)
echo ""
echo "[1/3] Building Django App image..."
docker build \
    -t "${REGISTRY}:${TAG}" \
    -f Dockerfile \
    --build-arg COMMIT_HASH="$(git rev-parse HEAD)" \
    .
echo "  -> ${REGISTRY}:${TAG}"

# 2. Node.js Ingestion
echo ""
echo "[2/3] Building Node.js Ingestion image..."
docker build \
    -t "${REGISTRY}-node:${TAG}" \
    -f Dockerfile.node \
    .
echo "  -> ${REGISTRY}-node:${TAG}"

# 3. Rust Services
echo ""
echo "[3/3] Building Rust services..."
RUST_SERVICES=(
    "capture"
    "capture-logs"
    "replay-capture"
    "property-defs-rs"
    "personhog-replica"
    "personhog-router"
    "feature-flags"
    "hypercache-server"
    "cyclotron-janitor"
    "cymbal"
)

for svc in "${RUST_SERVICES[@]}"; do
    echo "  Building ${svc}..."
    docker build \
        -t "${REGISTRY}/${svc}:${TAG}" \
        -f rust/Dockerfile \
        --build-arg BIN="${svc}" \
        rust/
    echo "  -> ${REGISTRY}/${svc}:${TAG}"
done

echo ""
echo "=========================================="
echo "All images built successfully!"
echo "=========================================="
echo ""
echo "Push to registry:"
echo "  docker push ${REGISTRY}:${TAG}"
echo "  docker push ${REGISTRY}-node:${TAG}"
for svc in "${RUST_SERVICES[@]}"; do
    echo "  docker push ${REGISTRY}/${svc}:${TAG}"
done
