#!/bin/sh
set -eu

: "${TEMPORAL_ADDRESS:?required}"
: "${DEFAULT_NAMESPACE:?required}"
: "${DEFAULT_NAMESPACE_RETENTION:?required}"
: "${MAX_ATTEMPTS:?required}"
: "${SLEEP_SECONDS:?required}"

attempt=1
while ! temporal operator cluster health \
    --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; do
    if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep "$SLEEP_SECONDS"
done

attempt=1
while ! temporal operator namespace describe \
    --namespace "$DEFAULT_NAMESPACE" \
    --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; do
    if temporal operator namespace create \
        --namespace "$DEFAULT_NAMESPACE" \
        --retention "$DEFAULT_NAMESPACE_RETENTION" \
        --description "MedChat Temporal namespace" \
        --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; then
        temporal operator namespace describe \
            --namespace "$DEFAULT_NAMESPACE" \
            --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1
        exit 0
    fi
    if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep "$SLEEP_SECONDS"
done

temporal operator namespace describe \
    --namespace "$DEFAULT_NAMESPACE" \
    --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1
