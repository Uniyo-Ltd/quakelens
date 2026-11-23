from datetime import UTC, date, datetime
from math import isfinite

from django.db import transaction
from django.utils import timezone

from earthquakes.clients import usgs
from earthquakes.models import Earthquake, ImportWindow


class InvalidEarthquake(ValueError):
    """A source record cannot be used for this import."""


def finite_number(value, name):
    if type(value) not in (int, float):
        raise InvalidEarthquake(f"{name} must be a number")
    try:
        if isfinite(value):
            return value
    except OverflowError:
        pass
    raise InvalidEarthquake(f"{name} must be finite")


def utc_from_milliseconds(value: int) -> datetime:
    if type(value) is not int:
        raise InvalidEarthquake("Expected an integer timestamp in milliseconds")
    try:
        return datetime.fromtimestamp(value / 1000, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise InvalidEarthquake("Timestamp is outside the supported range") from exc


def earthquake_fields(quake, *, day=None):
    if not isinstance(quake, dict) or quake.get("type") != "Feature":
        raise InvalidEarthquake("Expected a GeoJSON feature")
    external_id = quake["id"]
    if not isinstance(external_id, str) or not external_id.strip() or len(external_id) > 128:
        raise InvalidEarthquake("Expected an earthquake ID of 1 to 128 characters")
    details = quake["properties"]
    if not isinstance(details, dict) or details.get("type") != "earthquake":
        raise InvalidEarthquake("Expected earthquake properties")
    geometry = quake["geometry"]
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise InvalidEarthquake("Expected point geometry")
    coordinates = geometry["coordinates"]
    if not isinstance(coordinates, list) or len(coordinates) not in (2, 3):
        raise InvalidEarthquake("Expected longitude and latitude")
    longitude = finite_number(coordinates[0], "longitude")
    latitude = finite_number(coordinates[1], "latitude")
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise InvalidEarthquake("Coordinates outside the valid range")
    occurred_at = utc_from_milliseconds(details["time"])
    if day is not None:
        region = usgs.REGION
        if occurred_at.date() != day:
            raise InvalidEarthquake("Earthquake occurred outside the requested UTC day")
        if not (
            region["min_latitude"] <= latitude <= region["max_latitude"]
            and region["min_longitude"] <= longitude <= region["max_longitude"]
        ):
            raise InvalidEarthquake("Earthquake outside the requested area")
    magnitude = details.get("mag")
    if magnitude is not None:
        finite_number(magnitude, "magnitude")
    place = details.get("place")
    if place is not None and not isinstance(place, str):
        raise InvalidEarthquake("Expected text for place")

    return {
        "external_id": external_id,
        "occurred_at": occurred_at,
        "source_updated_at": utc_from_milliseconds(details["updated"]),
        "magnitude": magnitude,
        "place": place,
        "longitude": longitude,
        "latitude": latitude,
    }

def save_earthquake(feature, *, day=None):
    fields = earthquake_fields(feature, day=day)
    external_id = fields.pop("external_id")

    quake, created = Earthquake.objects.get_or_create(
        external_id=external_id,
        defaults=fields,
    )
    if not created:
        Earthquake.objects.filter(
            pk=quake.pk,
            source_updated_at__lt=fields["source_updated_at"],
        ).update(**fields)


def save_batch(events, *, day=None):
    with transaction.atomic():
        for event in events:
            save_earthquake(event, day=day)


def import_day(day: date) -> int | None:
    """Return the accepted record count, or None if a newer attempt took over."""
    started = timezone.now()
    if day >= started.date():
        raise ValueError("Only complete UTC days can be imported")

    status = ImportWindow.AttemptStatus
    running = {
        "last_attempt_started_at": started,
        "last_attempt_status": status.RUNNING,
        "accepted_count": 0,
        "rejected_count": 0,
        "failure_reason": "",
    }
    window, created = ImportWindow.objects.get_or_create(
        day=day, **usgs.REGION, defaults=running
    )
    row = ImportWindow.objects.filter(pk=window.pk)
    if not created and not row.filter(last_attempt_started_at__lt=started).update(**running):
        return None

    # Only the attempt that still owns this timestamp may publish its outcome.
    attempt = row.filter(last_attempt_started_at=started)
    try:
        events = usgs.fetch_day(day)
        with transaction.atomic():
            if not attempt.select_for_update().exists():
                return None
            save_batch(events, day=day)
            attempt.update(
                last_attempt_status=status.SUCCEEDED,
                accepted_count=len(events),
                last_success_at=timezone.now(),
            )
        return len(events)
    except Exception as exc:
        # The batch transaction has rolled back. Keep any previous successful coverage.
        attempt.update(
            last_attempt_status=status.FAILED,
            rejected_count=int(isinstance(exc, (InvalidEarthquake, KeyError))),
            failure_reason=f"{type(exc).__name__}: {exc}"[:255],
        )
        raise
