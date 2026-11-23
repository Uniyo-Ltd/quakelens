"""Run inside the smoke project's Django shell, with the normal worker and broker."""

from datetime import timedelta
from time import monotonic, sleep

import httpx
from django.utils import timezone

from earthquakes.clients.usgs import REGION
from earthquakes.models import Earthquake, ImportWindow
from earthquakes.tasks import import_earthquake_day


def wait_until(description, predicate, seconds=90):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.25)
    raise AssertionError(f"Timed out waiting for {description}")


def run():
    # The check runs in the same container as the web server.
    base = "http://127.0.0.1:8000"
    today = timezone.now().date()
    yesterday = today - timedelta(days=1)
    windows = ImportWindow.objects.filter(**REGION)
    wait_until("initial worker imports", lambda: windows.filter(last_success_at__isnull=False).count() == 30)
    assert Earthquake.objects.count() == 30
    day_window = windows.get(day=yesterday)
    wait_until("Beat to refresh a verified day", lambda: windows.filter(
        day=yesterday, last_success_at__gt=day_window.last_success_at).exists())
    print("PASS: one-command startup imported 30 days; Beat refreshed a verified day.")

    with httpx.Client(base_url=base, timeout=5) as api:
        first = api.get("/api/v1/earthquakes/", params={"min_magnitude": 2, "page_size": 2})
        first.raise_for_status()
        page = first.json()
        assert len(page["results"]) == 2 and page["next"]
        second = api.get(page["next"])
        second.raise_for_status()
        ids = {e["external_id"] for e in page["results"]}
        assert ids.isdisjoint(e["external_id"] for e in second.json()["results"])
        assert api.get("/api/v1/earthquakes/", params={"min_magnitude": 3}).json()["results"] == []
        assert api.get("/api/v1/earthquakes/?page_size=0").status_code == 400
        assert api.post("/api/v1/earthquakes/", json={}).status_code == 405
        trend = api.get("/api/v1/trends/")
        trend.raise_for_status()
        assert (trend.json()["previous_count"], trend.json()["current_count"]) == (7, 7)
        print("PASS: two pages, filters, invalid input, denied write, seven events in each week.")

        day = today - timedelta(days=20)
        external_id = f"smoke-{day}"
        detail_path = f"/api/v1/earthquakes/{external_id}/"
        previous = windows.get(day=day).last_success_at
        import_earthquake_day.delay(day.isoformat())
        wait_until("explicit queued replay", lambda: windows.filter(day=day, last_success_at__gt=previous).exists())
        assert api.get(detail_path).json()["external_id"] == external_id
        assert Earthquake.objects.count() == 30
        print("PASS: Redis delivered a replay to Celery; HTTP returned the row; count stayed 30.")

        previous = windows.get(day=day).last_success_at
        httpx.post("http://source:8080/offline", timeout=3).raise_for_status()
        import_earthquake_day.delay(day.isoformat())
        wait_until("failed refresh", lambda: windows.filter(day=day, last_attempt_status="failed").exists())
        assert windows.get(day=day).last_success_at == previous
        assert api.get(detail_path).status_code == 200
        assert api.get("/api/v1/trends/").status_code == 200
        print("PASS: upstream 503 was recorded; existing records and verified trend remained readable.")

        # Simulate one never-verified day in this disposable database while the source is offline.
        windows.filter(day=yesterday).update(last_success_at=None)
        incomplete = api.get("/api/v1/trends/")
        assert incomplete.status_code == 503
        assert incomplete.json()["code"] == "coverage_incomplete"
        assert yesterday.isoformat() in incomplete.json()["details"]["missing_days"]
        print("PASS: one missing day withheld the trend and identified the gap.")


run()
