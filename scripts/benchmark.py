"""Measure the SQL executed by the public endpoints. Run via scripts/benchmark.sh."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import django

django.setup()

from django.core.management import call_command
from django.db import connection
from django.test import Client

from earthquakes.clients.usgs import REGION
from earthquakes.models import Earthquake, ImportWindow


def add_records(first_id, last_id, midnight):
    # Permuting occurrence times prevents insertion order from giving the list
    # query an unrealistically perfect correlation between ID and time.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO earthquakes_earthquake
                (external_id, occurred_at, source_updated_at, latitude, longitude, magnitude, place)
            SELECT 'bench-' || n, event_time, event_time + interval '4 minutes',
                   32 + mod(n, 100) / 10.0, -125 + mod(n, 110) / 10.0,
                   CASE WHEN mod(n, 17) <> 0 THEN mod(n * 11, 70) / 10.0 - 1 END,
                   'Synthetic benchmark earthquake ' || n
            FROM (
                SELECT n, %s::timestamptz - interval '180 days'
                       + mod(n * 7919, 100000) * interval '180 days' / 100000 AS event_time
                FROM generate_series(%s, %s) AS n
            ) AS fixture
            """,
            [midnight, first_id, last_id],
        )

    for offset in range(1, 15):
        start = midnight - timedelta(days=offset)
        count = Earthquake.objects.filter(
            occurred_at__gte=start, occurred_at__lt=start + timedelta(days=1)
        ).count()
        ImportWindow.objects.update_or_create(
            day=start.date(), **REGION,
            defaults={
                "last_attempt_started_at": midnight,
                "last_attempt_status": ImportWindow.AttemptStatus.SUCCEEDED,
                "last_success_at": midnight,
                "accepted_count": count,
            },
        )

    with connection.cursor() as cursor:
        cursor.execute("ANALYZE earthquakes_earthquake")
        cursor.execute("ANALYZE earthquakes_importwindow")


def profile_request(client, url, label):
    queries = []

    def capture(execute, sql, params, many, context):
        if sql.lstrip().upper().startswith("SELECT"):
            queries.append((sql, params))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture):
        response = client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"{label}: {response.status_code} {response.content!r}")

    print(f"\n{label} | {len(queries)} SELECT statements")
    with connection.cursor() as cursor:
        for sql, params in queries:
            print(sql, "\nParameters:", params)
            cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
            print("\n".join(line for (line,) in cursor.fetchall()))
    return response.json()


def next_path(page):
    link = urlsplit(page["next"])
    return f"{link.path}?{link.query}"


def main():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), version(), current_setting('shared_buffers')")
        database, version, shared_buffers = cursor.fetchone()
    if database != "quakelens_benchmark":
        raise RuntimeError("Run this script inside the disposable benchmark database.")
    print(version, "| shared_buffers:", shared_buffers)
    call_command("migrate", interactive=False, verbosity=0)
    if Earthquake.objects.exists() or ImportWindow.objects.exists():
        raise RuntimeError("The benchmark requires a fresh database.")

    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    print("UTC cutoff:", midnight.isoformat())
    client = Client(HTTP_HOST="localhost")
    filters = {
        "start": (midnight - timedelta(days=30)).isoformat(),
        "end": midnight.isoformat(),
        "min_magnitude": 2,
        "page_size": 25,
    }
    url = "/api/v1/earthquakes/?" + urlencode(filters)

    for first_id, last_id in ((1, 1_000), (1_001, 100_000)):
        add_records(first_id, last_id, midnight)
        print(f"\n=== {Earthquake.objects.count():,} synthetic earthquakes ===")
        page = profile_request(client, url, "List / first page")
        page = profile_request(client, next_path(page), "List / second page")
        if last_id == 100_000:
            for _ in range(3, 100):
                page = client.get(next_path(page)).json()
            profile_request(client, next_path(page), "List / page 100")
        trend = profile_request(client, "/api/v1/trends/?min_magnitude=2", "Trend / two weeks")
        print("Trend result:", trend)


if __name__ == "__main__":
    main()
