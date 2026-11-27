FROM valkey/valkey:8.1-alpine

USER root

RUN apk add --no-cache su-exec

COPY --chmod=0755 docker/redis-ha/redis-node.sh /usr/local/bin/posthog-valkey-node
COPY --chmod=0755 docker/redis-ha/redis-sentinel.sh /usr/local/bin/posthog-valkey-sentinel
