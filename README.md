# QuakeLens

```sh
docker compose up --build
```

Run this from the repository root with Docker and Compose. The API listens on **http://127.0.0.1:8000**. Migrations and the first 30 daily imports start automatically. The list fills as days arrive. The trend becomes available once both weeks have been checked. No account, key or configuration is required.

QuakeLens answers **“Did USGS record more earthquakes in the California area this week than last week?”** Track 2 fits this question because the catalogue changes after publication. USGS provides stable event IDs and source revision times, so an importer can recognise a corrected earthquake. Counts describe recorded activity in a fixed rectangle, including offshore areas. [USGS catalogue](https://earthquake.usgs.gov/fdsnws/event/1/)

## Try the API

```sh
BASE=http://127.0.0.1:8000
curl -sS "$BASE/health/"
curl -sS "$BASE/api/v1/earthquakes/?min_magnitude=2&page_size=3"
curl -sS "$BASE/api/v1/trends/?min_magnitude=2"
```

| GET endpoint | What you get |
| --- | --- |
| `/health/` | `200` when PostgreSQL answers. This can succeed while imports are running. |
| `/api/v1/earthquakes/` | `results`, `next` and `previous`. Follow the supplied links. Default 25 rows, maximum 100. |
| `/api/v1/earthquakes/{external_id}/` | One event by its USGS ID. Copy an `external_id` from the list. An unknown ID returns `404`. |
| `/api/v1/trends/` | Counts for two adjacent seven-day periods, their difference, percentage change and freshness. A zero baseline gives a null percentage and a reason. |

Both collection endpoints accept an optional minimum magnitude. Earthquakes with unknown magnitude appear when the filter is omitted. The area is 32–42° N, 125–114° W. The list also accepts `start` (inclusive) and `end` (exclusive) as dates at UTC midnight or timestamps with an explicit offset. Its default range covers the last 30 complete UTC days. With one bound, the other sits 30 days away. The maximum span is 30 days. Cursor links preserve the time range and order by descending, immutable database ID. New arrivals and corrections can change catalogue membership between requests.

## How the import works

```text
Celery Beat → Redis queue → Celery worker → USGS
                                 ↓
                          PostgreSQL ← Django REST API
```

Beat queues missing days at startup. Every hour it revisits the latest 14 complete days for revisions and fills remaining gaps in the 30-day window. One worker fetches one day at a time with HTTPX. Reads go directly to PostgreSQL, so an upstream outage leaves previously imported data available.

- **Replay and rollback.** A unique USGS ID prevents duplicate rows. An update succeeds only when the incoming revision is newer. PostgreSQL commits the entire day's events and its success marker together. A timestamp check and row lock prevent a superseded import from recording its result.
- **Quiet days and outages.** A separate row records coverage for each day and area. A successful empty response verifies zero. A first failed import leaves coverage unknown and the trend returns `503 coverage_incomplete` with the missing dates. A failed refresh preserves prior coverage and appears in `freshness.failed_refresh_days`.
- **Bounded work.** HTTP operations use 10-second timeouts, the worker enforces a 180-second hard limit, and the client caps decoded responses at 32 MiB. Malformed records or a response reaching the 20,000-event ceiling fail the day. Temporary network, database and selected HTTP errors get up to three retries with backoff. Workers share the USGS rate-limit deadline in Redis and honour valid `Retry-After` headers. Later scheduled runs recover work that was interrupted.

The API allows anonymous reads of public data. Writes go through the worker. Invalid query parameters return `400` and unsupported HTTP methods return `405`. The web port binds to loopback. Local demo credentials can be overridden for deployment.

## Verify

```sh
docker compose exec web python manage.py test
./qa/smoke.sh
./scripts/benchmark.sh
```

Django creates a separate PostgreSQL test database. Tests cover rollback, duplicate and older records, malformed replies, retries, time boundaries, pagination and trend arithmetic. The smoke test starts a disposable stack with real Redis, Celery, PostgreSQL and a controlled USGS fixture.

| Manual check | Expected observation |
| --- | --- |
| Request health, then follow the list's next link. | Health returns 200. The next page contains different IDs. |
| Request `/api/v1/earthquakes/?page_size=0`, then POST to the list. | Invalid size returns 400. POST returns 405. |
| Request the trend during and after the first backfill. | Missing dates produce 503. Once verified, both weeks have counts and freshness timestamps. |

In a live run on 23 November 2026, 30 completed days produced 2,865 earthquake rows. Replaying 20 November left 151 matching IDs. In a separate crash check, a worker was killed during a database write. PostgreSQL preserved the previous data and success timestamp. The next scheduled refresh applied the correction once. The reproducible benchmark compares 1,000 and 100,000 synthetic rows and includes actual PostgreSQL plans. [Query plans and measurements](PERFORMANCE.md)

## Limits and production priorities

USGS can delete an event or move it outside the query region. This importer discovers revisions in its refresh window and keeps previously seen rows. Reconciliation of deletions and older revisions would be the first data-quality improvement. For days that exceed the source cap, split the day into smaller requests and publish the coverage marker only after all pieces succeed.

For deployment, replace Django's development server with a production WSGI server behind HTTPS. Provision credentials through a secret manager, restrict broker and database access, and add per-client rate limits at the gateway. Keep one active scheduler. Monitor queue age, retry counts, missing coverage and the oldest successful refresh. Alert on stale data as well as HTTP errors.

Back up PostgreSQL, rehearse restoration and verify that scheduled jobs refill coverage gaps. Before increasing worker concurrency, measure USGS load and database lock waits. Profile sparse filters, cold storage and concurrent requests against production-sized data. The current cursor bounds page size, but a rare filter can still require a long index scan.

## AI assistance

OpenAI Codex helped design the importer, implement the API, write tests, run failure checks and prepare the documentation. During review, the proposed names `save_earthquake` and `save_earthquakes` were easy to confuse. The batch operation became `save_batch` to make its transaction boundary clearer. The live import, PostgreSQL tests, crash check and query plans supplied independent checks on the generated code.
