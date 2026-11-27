#!/bin/sh
set -eu

BROKERS="${KAFKA_HOSTS:-kafka:9092}"
REPLICATION_FACTOR="${KAFKA_TOPIC_REPLICATION_FACTOR:-1}"
TOPIC_LIST_FILE="${KAFKA_TOPIC_LIST_FILE:-/topics.txt}"
TIMEOUT=60
ELAPSED=0

echo "Waiting for Kafka brokers to accept connections..."
until rpk topic list --brokers "$BROKERS" 2>/dev/null; do
    echo "Kafka brokers not ready yet (elapsed: ${ELAPSED}s)..."
    sleep 2
    ELAPSED=$((ELAPSED + 2))
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        echo "Timeout waiting for Kafka brokers after ${TIMEOUT}s"
        rpk topic list --brokers "$BROKERS" || true
        exit 1
    fi
done

echo "Kafka brokers are accepting requests, creating topics..."
if [ "$REPLICATION_FACTOR" -gt 1 ]; then
    rpk cluster config set "default_topic_replications=$REPLICATION_FACTOR" --brokers "$BROKERS"
    rpk cluster config set "minimum_topic_replications=$REPLICATION_FACTOR" --brokers "$BROKERS"
fi
TOPICS="$(grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$TOPIC_LIST_FILE") personhog_updates personhog_changelog_relay_dlq"
for topic in $TOPICS; do
    if rpk topic create "$topic" --brokers "$BROKERS" -p 1 -r "$REPLICATION_FACTOR" 2>&1; then
        echo "Topic $topic created successfully"
    elif rpk topic list --brokers "$BROKERS" | awk '{print $1}' | grep -qx "$topic"; then
        echo "Topic $topic already exists, continuing"
        if [ "$REPLICATION_FACTOR" -gt 1 ]; then
            rpk topic alter-config "$topic" --brokers "$BROKERS" --set "replication.factor=$REPLICATION_FACTOR"
        fi
    else
        echo "Failed to create topic $topic"
        exit 1
    fi
done

echo "Final topic list:"
rpk topic list --brokers "$BROKERS"
echo "Topics ready"
