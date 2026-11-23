from datetime import UTC, datetime, time, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from earthquakes.clients.usgs import REGION
from earthquakes.models import Earthquake, ImportWindow


class IncompleteCoverage(Exception):
    def __init__(self, missing_days):
        self.missing_days = missing_days
        super().__init__("Some days have no successful import.")


def regional_earthquakes(min_magnitude=None):
    records = Earthquake.objects.filter(
        latitude__range=(REGION["min_latitude"], REGION["max_latitude"]),
        longitude__range=(REGION["min_longitude"], REGION["max_longitude"]),
    )
    if min_magnitude is not None:
        records = records.filter(magnitude__gte=min_magnitude)
    return records


def count_change(previous, current):
    difference = current - previous
    return {
        "difference": difference,
        "percentage_change": round(100 * difference / previous, 2) if previous else None,
        "percentage_change_reason": None if previous else "The previous period had zero records.",
    }


def weekly_trend(min_magnitude=None):
    end = datetime.combine(timezone.now().date(), time.min, tzinfo=UTC)
    midpoint = end - timedelta(days=7)
    start = midpoint - timedelta(days=7)

    windows = list(ImportWindow.objects.filter(
        **REGION, day__gte=start.date(), day__lt=end.date(), last_success_at__isnull=False,
    ).order_by("day").values("day", "last_success_at", "last_attempt_status"))
    verified_days = {row["day"] for row in windows}
    expected_days = [start.date() + timedelta(days=index) for index in range(14)]
    missing = [day for day in expected_days if day not in verified_days]
    if missing:
        raise IncompleteCoverage(missing)

    counts = regional_earthquakes(min_magnitude).filter(
        occurred_at__gte=start, occurred_at__lt=end,
    ).aggregate(
        previous_count=Count("pk", filter=Q(occurred_at__lt=midpoint)),
        current_count=Count("pk", filter=Q(occurred_at__gte=midpoint)),
    )
    checked_at = [row["last_success_at"] for row in windows]
    return {
        "previous_period": {"start": start, "end": midpoint},
        "current_period": {"start": midpoint, "end": end},
        "area": REGION,
        "min_magnitude": min_magnitude,
        **counts,
        **count_change(counts["previous_count"], counts["current_count"]),
        "freshness": {
            "oldest_success_at": min(checked_at),
            "latest_success_at": max(checked_at),
            "failed_refresh_days": [
                row["day"] for row in windows
                if row["last_attempt_status"] == ImportWindow.AttemptStatus.FAILED
            ],
        },
    }
