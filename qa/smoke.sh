#!/bin/sh
# Boot a disposable copy, including its own database, broker and local source.
set -eu
cd "$(dirname "$0")/.."
project="quakelens-smoke-$(date +%s)-$$"
export QUAKELENS_PORT=0 QUAKE_REFRESH_SECONDS=8
export QUAKELENS_IMAGE="$project:local"

compose() {
    docker compose -p "$project" -f compose.yaml -f qa/compose.fixture.yaml "$@"
}

cleanup() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then compose logs --tail=35 worker beat web source >&2 || true; fi
    compose down --volumes --remove-orphans >/dev/null
    docker image rm "$QUAKELENS_IMAGE" "$QUAKELENS_IMAGE-fixture" >/dev/null 2>&1 || true
    exit "$result"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
compose up --build --detach --wait --wait-timeout 120
compose exec -T web python manage.py shell < qa/smoke_checks.py
