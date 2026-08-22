#!/bin/sh
set -eu

fail() {
    printf '%s\n' "ERROR $1" >&2
    exit 1
}

for required_value in \
    "${TEMPORAL_ADDRESS:-}" \
    "${DEFAULT_NAMESPACE:-}" \
    "${DEFAULT_NAMESPACE_RETENTION:-}"; do
    [ -n "$required_value" ] || fail E_CONFIGURATION
done
for numeric_value in "${MAX_ATTEMPTS:-}" "${SLEEP_SECONDS:-}"; do
    case "$numeric_value" in
        ''|*[!0-9]*) fail E_CONFIGURATION ;;
    esac
done
[ "$MAX_ATTEMPTS" -gt 0 ] || fail E_CONFIGURATION

attempt=1
while ! temporal operator cluster health \
    --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; do
    if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
        fail E_TEMPORAL_UNAVAILABLE
    fi
    attempt=$((attempt + 1))
    sleep "$SLEEP_SECONDS"
done

attempt=1
while ! temporal operator namespace describe \
    --namespace "$DEFAULT_NAMESPACE" \
    --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; do
    temporal operator namespace create \
        --namespace "$DEFAULT_NAMESPACE" \
        --retention "$DEFAULT_NAMESPACE_RETENTION" \
        --description "MedChat Temporal namespace" \
        --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1 || true
    if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
        fail E_NAMESPACE_UNAVAILABLE
    fi
    attempt=$((attempt + 1))
    sleep "$SLEEP_SECONDS"
done

if ! temporal operator namespace update \
    --namespace "$DEFAULT_NAMESPACE" \
    --retention "$DEFAULT_NAMESPACE_RETENTION" \
    --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; then
    fail E_NAMESPACE_RETENTION
fi
