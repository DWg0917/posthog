FROM redis:7.2-alpine

COPY --chmod=0755 docker/redis-ha/redis-node.sh /usr/local/bin/posthog-redis-node
COPY --chmod=0755 docker/redis-ha/redis-sentinel.sh /usr/local/bin/posthog-redis-sentinel
