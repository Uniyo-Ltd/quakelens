# QuakeLens query plans

With 100,000 synthetic earthquake rows, the list used one SQL query for both page 1 and page 100. Page 100 read eight cached blocks and took 0.036 ms in PostgreSQL. The trend checked coverage in one query and counted two weeks in a second. The count took 1.405 ms and touched 1,643 cached blocks. These are warm database execution times from one local run. They exclude HTTP, serialization and connection setup.

## Reproduce

Run this command from the repository root with Docker running.

```sh
./scripts/benchmark.sh
```

The script builds the app image, migrates a fresh PostgreSQL container, inserts deterministic fixtures and runs `ANALYZE` so the planner has current statistics. Django's test client calls the public API. For each measured request the script captures the executed SQL and parameters, then repeats each SELECT with `EXPLAIN (ANALYZE, BUFFERS)`. It prints the full plans and removes the disposable database when it exits. The database shares a loopback connection with the benchmark app and has no published ports.

The fixture grows from 1,000 to 100,000 synthetic records spread across 180 days in the supported area. Insertion order and occurrence time differ. Every seventeenth record has unknown magnitude. Fourteen synthetic daily import records allow the trend endpoint to verify coverage. Real USGS imports live in the separate application database.

## Observed results

Measured with PostgreSQL 17.12 (`postgres:17-alpine`, Linux arm64), 128 MB shared buffers and a Docker VM with two CPUs and 1.91 GiB RAM. The run used UTC midnight on November 23, 2026. Filters used minimum magnitude 2, a 30-day list range and 25 results per page. The benchmark followed the API's own next links to reach page 100.

| Query | 1,000 rows · ms | 100,000 rows · ms | Shared block hits at 100k |
| --- | ---: | ---: | ---: |
| List, first page | 0.030 | 0.043 | 11 |
| List, second page | 0.045 | 0.026 | 8 |
| List, page 100 | Too few matches | 0.036 | 8 |
| Trend, coverage check | 0.013 | 0.014 | 1 |
| Trend, weekly counts | 0.026 | 1.405 | 1,643 |

### Filtered cursor page

The list uses the immutable database ID for its cursor. Page 100 started below ID 72,256. The extra row tells the paginator whether another page exists. The following SQL uses the parameters observed in that request, with table qualifiers shortened.

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, external_id, occurred_at, source_updated_at, magnitude, latitude, longitude, place
FROM earthquakes_earthquake
WHERE latitude BETWEEN 32 AND 42 AND longitude BETWEEN -125 AND -114
  AND magnitude >= 2
  AND occurred_at >= '2026-10-24 00:00:00+00'
  AND occurred_at <  '2026-11-23 00:00:00+00'
  AND id < 72256
ORDER BY id DESC
LIMIT 26;
```

PostgreSQL reported this index scan.

```text
Limit  (cost=0.29..18.42 rows=26 width=95) (actual time=0.007..0.025 rows=26 loops=1)
  Buffers: shared hit=8
  ->  Index Scan Backward using earthquakes_earthquake_pkey on earthquakes_earthquake
        (cost=0.29..4485.16 rows=6434 width=95) (actual time=0.006..0.024 rows=26 loops=1)
        Index Cond: (id < 72256)
        Rows Removed by Filter: 152
Planning Time: 0.068 ms
Execution Time: 0.036 ms
```

PostgreSQL sought to the cursor in the primary-key index, visited 178 candidate records and stopped at the 26th match. The time, area and magnitude filters still cost work. A very sparse filter can require scanning many candidate IDs. One SELECT served the whole page, including serialization.

### Two-week trend

After checking 14 coverage records, the endpoint counts both weeks in one query. November 16 at midnight belongs to the newer week.

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(id) FILTER (WHERE occurred_at <  '2026-11-16 00:00:00+00') AS previous_count,
       COUNT(id) FILTER (WHERE occurred_at >= '2026-11-16 00:00:00+00') AS current_count
FROM earthquakes_earthquake
WHERE latitude BETWEEN 32 AND 42 AND longitude BETWEEN -125 AND -114
  AND magnitude >= 2
  AND occurred_at >= '2026-11-09 00:00:00+00'
  AND occurred_at <  '2026-11-23 00:00:00+00';
```

PostgreSQL reported this aggregate plan.

```text
Aggregate  (cost=2038.17..2038.18 rows=1 width=16) (actual time=1.390..1.390 rows=1 loops=1)
  Buffers: shared hit=1643
  ->  Bitmap Heap Scan on earthquakes_earthquake
        (cost=170.70..1996.44 rows=4173 width=16) (actual time=0.323..1.212 rows=4188 loops=1)
        Rows Removed by Filter: 3589
        Heap Blocks: exact=1613
        ->  Bitmap Index Scan on earthquakes_earthquake_occurred_at_b2a27adc
              (cost=0.00..169.65 rows=7736 width=0) (actual time=0.240..0.241 rows=7777 loops=1)
              Buffers: shared hit=30
Planning Time: 0.047 ms
Execution Time: 1.405 ms
```

The occurrence-time index identified 7,777 candidates in the 14-day interval. PostgreSQL read their table blocks and applied the other filters. The resulting 4,188 matches split into 2,094 per week. Reading the single block containing all 14 coverage records took an efficient sequential scan.

The primary key supports pagination, the unique external ID supports repeat-safe imports and detail lookups, and the occurrence-time index narrows the trend scan. Each index adds storage and write cost. Before adding another, profile actual traffic with rarer magnitude thresholds, a working set larger than memory and concurrent reads and imports. This single-client fixture measures neither cold reads nor production tail latency.
