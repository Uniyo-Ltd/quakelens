#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
app_image=$(docker build -q .)
database=$(docker run --rm -d --network none \
    -e POSTGRES_DB=quakelens_benchmark -e POSTGRES_USER=bench \
    -e POSTGRES_PASSWORD=local-benchmark postgres:17-alpine)
trap 'docker stop "$database" >/dev/null' EXIT
trap 'exit 1' HUP INT TERM

for attempt in $(seq 1 30); do
    if docker exec "$database" pg_isready -q -h 127.0.0.1 -U bench; then
        break
    fi
    sleep 1
done
docker exec "$database" pg_isready -q -h 127.0.0.1 -U bench
docker run --rm -i --network "container:$database" \
    -e POSTGRES_HOST=127.0.0.1 -e POSTGRES_DB=quakelens_benchmark \
    -e POSTGRES_USER=bench -e POSTGRES_PASSWORD=local-benchmark \
    -e DJANGO_SETTINGS_MODULE=config.settings "$app_image" python - < scripts/benchmark.py
