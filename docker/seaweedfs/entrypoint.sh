#!/bin/sh
set -eu

weed_bin="${WEED_BIN:-/usr/bin/weed}"
weed_master="${WEED_MASTER:-127.0.0.1:9333}"
ready_file="${SEAWEEDFS_READY_FILE:-/tmp/posthog-seaweedfs-ready}"

configure_s3() {
    printf 's3.configure -access_key=%s -secret_key=%s -user=posthog -actions=Admin -apply\ns3.configure -access_key=%s -secret_key=%s -user=session-replay -actions=Admin -apply\ns3.bucket.create -name posthog\ns3.bucket.create -name ai-blobs\n' \
        "$OBJECT_STORAGE_ACCESS_KEY_ID" "$OBJECT_STORAGE_SECRET_ACCESS_KEY" \
        "$SESSION_REPLAY_ACCESS_KEY_ID" "$SESSION_REPLAY_SECRET_ACCESS_KEY" \
        | "$weed_bin" shell -master="$weed_master" >/dev/null 2>&1 || true
}

wait_for_storage() {
    while true; do
        if [ "${SEAWEEDFS_BOOTSTRAP_ENABLED:-false}" = "true" ]; then
            configure_s3
        fi
        buckets="$(printf 's3.bucket.list\n' | "$weed_bin" shell -master="$weed_master" 2>/dev/null || true)"
        identities="$(printf 's3.configure\n' | "$weed_bin" shell -master="$weed_master" 2>/dev/null || true)"
        if printf '%s' "$buckets" | grep -Fq posthog \
            && printf '%s' "$buckets" | grep -Fq ai-blobs \
            && printf '%s' "$identities" | grep -Fq "$OBJECT_STORAGE_ACCESS_KEY_ID" \
            && printf '%s' "$identities" | grep -Fq "$SESSION_REPLAY_ACCESS_KEY_ID"; then
            touch "$ready_file"
            return
        fi
        sleep 1
    done
}

: "${OBJECT_STORAGE_ACCESS_KEY_ID:?OBJECT_STORAGE_ACCESS_KEY_ID must be configured}"
: "${OBJECT_STORAGE_SECRET_ACCESS_KEY:?OBJECT_STORAGE_SECRET_ACCESS_KEY must be configured}"
: "${SESSION_REPLAY_ACCESS_KEY_ID:?SESSION_REPLAY_ACCESS_KEY_ID must be configured}"
: "${SESSION_REPLAY_SECRET_ACCESS_KEY:?SESSION_REPLAY_SECRET_ACCESS_KEY must be configured}"

wait_for_storage &
exec "$weed_bin" "$@"
